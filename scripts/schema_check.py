"""
Release preflight: which migrations a database has, and whether the next one is safe.

Read-only by construction: the session is set read-only before any query, so this cannot
write even if pointed at production by mistake. It prints names and counts, never row
contents.

    python scripts/schema_check.py --dsn "$DATABASE_URL"                    # report
    python scripts/schema_check.py --dsn "$DATABASE_URL" --require health_006  # gate a step

Steps, in release order (see docs/release-runbook.md):
    workforce_021   pools.project_id (and the hand-added pools columns, declared)
    health_005      the native task store
    health_006      versioned items
    health_007      api_keys declared
    workforce_022   agreement_acceptances and hashed invite tokens declared
    health_008      Label Studio removed: task_store native-only, ls_task_map and
                    project_submissions.ls_project_id dropped (a removal: "applied" means gone)

--require STEP exits 1 unless STEP and every step before it are fully present, and every
integrity check that would make the NEXT step fail is clear.
"""
import argparse
import json
import sys

import psycopg

STEPS = ["workforce_021", "health_005", "health_006", "health_007", "workforce_022", "health_008"]

COLUMNS = {
    "workforce_021": {"pools": ["project_id", "eval_config", "maximum_annotations"]},
    "health_005": {"project_items": ["task_no"], "project_submissions": ["task_store"],
                   "task_answers": ["id", "task_no", "result", "answers", "clinician_id", "withdrawn_at"],
                   "answer_events": ["id", "task_no", "status", "attempts", "locked_at"]},
    "health_006": {"project_items": ["content_hash", "version", "supersedes", "superseded_at"]},
    "health_007": {"api_keys": ["email", "key_hash", "last4", "label", "revoked", "last_used_at"]},
    "workforce_022": {"agreement_acceptances": ["clinician_id", "version", "accepted_at", "ip"],
                      "invites": ["token_hash"]},
}
FUNCTIONS = {
    "health_005": ["native_ceiling", "native_task_for_pool", "native_submit_judgement", "native_approve_review",
                   "native_task_states", "claim_answer_events", "finish_answer_event", "request_rebuild",
                   "retry_failed_events"],
    "health_006": ["ingest_item_version"],
}
INDEXES = {
    "workforce_021": ["pools_one_per_project"],
    "health_005": ["project_items_task_no_key", "answer_events_one_processing_per_task"],
    "health_006": ["project_items_one_current_per_idx"],
    "workforce_022": ["invites_token_hash_key"],
}
# What a step takes away. 005 also created ls_task_map; 008 drops it again.
REMOVED = {
    "health_008": {"project_submissions": ["ls_project_id"], "ls_task_map": ["item_id"]},
}
# What the applications read, before and after every step: the baseline a release starts from.
BASELINE = {"project_submissions": ["id", "eval_config", "stage"],
            "project_items": ["id", "project_id", "idx", "content", "label", "status"],
            "pools": ["id", "ls_project_id"], "pool_eligibility": ["clinician_id", "pool_id", "eligible", "senior"],
            "task_completions": ["clinician_id", "pool_id", "ls_task_id", "kind"],
            "review_items": ["id", "pool_id", "ls_task_id", "state"], "task_flags": ["ls_task_id"],
            "clinicians": ["id", "email"], "audit_events": ["item_id", "action"], "operators": ["key_hash"]}


def _one(c, sql, *args):
    # No parameters means no placeholders: a literal % (as in LIKE 'pgrst%') stays literal.
    return (c.execute(sql, args) if args else c.execute(sql)).fetchone()[0]


