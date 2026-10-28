"""
Read-only pre-check for scripts/sql/delete_label_studio_test_data.sql (release runbook §9
step 1): is the database exactly what that script was reviewed against?

    python scripts/label_studio_precheck.py            # reads ~/.config/senebiclabs/backup-db.url

It reads the connection string from that file (mode 600, as for backups), never from the
command line, and never prints it or the host. Everything runs in one read-only snapshot
that is rolled back. It applies the deletion script's own guards, taking the expected counts
and the reviewed foreign keys from that file so the two cannot disagree, and lists what the
deletion would remove. Exit 0 is READY, 1 is STOP, 2 is no connection.
"""
import argparse
import re
import stat
import sys
from pathlib import Path

import psycopg

DELETE_SQL = Path(__file__).resolve().parent / "sql" / "delete_label_studio_test_data.sql"
DEFAULT_DSN_FILE = Path("~/.config/senebiclabs/backup-db.url").expanduser()
DEMO_POOL = 194          # the recruiting demo pool's ls_project_id, reported on its own

# Tables the deletion removes rows from, directly or by cascade.
DELETED_FROM = ("project_submissions", "project_items", "project_clinicians", "audit_events",
                "clinical_failures", "ls_task_map", "task_answers", "answer_events", "pools",
                "pool_eligibility", "calibration_attempts", "task_completions", "task_flags",
                "review_items")


def reviewed() -> tuple[int, int, set[tuple[str, str, str, str]]]:
    """EXPECT_PROJECTS, EXPECT_POOLS and the reviewed foreign keys, read from the SQL."""
    sql = DELETE_SQL.read_text()
    projects = int(re.search(r"EXPECT_PROJECTS\s+constant int := (\d+);", sql).group(1))
    pools = int(re.search(r"EXPECT_POOLS\s+constant int := (\d+);", sql).group(1))
    fks = set(re.findall(r"\('(\w+)',\s*'(\w+)',\s*'(\w+)',\s*'([acnr])'\)", sql))
    return projects, pools, fks


class Report:
    def __init__(self, out=print):
        self.out, self.stops = out, []

    def check(self, ok: bool, what: str) -> None:
        self.out(f"  [{'ok' if ok else 'STOP'}] {what}")
        if not ok:
            self.stops.append(what)

    def rows(self, cur, title: str, sql: str, args=()) -> list[tuple]:
        rows = cur.execute(sql, args).fetchall()
        cols = [d.name for d in cur.description]
        self.out(f"\n{title} ({len(rows)})")
        for r in rows:
            self.out("  " + " | ".join(f"{c}={v}" for c, v in zip(cols, r)))
        return rows


def one(cur, sql: str, args=()):
    return cur.execute(sql, args).fetchone()[0]


def precheck(conn, out=print) -> list[str]:
    """Run every check in one read-only snapshot; return the failed checks."""
    expect_projects, expect_pools, reviewed_fks = reviewed()
    r = Report(out)
    conn.autocommit = True
    conn.execute("set statement_timeout = '30s'")
    conn.execute("set default_transaction_read_only = on")
    cur = conn.cursor()
    cur.execute("begin isolation level repeatable read read only")
    try:
        assert one(cur, "show transaction_read_only") == "on"
        if not _schema(cur, r, reviewed_fks):
            return r.stops
        _work(cur, r, expect_projects, expect_pools)
    finally:
        cur.execute("rollback")
    return r.stops


