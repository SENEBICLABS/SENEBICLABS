import great_expectations as gx
import pandas as pd

# ── 1. An incoming ingestion batch, as a table (one row per item). ──
# Row 3 is deliberately broken so you can see the checks bite.
df = pd.DataFrame([
    {"case_id":"C1","prompt":"58M pre-op CXR","output":"Normal","prediction":"Normal"},
    {"case_id":"C2","prompt":"71F cough","output":"RLL consolidation","prediction":"Parenchyma"},
    {"case_id":"C2","prompt":None,"output":"","prediction":"Bogus"},
])
CLASSES  = ["Normal","Airway","Parenchyma","Vascular","Pleural"]
REQUIRED = ["case_id","prompt","output","prediction"]

# ── 2. Wire the DataFrame in as a GX "batch" (ephemeral, in-memory context). ──
batch = (gx.get_context()
         .data_sources.add_pandas("ingest")
         .add_dataframe_asset("batch")
         .add_batch_definition_whole_dataframe("all")
         .get_batch(batch_parameters={"dataframe": df}))

# ── 3. The schema-integrity suite: the rules a batch must satisfy to be ingestible. ──
suite = gx.ExpectationSuite(name="ingestion_schema_integrity")
suite.add_expectation(gx.expectations.ExpectTableColumnsToMatchSet(column_set=REQUIRED, exact_match=False))
for c in REQUIRED:
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column=c))
for c in ["case_id","prompt","output"]:                      # empty string is NOT null, so check length too
    suite.add_expectation(gx.expectations.ExpectColumnValueLengthsToBeBetween(column=c, min_value=1))
suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column="prediction", value_set=CLASSES))
suite.add_expectation(gx.expectations.ExpectColumnValuesToBeUnique(column="case_id"))

# ── 4. Validate and print a readable pass/fail. ──
result = batch.validate(suite)
print("OVERALL PASS:", result.success)
for r in result.results:
    cfg = r.expectation_config
    col = cfg.kwargs.get("column", cfg.kwargs.get("column_set","-"))
    print(f"  [{'PASS' if r.success else 'FAIL'}] {cfg.type} ({col})")
    if not r.success:
        print("       unexpected:", r.result.get("partial_unexpected_list", r.result))
