"""The Label Studio pre-check's handling of the production connection string, without a
database: the file must be private, and a failure never prints the string or the host."""
import os

import psycopg
import pytest

from scripts import label_studio_precheck as P

SECRET = "postgresql://postgres.ref:s3cret-pw@db.example.supabase.co:5432/postgres"


def _dsn_file(tmp_path, mode):
    f = tmp_path / "backup-db.url"
    f.write_text(SECRET)
    os.chmod(f, mode)
    return f


def test_a_connection_file_others_can_read_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="readable by others") as e:
        P.main(["--dsn-file", str(_dsn_file(tmp_path, 0o644))])
    assert "s3cret" not in str(e.value)


def test_a_failed_connection_prints_neither_the_string_nor_the_host(tmp_path, monkeypatch, capsys):
    def refuse(dsn, **kw):
        raise psycopg.OperationalError(f'connection to server at "db.example.supabase.co" failed: {dsn}')
    monkeypatch.setattr(P.psycopg, "connect", refuse)
    assert P.main(["--dsn-file", str(_dsn_file(tmp_path, 0o600))]) == 2
    out = capsys.readouterr()
    assert "could not connect (OperationalError)" in out.out
    for leak in ("s3cret", "supabase.co", "postgres.ref"):
        assert leak not in out.out + out.err


def test_it_takes_its_expectations_from_the_deletion_script():
    projects, pools, fks = P.reviewed()
    assert (projects, pools, len(fks)) == (4, 5, 20)
    assert ("review_items", "task_answers", "review_item_id", "n") in fks