def _schema(cur, r: Report, reviewed_fks) -> bool:
    """The schema checks; False when a column the work queries need is missing."""
    out = r.out
    out("== Schema the deletion relies on")
    cols = set(cur.execute("select table_name, column_name from information_schema.columns"
                           " where table_schema = 'public'").fetchall())
    needed = (("project_submissions", "task_store"), ("project_submissions", "ls_project_id"),
              ("pools", "project_id"), ("pools", "closed_at"), ("task_answers", "review_item_id"),
              ("ls_task_map", "item_id"), ("api_keys", "key_hash"), ("operators", "id"))
    for t, c in needed:
        r.check((t, c) in cols, f"{t}.{c} exists")
    fks = set(cur.execute("""
        select ref.relname::text, rel.relname::text, a.attname::text, con.confdeltype::text
          from pg_constraint con
          join pg_class rel on rel.oid = con.conrelid
          join pg_class ref on ref.oid = con.confrelid
          join pg_namespace n on n.oid = ref.relnamespace and n.nspname = 'public'
          join pg_attribute a on a.attrelid = con.conrelid and a.attnum = con.conkey[1]
         where con.contype = 'f' and ref.relname = any(%s)""", (list(DELETED_FROM),)).fetchall())
    for fk in sorted(fks - reviewed_fks):
        r.check(False, f"unreviewed foreign key {fk[1]}.{fk[2]} -> {fk[0]} (on delete {fk[3]})")
    for fk in sorted(reviewed_fks - fks):
        r.check(False, f"reviewed foreign key missing: {fk[1]}.{fk[2]} -> {fk[0]} (on delete {fk[3]})")
    if fks == reviewed_fks:
        r.check(True, f"the {len(fks)} foreign keys into deleted tables are exactly the reviewed ones")
    trig = cur.execute("""
        select c.relname || '.' || t.tgname from pg_trigger t
          join pg_class c on c.oid = t.tgrelid
          join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
         where not t.tgisinternal and (t.tgtype & 8) <> 0 and c.relname = any(%s)""",
                       (list(DELETED_FROM),)).fetchall()
    r.check(not trig, "no delete trigger on a deleted table" + (f" (found {[t for (t,) in trig]})" if trig else ""))
    rules = cur.execute("select tablename || '.' || rulename from pg_rules"
                        " where schemaname = 'public' and tablename = any(%s)", (list(DELETED_FROM),)).fetchall()
    r.check(not rules, "no rewrite rule on a deleted table" + (f" (found {[t for (t,) in rules]})" if rules else ""))
    return all(n in cols for n in needed)


