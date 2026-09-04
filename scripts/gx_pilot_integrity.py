import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import great_expectations as gx
import pandas as pd
from app.services.supabase_client import get_client
from app.services.labelstudio import required_data_keys

db = get_client()

# 1. The pilot project (the only live submission) + its real config.
proj = db.table("project_submissions").select("id,company,eval_config").execute().data[0]
pid, ec = proj["id"], proj["eval_config"]
required = required_data_keys(ec)                       # the EXACT keys your pipeline requires
print(f"Project: {proj['company']} | required keys: {required}")

# 2. Pull its items' content into a DataFrame (one row per item).
rows = db.table("project_items").select("idx,content").eq("project_id", pid).order("idx").execute().data
df = pd.DataFrame([{**(r["content"] or {}), "_idx": r["idx"]} for r in rows])
print(f"{len(df)} items pulled")

# 3. Build the schema-integrity suite FROM the real required keys (not hard-coded).
batch = (gx.get_context().data_sources.add_pandas("ingest")
         .add_dataframe_asset("pilot").add_batch_definition_whole_dataframe("all")
         .get_batch(batch_parameters={"dataframe": df}))
suite = gx.ExpectationSuite(name="pilot_ingestion_integrity")
suite.add_expectation(gx.expectations.ExpectTableColumnsToMatchSet(column_set=required, exact_match=False))
for c in required:
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column=c))
    suite.add_expectation(gx.expectations.ExpectColumnValueLengthsToBeBetween(column=c, min_value=1))

# 4. Validate.
result = batch.validate(suite)
print("OVERALL PASS:", result.success)
for r in result.results:
    cfg = r.expectation_config
    col = cfg.kwargs.get("column", cfg.kwargs.get("column_set","-"))
    print(f"  [{'PASS' if r.success else 'FAIL'}] {cfg.type} ({col})")
    if not r.success:
        print("       unexpected:", r.result.get("partial_unexpected_list", r.result))
