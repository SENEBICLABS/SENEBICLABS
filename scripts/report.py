"""
Build the model-performance report for a project and write it out for the client.

Usage:
  python scripts/report.py --project-id <uuid> [--out-dir ./out]

Writes three files (same manual-first posture as ingest):
  report_<id>.json        full structured report
  report_<id>.md          readable summary to hand to the client
  report_<id>_cases.csv   per-case table (every case, its disposition, verdict, ground truth)

Reads the pulled verdicts already in project_items.label (run /ls/pull first).
"""
import argparse
import json
import os

from app.services import report as R
from app.services.supabase_client import get_client


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    db = get_client()
    if db is None:
        raise SystemExit("Supabase not configured (check .env).")

    rep = R.build_report(db, args.project_id)
    os.makedirs(args.out_dir, exist_ok=True)
    base = os.path.join(args.out_dir, f"report_{args.project_id[:8]}")

    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(R.render_markdown(rep))
    with open(base + "_cases.csv", "w", encoding="utf-8") as f:
        f.write(R.render_cases_csv(rep))

    print(R.render_markdown(rep))
    print(f"\nWrote {base}.json / {base}.md / {base}_cases.csv")


if __name__ == "__main__":
    main()