def _work(cur, r: Report, expect_projects: int, expect_pools: int) -> None:
    out = r.out
    projects = r.rows(cur, "== Projects to delete (task_store <> 'native')", """
        select s.id, s.name, s.stage, s.status, s.task_store, s.ls_project_id,
               s.created_at::date as created,
               (select count(*) from project_items i where i.project_id = s.id) as items,
               (select count(*) from project_clinicians x where x.project_id = s.id) as project_clinicians,
               (select count(*) from audit_events x where x.project_id = s.id) as audit_events,
               (select count(*) from clinical_failures x where x.project_id = s.id) as clinical_failures,
               (select count(*) from ls_task_map m join project_items i on i.id = m.item_id
                 where i.project_id = s.id) as ls_task_map,
               (select count(*) from project_items i where i.project_id = s.id
                   and i.content ? '_image_key') as items_with_images
          from project_submissions s where s.task_store <> 'native' order by s.created_at""")
    pools = r.rows(cur, "== Pools to delete (project_id is null)", """
        select p.id, p.name, p.ls_project_id, p.closed_at, p.created_at::date as created,
               (select count(*) from task_completions x where x.pool_id = p.id) as completions,
               (select count(*) from pool_eligibility x where x.pool_id = p.id) as eligible,
               (select count(*) from calibration_attempts x where x.pool_id = p.id) as calibration,
               (select count(*) from review_items x where x.pool_id = p.id) as review_items,
               (select count(*) from task_flags x where x.pool_id = p.id) as flags
          from pools p where p.project_id is null order by p.ls_project_id""")
    a = {"p": [p[0] for p in projects], "o": [p[0] for p in pools]}
    items = "select id from project_items where project_id = any(%(p)s)"
    nos = "select task_no from project_items where project_id = any(%(p)s)"

    out("\n== Checks (the deletion script's guards)")
    r.check(len(projects) == expect_projects,
            f"exactly {expect_projects} Label Studio projects (found {len(projects)})")
    r.check(len(pools) == expect_pools, f"exactly {expect_pools} Label Studio pools (found {len(pools)})")
    r.check(all(p[4] == "label_studio" for p in projects), "every listed project is task_store = 'label_studio'")

    demo = cur.execute("select id, name, project_id from pools where ls_project_id = %s", (DEMO_POOL,)).fetchall()
    out(f"  [info] pools with ls_project_id = {DEMO_POOL}: {len(demo)}, in the deletion set: "
        f"{sum(d[0] in a['o'] for d in demo)}"
        + "".join(f"\n         {d[0]} | {d[1]} | {'deleted' if d[0] in a['o'] else 'kept'}" for d in demo))

    for what, sql in (
        ("native answers on this work (by project, pool, task number or review row)",
         f"""select count(*) from task_answers where project_id = any(%(p)s) or pool_id = any(%(o)s)
               or task_no in ({nos})
               or review_item_id in (select id from review_items where pool_id = any(%(o)s))"""),
        ("answer events on this work", f"select count(*) from answer_events where project_id = any(%(p)s) or task_no in ({nos})"),
        ("pools with a project pointing at these projects", "select count(*) from pools where project_id = any(%(p)s)"),
        ("other projects' items superseding these items",
         f"select count(*) from project_items where supersedes in ({items}) and not project_id = any(%(p)s)"),
        ("other projects' audit rows pointing at these items",
         f"select count(*) from audit_events where item_id in ({items})"
         " and (project_id is null or not project_id = any(%(p)s))"),
        ("rows in other pools recording these items' task numbers",
         f"""select (select count(*) from task_completions where not pool_id = any(%(o)s) and ls_task_id in ({nos}))
                  + (select count(*) from task_flags where not pool_id = any(%(o)s) and ls_task_id in ({nos}))
                  + (select count(*) from review_items where not pool_id = any(%(o)s) and ls_task_id in ({nos}))"""),
    ):
        n = one(cur, sql, a)
        r.check(n == 0, f"no {what} (found {n})")

    shared = one(cur, f"""select count(distinct content->>'_image_key') from project_items
                           where project_id = any(%(p)s) and content ? '_image_key'
                             and content->>'_image_key' in (select content->>'_image_key' from project_items
                                                             where not project_id = any(%(p)s))""", a)
    orphans = one(cur, """select count(distinct content->>'_image_key') from project_items
                           where project_id = any(%(p)s) and content ? '_image_key'""", a) - shared
    out(f"  [info] storage: {orphans} image keys only this work uses (left in storage, recorded in the"
        f" audit rows for a separate cleanup); {shared} shared with kept items, which stay")

    out("\n== Kept (the deletion asserts these are unchanged; compare them afterwards)")
    for label, sql in (
        ("native projects", "select count(*) from project_submissions where task_store = 'native'"),
        ("items of native projects", "select count(*) from project_items where not project_id = any(%(p)s)"),
        ("pools with a project", "select count(*) from pools where project_id is not null"),
        ("task_answers", "select count(*) from task_answers"),
        ("answer_events", "select count(*) from answer_events"),
        ("clinicians", "select count(*) from clinicians"),
        ("operators", "select count(*) from operators"),
        ("api_keys", "select count(*) from api_keys"),
    ):
        out(f"  {label}: {one(cur, sql, a)}")


def read_dsn(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"No connection string: create {path} (mode 600).")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SystemExit(f"{path} is readable by others (mode {oct(mode)}); run: chmod 600 {path}")
    return path.read_text().strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn-file", type=Path, default=DEFAULT_DSN_FILE)
    args = ap.parse_args(argv)
    dsn = read_dsn(args.dsn_file)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10)
    except Exception as e:                   # never echo the connection string or the host
        print(f"could not connect ({type(e).__name__}); nothing was read")
        return 2
    with conn:
        stops = precheck(conn)
    print(f"\nSTOP: {len(stops)} check(s) failed; do not run the deletion" if stops
          else "\nREADY: the database is what the deletion script was reviewed against")
    return 1 if stops else 0


if __name__ == "__main__":
    sys.exit(main())
