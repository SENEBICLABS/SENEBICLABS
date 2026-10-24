"""
scripts/schema_check.py, the release preflight, against real databases: one shaped like
production before the release, one fully migrated, and the cases that must block a step.
"""
import psycopg
import pytest

from scripts import schema_check as S


def _report(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("set default_transaction_read_only = on")
        return S.inspect(c)


def test_a_fully_migrated_database_passes_every_gate(dsn):
    rep = _report(dsn)
    assert {s: v["state"] for s, v in rep["steps"].items()} == {s: "applied" for s in S.STEPS}
    assert rep["checks"]["baseline_missing"] == []
    assert rep["checks"]["anon_can_call_store"] is False
    for step in S.STEPS:
        assert S.gate(rep, step) == [], step


def test_before_the_release_only_the_baseline_is_present(dsn_before_native):
    rep = _report(dsn_before_native)
    # 'partial' on production, which has the two hand-added pools columns 021 declares;
    # 'absent' on a database built from the repo, which never had them.
    assert rep["steps"]["workforce_021"]["state"] in ("absent", "partial")
    for s in ("health_005", "health_006"):
        assert rep["steps"][s]["state"] == "absent"
    assert rep["checks"]["baseline_missing"] == []
    assert S.gate(rep, "workforce_021")                             # not ready: 021 not applied


def test_duplicate_current_items_block_migration_006(dsn_before_native):
    from conftest import WORKFORCE, HEALTH
    with psycopg.connect(dsn_before_native, autocommit=True) as c:
        c.execute((WORKFORCE / "migrations" / "021_pool_project.sql").read_text())
        c.execute((HEALTH / "migrations" / "005_native_task_store.sql").read_text())
        pid = c.execute("insert into project_submissions (name, email, company, description)"
                        " values ('P', 'c@x', 'C', 'd') returning id").fetchone()[0]
        c.execute("insert into project_items (project_id, idx) values (%s, 0), (%s, 0)", (pid, pid))
    rep = _report(dsn_before_native)
    reasons = S.gate(rep, "health_005")
    assert any("migration 006's unique index would fail" in r for r in reasons)
    # And 006 really would fail on it: the check describes the database, not a guess.
    with psycopg.connect(dsn_before_native, autocommit=True) as c, pytest.raises(psycopg.errors.UniqueViolation):
        c.execute((HEALTH / "migrations" / "006_item_versions.sql").read_text())


def test_the_check_cannot_write(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("set session characteristics as transaction read only")
        c.execute("set default_transaction_read_only = on")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            c.execute("insert into waitlist (email, type) values ('x@y', 'patient')")


def test_the_command_line_gates_and_reports(dsn, capsys):
    assert S.main(["--dsn", dsn, "--require", "workforce_022"]) == 0
    assert "READY: workforce_022" in capsys.readouterr().out
