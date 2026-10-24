"""
scripts/backup_verify.py's own rules: where the connection string may come from, and what
counts as a backup that did not restore faithfully. The full run (dump, restore, compare)
is exercised against throwaway Postgres; see docs/operations.md.
"""
import os

import pytest

from scripts import backup_verify as B


def _measure(**over):
    base = {"counts": {"a": 2, "b": 0}, "checksums": {"a": "x", "b": "y"},
            "inventory": {k: [f"{k}-1"] for k in B.INVENTORY}}
    base.update(over)
    return base


def test_an_identical_restore_has_no_problems():
    assert B.compare(_measure(), _measure()) == {}


def test_every_kind_of_difference_is_reported():
    restored = _measure(counts={"a": 1, "b": 0, "extra": 3}, checksums={"a": "x", "b": "changed"},
                        inventory={**{k: [f"{k}-1"] for k in B.INVENTORY}, "policies": []})
    problems = B.compare(_measure(), restored)
    assert problems["row_counts"] == {"a": [2, 1]}
    assert problems["content_checksums"] == ["b"]
    assert problems["unexpected_tables"] == ["extra"]
    assert problems["schema"]["policies"]["missing_after_restore"] == ["policies-1"]


def test_the_connection_string_file_must_be_private(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKUP_DATABASE_URL", raising=False)
    f = tmp_path / "db.url"
    f.write_text("postgresql://u:secret@h/db\n")
    os.chmod(f, 0o644)
    with pytest.raises(SystemExit, match="readable by others"):
        B.read_dsn(f)
    os.chmod(f, 0o600)
    assert B.read_dsn(f) == "postgresql://u:secret@h/db"
    with pytest.raises(SystemExit, match="No connection string"):
        B.read_dsn(tmp_path / "missing")


def test_the_report_names_the_target_without_its_password():
    params = {"host": "db.example", "port": "5432", "dbname": "postgres", "user": "postgres"}
    assert "secret" not in B._public_target(params)
