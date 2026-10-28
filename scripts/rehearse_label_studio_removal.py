"""
Rehearse the Label Studio removal on a restored copy of production, never on production.

    python scripts/rehearse_label_studio_removal.py --dump ~/senebiclabs-backups/<date>/db/db-<stamp>.dump

The dump must be a verified backup (its db-<stamp>.json says "verified": true). It is
restored into a throwaway Postgres of the same major version (backup_verify.RestoreContainer:
never pulled, bound to 127.0.0.1, data in RAM, removed however the run ends), then:

  removal    the pre-check is READY; the deletion removes exactly the Label Studio work;
             every other row of every table is unchanged; the audit rows are written; the
             pre-check then finds nothing; migration 008 applies, twice, and
             schema_check reports health_008 applied with an empty gate
  rollback   a write failing part way through the deletion leaves every table as it was
  refusal    an unreviewed rewrite rule makes the deletion refuse, changing nothing

Each runs in its own copy of the restored database. It prints table names and counts, never
row contents. Exit 0 when every check passes.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import backup_verify as B                       # noqa: E402
from scripts import label_studio_precheck as P               # noqa: E402
from scripts import schema_check as S                        # noqa: E402

HEALTH = Path(__file__).resolve().parent.parent
DELETE = (HEALTH / "scripts" / "sql" / "delete_label_studio_test_data.sql").read_text()
M008 = (HEALTH / "migrations" / "008_remove_label_studio.sql").read_text()
ACTOR = "Label Studio removal (reviewed SQL)"
MiB = 1024 * 1024
# Backups are dumped with --no-privileges, so a restore leaves every function executable by
# PUBLIC. Production revokes that in the migrations; re-applying exactly those statements
# makes the copy match it. (The live schema_check reads production's real privileges.)
REVOKES = "\n".join(line for m in sorted((HEALTH / "migrations").glob("00*.sql"))
                     for line in m.read_text().splitlines() if line.startswith("revoke all on function"))


def all_rows(c) -> dict[str, list[str]]:
    tables = [t for (t,) in c.execute("select tablename from pg_tables where schemaname = 'public' order by 1")]
    return {t: sorted(r for (r,) in c.execute(f'select to_jsonb(t)::text from public."{t}" t')) for t in tables}


def scope(c) -> set[str]:
    """Ids of everything the deletion is meant to remove, read before it runs."""
    ids = c.execute("""
        select id::text from project_submissions where task_store <> 'native'
        union all select id::text from pools where project_id is null
        union all select i.id::text from project_items i
                    join project_submissions s on s.id = i.project_id where s.task_store <> 'native'""").fetchall()
    return {i for (i,) in ids}


def _in_scope(row: str, ids: set[str]) -> bool:
    return any(f'"{i}"' in row for i in ids)


class Checks:
    def __init__(self, out=print):
        self.out, self.failed = out, []

    def __call__(self, ok: bool, what: str) -> None:
        self.out(f"  [{'ok' if ok else 'FAIL'}] {what}")
        if not ok:
            self.failed.append(what)


def rehearse_removal(dsn: str, check: Checks) -> None:
    out = check.out
    with psycopg.connect(dsn) as c:
        stops = P.precheck(c, out=lambda *_: None)
    check(stops == [], "pre-check READY" + (f" (stops: {stops})" if stops else ""))
    if stops:
        return
    with psycopg.connect(dsn, autocommit=True) as c:
        ids = scope(c)
        before = all_rows(c)
        projects = c.execute("select count(*) from project_submissions where task_store <> 'native'").fetchone()[0]
        pools = c.execute("select count(*) from pools where project_id is null").fetchone()[0]
        out(f"  scope: {projects} projects, {pools} pools, {len(ids) - projects - pools} items")
        c.execute(DELETE)
        after = all_rows(c)
        audit = dict(c.execute("select action, count(*) from audit_events where actor_name = %s group by action",
                               (ACTOR,)).fetchall())
    removed = {t: len(before[t]) - len([r for r in after[t] if ACTOR not in r]) for t in before}
    out("  removed: " + ", ".join(f"{t} {n}" for t, n in removed.items() if n))
    for t in before:
        kept_before = [r for r in before[t] if not _in_scope(r, ids)]
        kept_after = [r for r in after[t] if ACTOR not in r]
        check(kept_after == kept_before, f"{t}: every row outside the Label Studio work unchanged ({len(kept_after)})")
        check(not any(_in_scope(r, ids) for r in after[t] if ACTOR not in r), f"{t}: nothing of the Label Studio work left")
    check(audit == {"label_studio_project_deleted": projects, "label_studio_pool_deleted": pools},
          f"audit rows: {audit}")
    with psycopg.connect(dsn) as c:
        stops = P.precheck(c, out=lambda *_: None)
    check(any("found 0" in s for s in stops), "pre-check afterwards finds no Label Studio work")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(M008)
        c.execute(M008)
        rep = S.inspect(c)
        c.execute("begin")
        store = c.execute("insert into project_submissions (name, email, company, description)"
                          " values ('r', 'r', 'r', 'r') returning task_store").fetchone()[0]
        c.execute("rollback")                      # leaves the rehearsal copy as 008 left it
    check(store == "native", "a new project is created on the task store")
    check(rep["steps"]["health_008"]["state"] == "applied", "schema_check: health_008 applied")
    reasons = S.gate(rep, "health_008")
    check(reasons == [], "schema_check: gate for health_008 is clear" + (f" ({reasons})" if reasons else ""))


def rehearse_rollback(dsn: str, check: Checks) -> None:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("alter table audit_events add constraint rehearsal_fail"
                  " check (action <> 'label_studio_pool_deleted') not valid")
        before = all_rows(c)
        try:
            c.execute(DELETE)
            failed = None
        except psycopg.Error as e:
            failed = type(e).__name__
            c.execute("rollback")
        check(failed == "CheckViolation", f"a failure part way through stops the deletion ({failed})")
        check(all_rows(c) == before, "every table is exactly as before")


def rehearse_refusal(dsn: str, check: Checks) -> None:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("create rule rehearsal as on delete to pools do also update operators set name = name")
        before = all_rows(c)
        try:
            c.execute(DELETE)
            msg = ""
        except psycopg.errors.RaiseException as e:
            msg = str(e)
            c.execute("rollback")
        check("rewrite rules would run" in msg, "an unreviewed rewrite rule makes it refuse")
        check(all_rows(c) == before, "every table is exactly as before")


REHEARSALS = {"removal": rehearse_removal, "rollback": rehearse_rollback, "refusal": rehearse_refusal}


def _clone(admin: dict, template: str, name: str) -> str:
    with psycopg.connect(**{**admin, "dbname": "template1"}, autocommit=True) as c:
        c.execute(f'create database "{name}" template "{template}"')
    return psycopg.conninfo.make_conninfo(**{**admin, "dbname": name})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", type=Path, required=True)
    args = ap.parse_args(argv)
    report = json.loads(args.dump.with_suffix(".json").read_text())
    if report.get("status") != "verified" or report.get("dump") != args.dump.name:
        print(f"{args.dump.name} is not a verified backup; refusing")
        return 2
    mem = report["preflight"]["memory"]
    image = report["preflight"]["restore_image"]
    # Room for the restore plus three copies of a small database.
    data_cap = max(mem["restore_data_cap_bytes"], 256 * MiB) + 128 * MiB
    check = Checks()
    with B.RestoreContainer(image, data_cap, mem["restore_memory_cap_bytes"]) as rc:
        rc.wait_ready()
        with open(args.dump, "rb") as f:
            proc = subprocess.run(["docker", "exec", "-i", rc.name, "pg_restore", "--no-owner", "--no-privileges",
                                   "--exit-on-error", "--dbname=postgres", "--username=postgres"],
                                  stdin=f, capture_output=True)
        if proc.returncode != 0:
            print("pg_restore failed: " + proc.stderr.decode()[-500:])
            return 2
        admin = rc.target()
        with psycopg.connect(**admin, autocommit=True) as c:
            c.execute(REVOKES)
        print(f"restored {args.dump.name} into a throwaway {image}, and re-applied the migrations'"
              " function revokes (the dump carries no privileges)")
        for name, rehearse in REHEARSALS.items():
            print(f"\n== {name}")
            rehearse(_clone(admin, "postgres", f"rehearse_{name}"), check)
    if rc.cleanup_error:
        check(False, f"remove the throwaway container: {rc.cleanup_error}")
    print("\nREHEARSED: every check passed" if not check.failed
          else f"\nFAILED: {len(check.failed)} check(s)")
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
