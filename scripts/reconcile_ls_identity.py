"""
Reconcile Label Studio task ids with Senebiclabs' own records, from backups, offline.

Label Studio task ids are not stable. When Label Studio was rebuilt in October 2026 every
task was re-imported under a new id, while our tables (task_completions, task_flags,
review_items, and each item's label) kept pointing at the old ones. The only key that
survived is the item's own id, which every task carries as data._item_id.

This script reads backups taken by ~/senebiclabs-backups/backup.sh and writes, without
connecting to anything:

  report.md      what maps, what is ambiguous, what exists only in Label Studio, and
                 whether re-scoring the migrated answers reproduces every stored label
  forward.sql    the migration for the projects that reconcile cleanly, as one transaction
  rollback.sql   its exact inverse

Nothing is applied. forward.sql is for an operator to read, approve and run in the
Supabase SQL editor after migrations 021 and 005, and only for projects whose report says
"clean". A row is never matched on a bare integer: a reference resolves only through a
Label Studio task whose _item_id is an item of the referencing row's own project.

    python scripts/reconcile_ls_identity.py \\
        --ls gcp-2026=~/senebiclabs-backups/2026-10-05-2104 \\
        --ls azure-2026=~/senebiclabs-backups/2026-10-16-1040 \\
        --supabase ~/senebiclabs-backups/2026-10-16-1040 \\
        --out /path/to/private/folder

The output names item and clinician ids but carries no clinical content.
"""
import argparse
import copy
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _sql(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _jsonb(v) -> str:
    return _sql(json.dumps(v, separators=(",", ":"), sort_keys=True)) + "::jsonb"


def load_ls(instances: list[tuple[str, Path]]) -> list[dict]:
    """Every task in every export: (instance, project, id, item, annotations, evidence)."""
    out = []
    for instance, folder in instances:
        for f in sorted((folder / "labelstudio").glob("project-*-tasks.json")):
            evidence = f"{folder.name}/labelstudio/{f.name} sha256:{_sha(f)}"
            for t in json.loads(f.read_text()):
                out.append({
                    "instance": instance, "ls_project_id": t.get("project"), "ls_task_id": t["id"],
                    "item_id": (t.get("data") or {}).get("_item_id"),
                    "annotations": [{"id": a["id"], "result": a.get("result") or [],
                                     "created_at": a.get("created_at"),
                                     "cancelled": bool(a.get("was_cancelled"))}
                                    for a in t.get("annotations") or []],
                    "evidence": evidence,
                })
    return out


def load_supabase(folder: Path) -> dict:
    tables = {}
    for name in ("project_submissions", "project_items", "pools", "task_completions",
                 "task_flags", "review_items", "clinicians"):
        f = folder / "supabase" / f"{name}.json"
        tables[name] = json.loads(f.read_text()) if f.exists() else []
    tables["_evidence"] = f"{folder.name}/supabase"
    return tables


def _project_of_pool(db: dict) -> dict:
    by_ls = defaultdict(list)
    for s in db["project_submissions"]:
        if s.get("ls_project_id"):
            by_ls[s["ls_project_id"]].append(s["id"])
    out = {}
    for p in db["pools"]:
        projects = by_ls.get(p.get("ls_project_id"), [])
        out[p["id"]] = projects[0] if len(projects) == 1 else None
    return out


def resolve(ls_tasks: list[dict], db: dict) -> dict:
    items = {i["id"]: i for i in db["project_items"]}
    by_id = defaultdict(list)                     # ls_task_id -> candidate tasks, any instance
    for t in ls_tasks:
        by_id[t["ls_task_id"]].append(t)

    def candidates(ls_task_id, project_id):
        """Tasks with this id whose item belongs to this project, deduplicated by item."""
        hits = {t["item_id"] for t in by_id.get(ls_task_id, [])
                if t["item_id"] in items and items[t["item_id"]]["project_id"] == project_id}
        return sorted(hits)

    pool_project = _project_of_pool(db)
    refs = {"resolved": [], "ambiguous": [], "unresolved": []}

    def place(kind, row, ls_task_id, project_id):
        if project_id is None:
            refs["unresolved"].append({"table": kind, "id": row.get("id"), "ls_task_id": ls_task_id,
                                       "why": "its pool maps to no single project"})
            return
        hits = candidates(ls_task_id, project_id)
        entry = {"table": kind, "id": row.get("id"), "ls_task_id": ls_task_id, "project_id": project_id}
        if len(hits) == 1:
            refs["resolved"].append({**entry, "item_id": hits[0]})
        elif hits:
            refs["ambiguous"].append({**entry, "items": hits})
        else:
            refs["unresolved"].append({**entry, "why": "no exported task with this id holds an item of this project"})

    for kind in ("task_completions", "task_flags", "review_items"):
        for row in db[kind]:
            place(kind, row, row.get("ls_task_id"), pool_project.get(row.get("pool_id")))
    for it in db["project_items"]:
        ref = (it.get("label") or {}).get("_ls_task_id")
        if ref is not None:
            place("project_items.label", {"id": it["id"]}, ref, it["project_id"])
            # The label is on the item itself, so the only right answer is that item.
            last = (refs["resolved"] or [{}])[-1]
            if last.get("table") == "project_items.label" and last.get("id") == it["id"] \
                    and last.get("item_id") != it["id"]:
                refs["ambiguous"].append({**refs["resolved"].pop(), "why": "resolves to a different item"})
    return refs


def ls_only_answers(ls_tasks: list[dict], db: dict) -> list[dict]:
    """Annotations no completion row records: they exist only in Label Studio. An annotation
    re-imported into a second instance is the same answer, so they are deduplicated by
    (item, result) before comparing."""
    recorded = defaultdict(list)
    for c in db["task_completions"]:
        if c.get("kind", "judgement") == "judgement":
            recorded[c.get("_item_id")].append(json.dumps(c.get("annotation_data"), sort_keys=True))
    seen, out = set(), []
    for t in ls_tasks:
        if not t["item_id"]:
            continue
        for a in t["annotations"]:
            if a["cancelled"]:
                continue
            key = (t["item_id"], json.dumps(a["result"], sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            out.append({"item_id": t["item_id"], "instance": t["instance"], "ls_task_id": t["ls_task_id"],
                        "annotation_id": a["id"], "result": a["result"], "created_at": a["created_at"],
                        "_key": key[1]})
    return out


def plan(ls_tasks, db, refs):
    items = {i["id"]: i for i in db["project_items"]}
    # Attach the item to each completion, so answers can be matched per item.
    item_of = {(r["table"], r["id"]): r["item_id"] for r in refs["resolved"]}
    for c in db["task_completions"]:
        c["_item_id"] = item_of.get(("task_completions", c["id"]))

    answers = ls_only_answers(ls_tasks, db)
    by_item_completions = defaultdict(list)
    for c in db["task_completions"]:
        if c.get("kind", "judgement") == "judgement" and c["_item_id"]:
            by_item_completions[c["_item_id"]].append(c)

    # Per project: clean only if nothing about it is ambiguous or unresolved.
    blocked = defaultdict(list)
    for r in refs["ambiguous"] + refs["unresolved"]:
        blocked[r.get("project_id")].append(r)
    projects = {}
    for s in db["project_submissions"]:
        pid = s["id"]
        mine = [i for i in db["project_items"] if i["project_id"] == pid]
        if not s.get("ls_project_id") and not mine:
            continue
        pools = [p for p in db["pools"] if p.get("ls_project_id") and p["ls_project_id"] == s.get("ls_project_id")]
        reasons = [f"{len(blocked[pid])} reference(s) ambiguous or unresolved"] if blocked[pid] else []
        # The project's tasks in the Label Studio it points at now (the last instance given).
        current = [t for t in ls_tasks if t["instance"] == ls_tasks[-1]["instance"]
                   and t["ls_project_id"] == s.get("ls_project_id")] if ls_tasks else []
        foreign = [t for t in current if not t["item_id"] or t["item_id"] not in {i["id"] for i in mine}]
        if foreign:
            reasons.append(f"Label Studio holds {len(foreign)} task(s) in its project that are not this "
                           "project's items; moving it would leave them behind")
        if len(pools) > 1:
            reasons.append(f"{len(pools)} pools share its Label Studio project; one per native project is allowed")
        projects[pid] = {"company": s.get("company"), "ls_project_id": s.get("ls_project_id"),
                         "items": len(mine), "pools": [p["id"] for p in pools], "blocked": reasons}

    # Which answers each item will hold on the native store: the completion-backed ones
    # (with their clinician), plus any annotation no completion records.
    native_answers = defaultdict(list)
    for item_id, comps in by_item_completions.items():
        for c in comps:
            native_answers[item_id].append({"clinician_id": c["clinician_id"], "pool_id": c["pool_id"],
                                            "result": c["annotation_data"], "source": f"completion:{c['id']}",
                                            "created_at": c.get("completed_at")})
    remaining = defaultdict(list)
    for c in db["task_completions"]:
        if c["_item_id"]:
            remaining[c["_item_id"]].append(json.dumps(c.get("annotation_data"), sort_keys=True))
    for a in answers:
        pool = remaining[a["item_id"]]
        if a["_key"] in pool:
            pool.remove(a["_key"])                # recorded by a completion already
            continue
        if a["item_id"] in items:
            native_answers[a["item_id"]].append({"clinician_id": None, "pool_id": None, "result": a["result"],
                                                 "source": f"ls:{a['instance']}:{a['annotation_id']}",
                                                 "created_at": a["created_at"]})
    # In the order the answers were given. Order is part of how a label is scored: a 1-1
    # tie on a structured finding goes to the first answer, and free-text versions are
    # listed first to last. Label Studio returned annotations in creation order and the
    # native store reads them by insertion, so the migrated answers must be in that order.
    for item_id in native_answers:
        native_answers[item_id].sort(key=lambda a: _when(a["created_at"]))
    return projects, native_answers, answers


def _when(stamp):
    """A comparable instant from either store's timestamp format (naive means UTC)."""
    from datetime import datetime, timezone
    import re
    if not stamp:
        return datetime.max.replace(tzinfo=timezone.utc)
    # Postgres trims trailing zeros from fractional seconds ("36.95209"), which Python
    # 3.10 will not parse; pad the fraction to microseconds.
    s = re.sub(r"\.(\d{1,6})", lambda m: "." + m.group(1).ljust(6, "0"),
               str(stamp).replace("Z", "+00:00"), count=1)
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def verify(db, native_answers, projects):
    """Re-score every item of a clean project from the answers it would hold, with HEALTH's
    own scoring path, and compare with the label stored today. The migration gate."""
    from _fakedb import FakeDB
    from app.api.v1 import ls as ls_api

    diffs = []

    def strip(label):
        drop = {"_ls_task_id", "_task_no", "_result"}
        out = {k: v for k, v in (label or {}).items() if k not in drop}
        if "_annotations" in out:
            out["_annotations"] = sorted(
                (json.dumps({k: v for k, v in a.items() if k not in ("by", "at")}, sort_keys=True)
                 for a in out["_annotations"]))
        return out

    for it in db["project_items"]:
        # Checked for every project, blocked or not: a re-scoring difference is evidence
        # worth having before anyone unblocks it.
        p = projects.get(it["project_id"])
        if not p or not (it.get("label") or {}):
            continue
        if (it["label"] or {}).get("_adjudicated"):
            continue                              # a senior's decision is final either way
        sub = next(s for s in db["project_submissions"] if s["id"] == it["project_id"])
        ec = sub.get("eval_config") or {}
        fake = FakeDB({"project_submissions": [copy.deepcopy(sub)],
                       "project_items": [{**copy.deepcopy(it), "label": None}],
                       "pools": [], "review_items": [], "audit_events": []})
        task = {"id": 0, "_native": True, "data": {"_item_id": it["id"]},
                "annotations": [{"id": n, "result": a["result"], "created_at": a["created_at"],
                                 "completed_by": {"email": a["clinician_id"] or "label-studio"}}
                                for n, a in enumerate(native_answers.get(it["id"], []))]}
        t, adj, txt, prim = ls_api._qa_from_ec(ec)
        with patch("app.services.audit.record"):
            status = ls_api._apply_task_annotations(fake, task, t, it["project_id"], adj, txt, prim, ec)
        got = fake.rows["project_items"][0]
        if status is None or strip(got["label"]) != strip(it["label"]) or got["status"] != it["status"]:
            diffs.append({"item_id": it["id"], "stored_status": it["status"], "rebuilt_status": got["status"],
                          "answers": len(native_answers.get(it["id"], []))})
    return diffs


def write_sql(out: Path, db, ls_tasks, refs, projects, native_answers):
    clean = {pid for pid, p in projects.items() if not p["blocked"]}
    items = {i["id"]: i for i in db["project_items"]}
    task_no = lambda item_id: f"(select task_no from project_items where id = {_sql(item_id)})"  # noqa: E731
    fwd, back = ["begin;"], ["begin;"]

    for t in ls_tasks:
        it = items.get(t["item_id"])
        if it and it["project_id"] in clean:
            fwd.append("insert into ls_task_map (item_id, ls_instance, ls_project_id, ls_task_id, evidence) values "
                       f"({_sql(t['item_id'])}, {_sql(t['instance'])}, {_sql(t['ls_project_id'])}, "
                       f"{_sql(t['ls_task_id'])}, {_sql(t['evidence'])}) on conflict do nothing;")
    for pid in sorted(clean):
        back.append(f"delete from ls_task_map where item_id in (select id from project_items where project_id = {_sql(pid)});")

    for r in refs["resolved"]:
        if r["project_id"] not in clean:
            continue
        if r["table"] == "project_items.label":
            fwd.append(f"update project_items set label = (label - '_ls_task_id') || jsonb_build_object("
                       f"'_task_no', task_no, '_legacy_ls_task_id', {r['ls_task_id']}) "
                       f"where id = {_sql(r['id'])} and (label->>'_ls_task_id')::int = {r['ls_task_id']};")
            back.append(f"update project_items set label = (label - '_task_no' - '_legacy_ls_task_id') || "
                        f"jsonb_build_object('_ls_task_id', {r['ls_task_id']}) where id = {_sql(r['id'])};")
        else:
            fwd.append(f"update {r['table']} set ls_task_id = {task_no(r['item_id'])} "
                       f"where id = {_sql(r['id'])} and ls_task_id = {r['ls_task_id']};")
            back.append(f"update {r['table']} set ls_task_id = {r['ls_task_id']} where id = {_sql(r['id'])};")

    for item_id, answers in sorted(native_answers.items()):
        it = items.get(item_id)
        if not it or it["project_id"] not in clean:
            continue
        # Inserted in the order given (see plan), since the store reads answers by id.
        for a in answers:
            fwd.append("insert into task_answers (task_no, project_id, pool_id, clinician_id, kind, result, answers, created_at) "
                       f"values ({task_no(item_id)}, {_sql(it['project_id'])}, {_sql(a['pool_id'])}, "
                       f"{_sql(a['clinician_id'])}, 'judgement', {_jsonb(a['result'])}, "
                       f"{_jsonb({'_migrated_from': a['source']})}, coalesce({_sql(a['created_at'])}::timestamptz, now()));")
    for pid in sorted(clean):
        back.append(f"delete from task_answers where project_id = {_sql(pid)} and answers ? '_migrated_from';")

    for pid in sorted(clean):
        p = projects[pid]
        fwd.append(f"update project_submissions set task_store = 'native' where id = {_sql(pid)};")
        back.append(f"update project_submissions set task_store = 'label_studio' where id = {_sql(pid)};")
        for pool in p["pools"]:
            fwd.append(f"update pools set project_id = {_sql(pid)} where id = {_sql(pool)};")
            back.append(f"update pools set project_id = null where id = {_sql(pool)};")

    fwd.append("commit;")
    back.append("commit;")
    (out / "forward.sql").write_text("\n".join(fwd) + "\n")
    (out / "rollback.sql").write_text("\n".join(back) + "\n")
    return clean


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ls", action="append", required=True, metavar="INSTANCE=BACKUP_DIR",
                    help="a backup holding a Label Studio export, and which instance it came from")
    ap.add_argument("--supabase", required=True, metavar="BACKUP_DIR", help="the backup whose Supabase tables to reconcile")
    ap.add_argument("--out", required=True, help="a private folder for the report and the SQL")
    args = ap.parse_args(argv)

    instances = []
    for spec in args.ls:
        name, _, path = spec.partition("=")
        instances.append((name, Path(path).expanduser()))
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    ls_tasks = load_ls(instances)
    db = load_supabase(Path(args.supabase).expanduser())
    refs = resolve(ls_tasks, db)
    projects, native_answers, ls_answers = plan(ls_tasks, db, refs)
    diffs = verify(db, native_answers, projects)
    for d in diffs:
        item = next(i for i in db["project_items"] if i["id"] == d["item_id"])
        projects[item["project_id"]]["blocked"].append(f"item {d['item_id']} does not re-score to its stored label")
    clean = write_sql(out, db, ls_tasks, refs, projects, native_answers)

    items = {i["id"] for i in db["project_items"]}
    no_item = [t for t in ls_tasks if not t["item_id"]]
    orphans = [t for t in ls_tasks if t["item_id"] and t["item_id"] not in items]
    lines = [
        "# Label Studio identity reconciliation",
        "",
        "Generated offline from backups; nothing was read from or written to a live system.",
        "",
        "## Sources",
        *[f"- Label Studio `{n}`: `{p}`" for n, p in instances],
        f"- Supabase: `{db['_evidence']}`",
        "",
        "## Label Studio tasks",
        f"- tasks read: {len(ls_tasks)} across {len({(t['instance'], t['ls_project_id']) for t in ls_tasks})} projects",
        f"- carrying an item id that exists in Supabase: {len([t for t in ls_tasks if t['item_id'] in items])}",
        f"- carrying no item id (not created by HEALTH, e.g. a demo project): {len(no_item)}"
        + (f" in projects {sorted({(t['instance'], t['ls_project_id']) for t in no_item})}" if no_item else ""),
        f"- carrying an item id Supabase does not have (data only in Label Studio): {len(orphans)}",
        "",
        "## References in our tables",
        f"- resolved through `_item_id`: {len(refs['resolved'])}",
        f"- ambiguous (more than one item could match): {len(refs['ambiguous'])}",
        f"- unresolved: {len(refs['unresolved'])}",
        *[f"  - {r['table']} {r['id']}: Label Studio task {r['ls_task_id']}: {r.get('why') or r.get('items')}"
          for r in refs["ambiguous"] + refs["unresolved"]],
        "",
        "## Answers",
        f"- distinct Label Studio annotations (re-imports counted once): {len(ls_answers)}",
        f"- answers the native store would hold: {sum(len(v) for v in native_answers.values())}, of which "
        f"{sum(1 for v in native_answers.values() for a in v if a['clinician_id'] is None)} exist only in Label Studio "
        "(imported without a clinician, marked `_migrated_from`)",
        "",
        "## Re-scoring check",
        "Every labelled item of every project (a senior's settled decision excepted, being final) was "
        "re-scored from the answers it would hold, in the order they were given, with HEALTH's own "
        "scoring code, and compared with its stored label (ignoring only the task reference and "
        "who/when on each reading). A difference blocks the project.",
        f"- items that differ: {len(diffs)}",
        *[f"  - {d}" for d in diffs],
        "",
        "## Projects",
        *[f"- `{pid}` ({p['company']}, Label Studio project {p['ls_project_id']}, {p['items']} items): "
          + ("**clean**, included in forward.sql" if pid in clean else "**not migrated**: " + "; ".join(p["blocked"]))
          for pid, p in projects.items()],
        "",
        "## To apply (only after approval)",
        "1. Apply workforce `migrations/021_pool_project.sql`, then HEALTH `migrations/005_native_task_store.sql`.",
        "2. Take a fresh backup (`backup.sh`).",
        "3. Read `forward.sql`, then run it in the Supabase SQL editor. It is one transaction.",
        "4. `POST /project/admin/rebuild` per project, then compare `/project/admin/export` with the backup.",
        "5. To undo, run `rollback.sql` (also one transaction).",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print((out / "report.md").read_text())
    return 0 if not diffs else 1


if __name__ == "__main__":
    raise SystemExit(main())
