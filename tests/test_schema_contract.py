"""
Schema contract: every column the code writes must exist in supabase_schema.sql.

The failure-library endpoints build dicts and hand them to PostgREST. A key that is not
a real column fails at RUNTIME, against the live database, on a client's first call —
the worst possible place to find it. The DDL is the source of truth and it is checked
into this repo, so the mismatch can be caught here instead.

This parses the checked-in DDL rather than connecting to anything, so it runs in CI with
no credentials.

Run: PYTHONPATH=. pytest tests/test_schema_contract.py
"""
import re
from pathlib import Path

from app.services import failure_library as fl

SCHEMA = Path(__file__).resolve().parents[1] / "supabase_schema.sql"


def _columns(table: str) -> set:
    """Column names declared for `table` in the checked-in DDL."""
    sql = SCHEMA.read_text()
    m = re.search(rf"create table if not exists {table}\s*\((.*?)\n\);", sql, re.S)
    assert m, f"{table} not found in supabase_schema.sql"
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        # "name  type ..." — the first token is the column, unless it is a constraint.
        first = line.split()[0]
        if first.lower() in ("primary", "unique", "foreign", "constraint", "check"):
            continue
        cols.add(first.strip(","))
    return cols


def test_failure_library_writes_only_real_columns():
    cols = _columns("clinical_failures")
    item = {"idx": 0, "status": "done",
            "content": {"case_id": "C1", "scenario": "s", "prediction": "p",
                        "clinical_domain": "Neuro", "case_type": "Red-flag",
                        "expected_triage": "Emergency", "triage": "Self-care"},
            "label": {"verdict": "Incorrect", "severity": "Critical",
                      "error_category": "Missed red flag", "correct_triage": "Emergency",
                      "rationale": "r"}}

    extracted = fl.extract([item], client_email="a@b.c", project_id="p1", model_version="v1")[0]
    unknown = set(extracted) - cols
    assert not unknown, f"extract() writes non-existent column(s): {sorted(unknown)}"

    # merge() is what actually hits insert/update, on both the new and the repeat path.
    for prev in (None, {"status": fl.FIXED, "occurrences": 1, "in_benchmark": True,
                        "first_seen": "2026-01-01T00:00:00Z"}):
        merged = fl.merge(prev, extracted, model_version="v1")
        unknown = set(merged) - cols
        assert not unknown, f"merge(prev={prev is not None}) writes: {sorted(unknown)}"

    closed = fl.close({"status": fl.OPEN}, model_version="v2")
    unknown = set(closed) - cols
    assert not unknown, f"close() writes: {sorted(unknown)}"


def test_every_column_the_api_filters_on_exists():
    # The filters exposed by GET /failures. A typo here is a silent empty result set,
    # not an error — worse than a crash, because it looks like "no failures found".
    filters = {"client_email", "severity", "clinical_domain", "error_category",
               "status", "model_version", "in_benchmark", "case_key", "last_seen"}
    missing = filters - _columns("clinical_failures")
    assert not missing, f"API filters on non-existent column(s): {sorted(missing)}"


def test_client_view_exposes_no_internal_identifiers():
    # These must never reach a client response: the internal row id, the owning email,
    # and the project a failure was found in.
    view = fl.client_view({c: c for c in _columns("clinical_failures")})
    leaked = {"id", "client_email", "project_id"} & set(view)
    assert not leaked, f"client_view leaks: {sorted(leaked)}"


def test_status_values_the_code_writes_are_the_ones_the_schema_documents():
    sql = SCHEMA.read_text()
    m = re.search(r"status\s+text not null default 'open',\s*--\s*(.*)", sql)
    assert m, "clinical_failures.status comment missing"
    documented = {s.strip() for s in m.group(1).replace("|", " ").split() if s.isalpha()}
    assert {fl.OPEN, fl.FIXED, fl.REGRESSED} <= documented, (
        f"code writes statuses the schema does not document: "
        f"{ {fl.OPEN, fl.FIXED, fl.REGRESSED} - documented }")
