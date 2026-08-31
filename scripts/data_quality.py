"""
Reusable data-quality validation (offline / CI).

GX is a DEV-only dependency (requirements-dev.txt), imported lazily inside the
functions so `import scripts.data_quality` never forces GX on the runtime app.

  validate_batch(df, required_keys, ...)   -> ingestion / schema integrity (INPUT)
  validate_deliverable(df, verdict_values) -> labeled-output integrity (OUTPUT)

Both return: {"passed": bool, "n_rows": int, "checks": [{expectation, column, passed, unexpected}]}

CLI gate (exit 0 clean / 1 failed), validates a project's ingested items:
  python scripts/data_quality.py [project_id]
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _validate(df, build_suite):
    """Shared engine: fresh context, unique data source (so repeated calls in one
    process never collide), validate, return a structured report."""
    import great_expectations as gx, uuid
    context = gx.get_context()                     # must precede building the suite
    suite = build_suite(gx)
    batch = (context.data_sources.add_pandas("ds_" + uuid.uuid4().hex[:8])
             .add_dataframe_asset("a").add_batch_definition_whole_dataframe("all")
             .get_batch(batch_parameters={"dataframe": df}))
    result = batch.validate(suite)
    checks = [{"expectation": r.expectation_config.type,
               "column": r.expectation_config.kwargs.get("column", r.expectation_config.kwargs.get("column_set")),
               "passed": r.success,
               "unexpected": None if r.success else r.result.get("partial_unexpected_list", r.result)}
              for r in result.results]
    return {"passed": result.success, "n_rows": len(df), "checks": checks}


def validate_batch(df, required_keys, classes=None, class_column=None, case_id_field=None):
    """INPUT / ingestion: required columns present, no nulls, no empty strings,
    optional class-in-set and case_id uniqueness."""
    def build(gx):
        suite = gx.ExpectationSuite(name="ingestion_schema_integrity")
        if required_keys:
            suite.add_expectation(gx.expectations.ExpectTableColumnsToMatchSet(
                column_set=list(required_keys), exact_match=False))
        for c in required_keys:
            suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column=c))
            suite.add_expectation(gx.expectations.ExpectColumnValueLengthsToBeBetween(column=c, min_value=1))
        if classes and class_column:
            suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column=class_column, value_set=list(classes)))
        if case_id_field:
            suite.add_expectation(gx.expectations.ExpectColumnValuesToBeUnique(column=case_id_field))
        return suite
    return _validate(df, build)


def validate_deliverable(df, verdict_values, classes=None):
    """OUTPUT / labeled items you'd ship: verdict in the allowed set, every item
    'done' (never ship in_progress / needs_adjudication), agreement in [0,1], and
    correct_label in the class set where present."""
    def build(gx):
        suite = gx.ExpectationSuite(name="deliverable_integrity")
        suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column="verdict", value_set=list(verdict_values)))
        if "status" in df.columns:
            suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column="status", value_set=["done"]))
        if "agreement" in df.columns:
            suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="agreement", min_value=0, max_value=1))
        if classes and "correct_label" in df.columns:
            suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column="correct_label", value_set=list(classes)))
        return suite
    report = _validate(df, build)
    # Confidentiality guard (not a GX map expectation — a table-shape assertion): a
    # delivered item must carry NO internal (_-prefixed) field, so gold answers
    # (_gold_expected) and reviewer detail/identities (_annotations) can never ship.
    leaked = [c for c in df.columns if str(c).startswith("_")]
    report["checks"].append({"expectation": "no_internal_keys_leaked",
                             "column": leaked or None, "passed": not leaked,
                             "unexpected": leaked or None})
    if leaked:
        report["passed"] = False
    return report


def _print(report, title=""):
    print(f"{title} rows={report['n_rows']}  PASS={report['passed']}")
    for c in report["checks"]:
        line = f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['expectation']} ({c['column']})"
        if not c["passed"]:
            line += f"   unexpected: {c['unexpected']}"
        print(line)


if __name__ == "__main__":
    import pandas as pd
    from app.services.supabase_client import get_client
    from app.services.labelstudio import required_data_keys
    db = get_client()
    q = db.table("project_submissions").select("id,company,eval_config")
    subs = (q.eq("id", sys.argv[1]).execute().data if len(sys.argv) > 1 else q.execute().data)
    if not subs:
        print("No project found."); sys.exit(2)
    proj = subs[0]; ec = proj["eval_config"] or {}
    required = required_data_keys(ec)
    case_id_field = ec.get("case_id_field") or (ec.get("schema") or {}).get("case_id_field")
    rows = db.table("project_items").select("content").eq("project_id", proj["id"]).execute().data
    df = pd.DataFrame([(r["content"] or {}) for r in rows])
    report = validate_batch(df, required, case_id_field=case_id_field)
    _print(report, title=f"[{proj['company']}] required={required}")
    sys.exit(0 if report["passed"] else 1)
