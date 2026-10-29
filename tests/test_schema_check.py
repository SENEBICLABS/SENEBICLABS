"""
Where the release preflight gets its connection string.

The checks themselves are proved against real Postgres in tests/sql/test_schema_check_sql.py.
This is only about which database it talks to, which is the part that can go wrong quietly.
"""
import argparse

import pytest

from scripts import schema_check as SC

# ── where the connection string comes from ──────────────────────────────────────

def test_an_empty_connection_string_is_refused_rather_than_sent_to_the_local_database(
    monkeypatch, capsys
):
    """The footgun this exists to stop.

    `psycopg.connect("")` is not an error: libpq reads it as "use the defaults", which
    means the local socket. A blank `$DB` in the runbook's command therefore reported on
    whatever database happened to be running on the release engineer's laptop, and a gate
    that passes against the wrong database is worse than one that fails.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert SC.main(["--dsn", ""]) == 2
    assert SC.main([]) == 2
    err = capsys.readouterr().err
    assert "no connection string" in err
    assert "local database" in err


def test_the_environment_is_used_when_no_argument_is_given(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://from-the-environment/db")
    args = argparse.Namespace(dsn=None, dsn_file=None)
    assert SC._resolve_dsn(args) == "postgresql://from-the-environment/db"


def test_a_dsn_file_others_can_read_is_refused(tmp_path, monkeypatch):
    """A connection string holds the password, so the file has to be the owner's alone."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    f = tmp_path / "dsn"
    f.write_text("postgresql://user:pw@host/db")
    f.chmod(0o644)
    args = argparse.Namespace(dsn=None, dsn_file=str(f))
    with pytest.raises(ValueError, match="readable by others"):
        SC._resolve_dsn(args)

    f.chmod(0o600)
    assert SC._resolve_dsn(args) == "postgresql://user:pw@host/db"


def test_an_empty_or_missing_dsn_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    missing = argparse.Namespace(dsn=None, dsn_file=str(tmp_path / "nope"))
    with pytest.raises(ValueError, match="no such DSN file"):
        SC._resolve_dsn(missing)

    f = tmp_path / "blank"
    f.write_text("   ")
    f.chmod(0o600)
    with pytest.raises(ValueError, match="is empty"):
        SC._resolve_dsn(argparse.Namespace(dsn=None, dsn_file=str(f)))


def test_a_file_is_preferred_over_an_argument_so_the_password_stays_out_of_argv(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
    f = tmp_path / "dsn"
    f.write_text("postgresql://file/db")
    f.chmod(0o600)
    args = argparse.Namespace(dsn="postgresql://argv/db", dsn_file=str(f))
    assert SC._resolve_dsn(args) == "postgresql://file/db"
