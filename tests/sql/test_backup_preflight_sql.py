"""
backup_verify.py's preflight against a real (throwaway) Postgres: it reads the metadata it
needs, its connection cannot write, an unreachable source is refused within the timeout,
and --preflight-only leaves nothing behind.
"""
import time

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from conftest import DSN
from scripts import backup_verify as B


def _params(dsn):
    p = conninfo_to_dict(dsn)
    return p, p.pop("password", "")


def test_metadata_is_read_without_reading_rows(dsn):
    params, password = _params(dsn)
    conn = B.connect_source(params, password)
    try:
        meta = B.read_metadata(conn)
    finally:
        conn.close()
    assert meta.major == 15 and meta.database_bytes > 0
    names = {t[0] for t in meta.tables}
    assert {"project_items", "task_answers", "answer_events"} <= names
    assert all(total >= table for _, table, _ix, total in meta.tables)


def test_the_preflight_connection_cannot_write(dsn):
    params, password = _params(dsn)
    conn = B.connect_source(params, password)
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("insert into waitlist (email, type) values ('x@y', 'patient')")
    finally:
        conn.close()


def test_an_unreachable_source_is_refused_within_the_timeout(tmp_path):
    started = time.monotonic()
    out = B.preflight({"host": "10.255.255.1", "port": "5432", "dbname": "postgres", "user": "u"},
                      "pw", tmp_path / "out", tmp_path, 0, 0)
    assert out["ok"] is False and "could not connect" in out["refusals"][0]
    assert time.monotonic() - started < B.CONNECT_TIMEOUT_S + 5


def test_preflight_only_passes_here_and_writes_nothing(dsn, tmp_path, capsys):
    f = tmp_path / "db.url"
    f.write_text(dsn)
    f.chmod(0o600)
    out = tmp_path / "out"
    # Small margins: this asserts the mechanics, not that the test host happens to be idle.
    rc = B.main(["--dsn-file", str(f), "--out", str(out), "--preflight-only",
                 "--margin-mb", "256", "--memory-margin-mb", "64"])
    printed = capsys.readouterr().out
    assert rc == 0, printed
    assert "PREFLIGHT PASSED" in printed and not out.exists()
    password = _params(DSN)[1]
    assert password and password not in printed