def inspect(c) -> dict:
    cols = {}
    for t, col in c.execute("select table_name, column_name from information_schema.columns "
                            "where table_schema = 'public'").fetchall():
        cols.setdefault(t, set()).add(col)
    funcs = {r[0] for r in c.execute("select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                                     "where n.nspname = 'public'").fetchall()}
    idx = {r[0] for r in c.execute("select indexname from pg_indexes where schemaname = 'public'").fetchall()}

    def missing(spec):
        return [f"{t}.{x}" for t, xs in spec.items() for x in xs if x not in cols.get(t, set())]

    def present(spec):
        return [f"{t}.{x} still present" for t, xs in spec.items() for x in xs if x in cols.get(t, set())]

    task_store_default = _one(c, "select column_default from information_schema.columns where table_schema = 'public' "
                                 "and table_name = 'project_submissions' and column_name = 'task_store'") \
        if "task_store" in cols.get("project_submissions", set()) else None

    steps = {}
    for s in STEPS:
        gaps = missing(COLUMNS.get(s, {})) + [f"function {f}" for f in FUNCTIONS.get(s, []) if f not in funcs] \
            + [f"index {i}" for i in INDEXES.get(s, []) if i not in idx] + present(REMOVED.get(s, {}))
        expected = sum(len(v) for v in COLUMNS.get(s, {}).values()) + len(FUNCTIONS.get(s, [])) + len(INDEXES.get(s, [])) \
            + sum(len(v) for v in REMOVED.get(s, {}).values())
        if s == "health_008":
            expected += 1
            if "'native'" not in str(task_store_default or ""):
                gaps.append("project_submissions.task_store does not default to 'native'")
        steps[s] = {"state": "applied" if not gaps else "absent" if len(gaps) == expected else "partial",
                    "missing": gaps}

    checks = {"baseline_missing": missing(BASELINE)}
    checks["server_version"] = _one(c, "show server_version")
    checks["project_items_rows"] = _one(c, "select count(*) from project_items")
    status_clause = "where status <> 'superseded'" if "superseded_at" in cols.get("project_items", set()) else ""
    checks["duplicate_current_idx"] = _one(c, f"select count(*) from (select project_id, idx from project_items "
                                              f"{status_clause} group by 1, 2 having count(*) > 1) d")
    checks["pools_holding_webhook_secret"] = _one(c, "select count(*) from pools where eval_config ? '_webhook_secret'") \
        if "eval_config" in cols.get("pools", set()) else None
    checks["postgrest_reload_trigger"] = bool(_one(c, "select count(*) from pg_event_trigger where evtname like 'pgrst%'"))
    if steps["health_005"]["state"] == "applied" and steps["workforce_021"]["state"] == "applied":
        checks["native_projects"] = _one(c, "select count(*) from project_submissions where task_store = 'native'")
        checks["native_projects_without_pool"] = _one(
            c, "select count(*) from project_submissions s where s.task_store = 'native' "
               "and not exists (select 1 from pools p where p.project_id = s.id)")
        checks["pools_linked_to_label_studio_projects"] = _one(
            c, "select count(*) from pools p join project_submissions s on s.id = p.project_id "
               "where s.task_store <> 'native'")
        checks["scoring_events_failed"] = _one(c, "select count(*) from answer_events where status = 'failed'")
        checks["label_studio_projects"] = _one(
            c, "select count(*) from project_submissions where task_store <> 'native'")
        checks["label_studio_pools"] = _one(c, "select count(*) from pools where project_id is null")
        checks["anon_can_call_store"] = bool(_one(
            c, "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
               "where n.nspname = 'public' and p.proname in ('native_submit_judgement', 'claim_answer_events') "
               "and has_function_privilege('anon', p.oid, 'execute')"))
    return {"steps": steps, "checks": checks}


def gate(report: dict, require: str) -> list[str]:
    """Reasons the database is NOT ready for `require` and the step after it."""
    reasons = []
    if report["checks"]["baseline_missing"]:
        reasons.append(f"baseline columns missing: {report['checks']['baseline_missing']}")
    for s in STEPS[:STEPS.index(require) + 1]:
        if report["steps"][s]["state"] != "applied":
            reasons.append(f"{s} is {report['steps'][s]['state']}: missing {report['steps'][s]['missing']}")
    nxt = STEPS[STEPS.index(require) + 1] if require != STEPS[-1] else None
    if nxt == "health_006" and report["checks"]["duplicate_current_idx"]:
        reasons.append(f"{report['checks']['duplicate_current_idx']} (project, idx) pairs have more than one item; "
                       "migration 006's unique index would fail. Resolve them first")
    if nxt == "health_008":
        left = (report["checks"].get("label_studio_projects") or 0) + (report["checks"].get("label_studio_pools") or 0)
        if left:
            reasons.append(f"{left} Label Studio projects or pools remain; migration 008 would refuse. "
                           "Run scripts/sql/delete_label_studio_test_data.sql first")
    if report["checks"].get("anon_can_call_store"):
        reasons.append("the anon role can execute native store functions")
    return reasons


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--require", choices=STEPS)
    args = ap.parse_args(argv)
    with psycopg.connect(args.dsn, autocommit=True) as c:
        c.execute("set session characteristics as transaction read only")
        c.execute("set default_transaction_read_only = on")
        report = inspect(c)
    print(json.dumps(report, indent=2, default=str))
    if args.require:
        reasons = gate(report, args.require)
        for r in reasons:
            print(f"NOT READY: {r}")
        if reasons:
            return 1
        print(f"READY: {args.require} is in place and the next step's preconditions hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
