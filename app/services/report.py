"""
Model-performance report (the client deliverable).

Turns the pulled, structured radiologist verdicts on a project (already in
project_items.label after /ls/pull) into "your model is X% accurate, here are the misses".

Ground truth is radiologist-adjudicated:
  - verdict == Correct              -> ground truth = the model's own prediction
  - verdict == Incorrect / Partial  -> ground truth = the radiologist's corrected_label

Only *assessable* cases enter the metrics. Excluded, with the reason stated:
  - unlabeled (not reviewed yet / no verdict)
  - cannot_assess (radiologist could not read it)
  - incomplete: a wrong verdict with NO corrected_label  -> flagged, never guessed, never dropped silently
  - missing model prediction in content (data problem)   -> flagged

Stats are pure Python (counts + a confusion matrix). No numpy/sklearn, so no version surface.

Honesty rails baked in:
  - reports n (support) per class and caveats that ~100 cases is a sample, not the truth
  - headline accuracy = % verdict==Correct; the confusion-matrix diagonal is derived from
    (prediction vs ground_truth) and can differ for 'Partially correct' cases whose corrected
    label equals the prediction. That divergence is surfaced, not hidden.
  - cases are keyed by their stable idx (the identity ingest guarantees), never list position.
"""
import csv
import io
from collections import Counter
from datetime import datetime, timezone

# A project's purpose decides its deliverable: 'evaluate' -> a model-performance scorecard;
# 'label'/'create' -> a summary of the produced dataset (there is no prediction to score).
PURPOSES = ("evaluate", "label", "create")

CORRECT, INCORRECT, PARTIAL = "correct", "incorrect", "partial"
THIN_SUPPORT = 10  # below this many ground-truth cases, per-class metrics are noisy

# The client's OWN case identifier, surfaced so every verdict binds to their id, not our
# row index. The operator can name the column anything and declare it via eval_config
# ("case_id_field"); absent that, these common names are picked up automatically.
_CASE_ID_KEYS = ("case_id", "study_id", "study_uid", "accession", "accession_number")


def _case_id(content: dict, field: str | None):
    """The client's case identifier for a row, or None. An explicit `field` wins; otherwise
    fall back to the first recognised id column present in the item content."""
    c = content or {}
    if field and c.get(field) not in (None, ""):
        return c.get(field)
    for k in _CASE_ID_KEYS:
        v = c.get(k)
        if v not in (None, ""):
            return v
    return None


def _verdict_kind(label: dict):
    v = (label or {}).get("verdict")
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if s.startswith("correct"):
        return CORRECT
    if s.startswith("incorrect"):
        return INCORRECT
    if s.startswith("partial"):
        return PARTIAL
    return None


def _prf(tp: int, fp: int, fn: int):
    p = tp / (tp + fp) if (tp + fp) else None      # None = never predicted this class
    r = tp / (tp + fn) if (tp + fn) else None      # None = class never in ground truth
    if p is None or r is None:
        f = None
    elif p + r == 0:
        f = 0.0
    else:
        f = 2 * p * r / (p + r)
    return p, r, f


def compute_report(items: list[dict], classes=None, case_id_field: str | None = None) -> dict:
    """Pure function: list of items ({idx, content, label, status}) -> structured report.
    `classes` is the canonical class list (e.g. from eval_config); observed classes are
    unioned in so nothing is missed."""
    excluded = {"unlabeled": 0, "cannot_assess": 0,
                "incomplete_missing_correct_label": 0, "missing_prediction": 0}
    assessable, cases = [], []
    critical_misses, failure_cases, incomplete_cases = [], [], []
    observed = set()

    for it in items:
        idx = it.get("idx")
        content = it.get("content") or {}
        label = it.get("label") or {}
        image = content.get("image")
        pred = content.get("prediction")
        # The input the case was judged on — the patient message / prompt for text
        # tasks (the image carries it for image tasks). Surfaced so a flagged miss
        # shows *what* was asked, not just the labels.
        prompt = content.get("prompt") or content.get("text") or content.get("question")
        kind = _verdict_kind(label)

        base = {"idx": idx, "image": image, "prompt": prompt, "model_prediction": pred,
                "case_id": _case_id(content, case_id_field),
                "verdict": (label or {}).get("verdict"),
                "confidence": (label or {}).get("radiologist_confidence"),
                "rationale": (label or {}).get("rationale")}

        if it.get("status") != "done":
            excluded["unlabeled"] += 1
            cases.append({**base, "ground_truth": None, "correct_label": None,
                          "critical_miss": False, "finding": None, "disposition": "excluded:unlabeled"})
            continue
        # cannot_assess means the radiologist DID review it, so it outranks a missing verdict.
        if (label or {}).get("cannot_assess"):
            excluded["cannot_assess"] += 1
            cases.append({**base, "ground_truth": None, "correct_label": None,
                          "critical_miss": False, "finding": None, "disposition": "excluded:cannot_assess"})
            continue
        if kind is None:
            excluded["unlabeled"] += 1
            cases.append({**base, "ground_truth": None, "correct_label": None,
                          "critical_miss": False, "finding": None, "disposition": "excluded:unlabeled"})
            continue
        if not pred:
            excluded["missing_prediction"] += 1
            row = {**base, "ground_truth": None, "correct_label": None, "critical_miss": False,
                   "finding": None, "disposition": "excluded:missing_prediction", "reason": "no model prediction in content"}
            cases.append(row)
            incomplete_cases.append({"idx": idx, "case_id": base["case_id"], "image": image,
                                     "verdict": base["verdict"], "model_prediction": pred,
                                     "reason": "missing model prediction in content"})
            continue

        if kind == CORRECT:
            corrected, truth = None, pred
        else:
            corrected = (label or {}).get("correct_label")
            if not corrected or not isinstance(corrected, str):
                excluded["incomplete_missing_correct_label"] += 1
                row = {**base, "ground_truth": None, "correct_label": None, "critical_miss": False,
                       "finding": None, "disposition": "excluded:incomplete_missing_correct_label",
                       "reason": "wrong verdict but no corrected_label"}
                cases.append(row)
                incomplete_cases.append({"idx": idx, "case_id": base["case_id"], "image": image,
                                         "verdict": base["verdict"], "model_prediction": pred,
                                         "reason": "wrong verdict but no corrected_label"})
                continue
            truth = corrected

        observed.add(pred)
        observed.add(truth)
        cm = (label or {}).get("critical_miss")
        cm_present = isinstance(cm, dict) and cm.get("present") is True
        finding = cm.get("finding") if isinstance(cm, dict) else None

        assessable.append({"idx": idx, "model_prediction": pred, "ground_truth": truth, "kind": kind})
        cases.append({**base, "ground_truth": truth, "correct_label": corrected,
                      "critical_miss": cm_present, "finding": finding, "disposition": "assessable"})

        if kind in (INCORRECT, PARTIAL):
            failure_cases.append({"idx": idx, "case_id": base["case_id"], "image": image,
                                  "prompt": base["prompt"],
                                  "verdict": base["verdict"], "model_prediction": pred,
                                  "correct_label": corrected, "rationale": base["rationale"]})
        if cm_present:
            critical_misses.append({"idx": idx, "case_id": base["case_id"], "image": image,
                                    "prompt": base["prompt"],
                                    "model_prediction": pred,
                                    "correct_label": corrected if kind != CORRECT else pred,
                                    "finding": finding, "rationale": base["rationale"]})

    cls_list = sorted(set(classes or []) | observed)

    # Confusion matrix: conf[pred][true]
    conf = {p: {t: 0 for t in cls_list} for p in cls_list}
    for a in assessable:
        conf[a["model_prediction"]][a["ground_truth"]] += 1

    per_class = {}
    for c in cls_list:
        tp = conf[c][c]
        fp = sum(conf[c][t] for t in cls_list if t != c)
        fn = sum(conf[p][c] for p in cls_list if p != c)
        support = sum(conf[p][c] for p in cls_list)   # ground-truth count for c
        p, r, f = _prf(tp, fp, fn)
        per_class[c] = {"support": support, "tp": tp, "fp": fp, "fn": fn,
                        "precision": p, "recall": r, "f1": f}

    n_assess = len(assessable)
    n_correct = sum(1 for a in assessable if a["kind"] == CORRECT)
    accuracy = (n_correct / n_assess) if n_assess else None
    partial_on_diagonal = sum(1 for a in assessable
                              if a["kind"] == PARTIAL and a["model_prediction"] == a["ground_truth"])

    caveats = [
        f"Metrics are computed on {n_assess} assessable case(s). At this sample size, per-class "
        "numbers are indicative of this sample, not the model's true population performance. "
        "Read them alongside the support (n) per class.",
    ]
    thin = [c for c in cls_list if 0 < per_class[c]["support"] < THIN_SUPPORT]
    if thin:
        caveats.append(f"Thin support (under {THIN_SUPPORT} ground-truth cases): {thin}. "
                       "Their precision/recall are noisy and can swing on a single case.")
    zero = [c for c in cls_list if per_class[c]["support"] == 0]
    if zero:
        caveats.append(f"No ground-truth cases for: {zero}. Metrics are undefined (n/a), not zero.")
    if partial_on_diagonal:
        caveats.append(f"{partial_on_diagonal} 'Partially correct' case(s) had a corrected label equal "
                       "to the model prediction, so the confusion-matrix diagonal counts them as matches "
                       "while headline accuracy (verdict==Correct) does not. The two rates can differ by this much.")
    if excluded["incomplete_missing_correct_label"]:
        caveats.append(f"{excluded['incomplete_missing_correct_label']} wrong-verdict case(s) are missing a "
                       "corrected_label; they were EXCLUDED from metrics and flagged as incomplete (see "
                       "incomplete_cases), never guessed. Complete them for accurate metrics.")

    # QA / inter-reviewer agreement — present only when items were multi-reviewed.
    qa = None
    agrees, reviewer_counts, disagreement_cases = [], [], []
    for it in items:
        lbl = it.get("label") or {}
        if lbl.get("_agreement") is None:
            continue
        agrees.append(lbl["_agreement"])
        reviewer_counts.append(lbl.get("_reviewers") or 0)
        if lbl.get("_disagreed"):
            content = it.get("content") or {}
            disagreement_cases.append({
                "idx": it.get("idx"),
                "case_id": _case_id(content, case_id_field),
                "agreement": lbl["_agreement"],
                "verdict": lbl.get("verdict"),
                "reviewers": lbl.get("_reviewers"),
            })
    if agrees:
        qa = {
            "reviewers": max(reviewer_counts) if reviewer_counts else None,
            "mean_agreement": round(sum(agrees) / len(agrees), 3),
            "reviewed_items": len(agrees),
            "disagreements": len(disagreement_cases),
            "disagreement_cases": disagreement_cases,
        }

    return {
        "totals": {
            "items": len(items),
            "assessable": n_assess,
            "excluded": excluded,
            "excluded_total": sum(excluded.values()),
        },
        "qa": qa,
        "accuracy": {"correct": n_correct, "assessable": n_assess, "value": accuracy},
        "classes": cls_list,
        "per_class": per_class,
        "confusion_matrix": {
            "orientation": "rows=predicted, cols=true",
            "labels": cls_list,
            "matrix": [[conf[p][t] for t in cls_list] for p in cls_list],
        },
        "critical_misses": critical_misses,
        "failure_cases": failure_cases,
        "incomplete_cases": incomplete_cases,
        "cases": cases,
        "caveats": caveats,
    }


def _agreement_qa(items: list[dict], case_id_field: str | None) -> dict | None:
    """Inter-reviewer agreement, present only when items were multi-reviewed. Shared by the
    evaluation and dataset deliverables so both report agreement the same way."""
    agrees, reviewer_counts, disagreement_cases = [], [], []
    for it in items:
        lbl = it.get("label") or {}
        if lbl.get("_agreement") is None:
            continue
        agrees.append(lbl["_agreement"])
        reviewer_counts.append(lbl.get("_reviewers") or 0)
        if lbl.get("_disagreed"):
            disagreement_cases.append({
                "idx": it.get("idx"),
                "case_id": _case_id(it.get("content") or {}, case_id_field),
                "agreement": lbl["_agreement"],
                "reviewers": lbl.get("_reviewers"),
            })
    if not agrees:
        return None
    return {
        "reviewers": max(reviewer_counts) if reviewer_counts else None,
        "mean_agreement": round(sum(agrees) / len(agrees), 3),
        "reviewed_items": len(agrees),
        "disagreements": len(disagreement_cases),
        "disagreement_cases": disagreement_cases,
    }


def compute_dataset_report(items: list[dict], fields: dict, purpose: str,
                           case_id_field: str | None = None) -> dict:
    """Deliverable for label/create projects: a summary of the PRODUCED dataset, not an
    accuracy scorecard. Reports completion/coverage, a per-field distribution or coverage,
    and inter-reviewer agreement. The produced values themselves live on each item's label
    (returned with the results); this summarises them rather than scoring a ground truth."""
    fields = fields or {}
    status_counts = Counter((it.get("status") or "pending") for it in items)
    done_items = [it for it in items if it.get("status") == "done"]

    field_summaries = {}
    for fname, fdef in fields.items():
        ftype = (fdef or {}).get("type")
        vals = [(it.get("label") or {}).get(fname) for it in done_items]
        vals = [v for v in vals if v not in (None, "")]
        if ftype in ("single", "from_classes", "scale", "flag"):
            field_summaries[fname] = {"type": ftype, "answered": len(vals),
                                      "distribution": dict(Counter(str(v) for v in vals))}
        elif ftype == "structured":
            present = sum(1 for v in vals if isinstance(v, dict) and v.get("present"))
            findings = Counter(v.get("finding") for v in vals
                               if isinstance(v, dict) and v.get("present") and v.get("finding"))
            field_summaries[fname] = {"type": "structured", "present": present, "findings": dict(findings)}
        elif ftype == "text":
            lengths = [len(str(v)) for v in vals]
            field_summaries[fname] = {"type": "text", "answered": len(vals),
                                      "avg_length": round(sum(lengths) / len(lengths)) if lengths else 0}
        else:
            field_summaries[fname] = {"type": ftype, "answered": len(vals)}

    total, completed = len(items), len(done_items)
    qa = _agreement_qa(items, case_id_field)
    noun = "labelled" if purpose == "label" else "produced"
    caveats = [
        f"This is a '{purpose}' project: the deliverable is the {noun} dataset itself (each "
        "item's values are returned with the results), summarised here — not an accuracy "
        "score, since there is no model prediction to score against.",
    ]
    if qa is None and completed:
        caveats.append("Items were single-reviewed, so no inter-reviewer agreement is reported.")

    return {
        "kind": "dataset",
        "purpose": purpose,
        "totals": {
            "items": total,
            "completed": completed,
            "in_progress": status_counts.get("in_progress", 0),
            "pending": status_counts.get("pending", 0) + status_counts.get("queued", 0),
            "coverage": round(completed / total, 3) if total else None,
        },
        "fields": field_summaries,
        "qa": qa,
        "caveats": caveats,
    }


def build_report(db, project_id: str) -> dict:
    """Fetch a project's items + eval_config and compute the deliverable for its PURPOSE:
    'evaluate' -> a model-performance scorecard; 'label'/'create' -> a produced-dataset
    summary. Purpose defaults to 'evaluate' when unset (existing projects are evaluations)."""
    ec = {}
    try:
        sub = db.table("project_submissions").select("eval_config").eq("id", project_id).limit(1).execute()
        ec = (sub.data[0].get("eval_config") if sub.data else None) or {}
    except Exception:
        ec = {}
    schema = ec.get("schema") or {}
    purpose = str(ec.get("purpose") or "evaluate").strip().lower()
    if purpose not in PURPOSES:
        purpose = "evaluate"
    # The column carrying the client's own case/study id, so outputs bind to THEIR id.
    case_id_field = ec.get("case_id_field") or schema.get("case_id_field")
    rows = (
        db.table("project_items").select("idx,content,label,status")
        .eq("project_id", project_id).order("idx").execute()
    )
    items = rows.data or []

    if purpose == "evaluate":
        report = compute_report(items, schema.get("classes") or None, case_id_field=case_id_field)
        report["kind"] = "evaluation"
        report["purpose"] = "evaluate"
    else:
        report = compute_dataset_report(items, schema.get("fields") or {}, purpose, case_id_field=case_id_field)

    report["project_id"] = project_id
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    return report


# ── Human-readable renderings ────────────────────────────────────────────────

def _pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def render_dataset_markdown(rep: dict) -> str:
    t = rep["totals"]
    out = [f"# {rep.get('purpose', 'dataset').capitalize()} dataset summary"]
    if rep.get("project_id"):
        out.append(f"Project `{rep['project_id']}` · generated {rep.get('generated_at', '')}")
    out += ["", f"- Items: **{t['items']}**  ·  completed: **{t['completed']}**  ·  "
            f"in progress: **{t['in_progress']}**  ·  pending: **{t['pending']}**",
            f"- Coverage: **{_pct(t['coverage'])}**", ""]
    out.append("## Per-field summary")
    for fname, fs in (rep.get("fields") or {}).items():
        if "distribution" in fs:
            dist = ", ".join(f"{k}: {v}" for k, v in fs["distribution"].items()) or "—"
            out.append(f"- **{fname}** ({fs['type']}, answered {fs['answered']}): {dist}")
        elif fs.get("type") == "text":
            out.append(f"- **{fname}** (text): {fs['answered']} answered, avg {fs['avg_length']} chars")
        elif fs.get("type") == "structured":
            out.append(f"- **{fname}** (structured): present in {fs['present']} case(s)")
        else:
            out.append(f"- **{fname}** ({fs.get('type')}): answered {fs.get('answered')}")
    out.append("")
    qa = rep.get("qa")
    if qa:
        out += [f"## Inter-reviewer agreement",
                f"- Reviewers: {qa['reviewers']}  ·  mean agreement: {_pct(qa['mean_agreement'])}  ·  "
                f"disagreements: {qa['disagreements']} of {qa['reviewed_items']}", ""]
    out.append("## Notes")
    for c in rep.get("caveats", []):
        out.append(f"- {c}")
    return "\n".join(out)


def render_markdown(rep: dict) -> str:
    if rep.get("kind") == "dataset":
        return render_dataset_markdown(rep)
    t = rep["totals"]
    ex = t["excluded"]
    acc = rep["accuracy"]
    L = rep["confusion_matrix"]["labels"]
    out = []
    out.append(f"# Model performance report")
    if rep.get("project_id"):
        out.append(f"Project `{rep['project_id']}` · generated {rep.get('generated_at', '')}")
    out.append("")
    out.append(f"**Headline accuracy: {_pct(acc['value'])}** "
               f"({acc['correct']} of {acc['assessable']} assessable cases the model got right).")
    out.append("")
    out.append(f"- Items in project: **{t['items']}**")
    out.append(f"- Assessable (in metrics): **{t['assessable']}**")
    out.append(f"- Excluded: **{t['excluded_total']}** "
               f"(unlabeled {ex['unlabeled']}, cannot-assess {ex['cannot_assess']}, "
               f"incomplete {ex['incomplete_missing_correct_label']}, missing-prediction {ex['missing_prediction']})")
    out.append("")

    out.append("## Per-class metrics")
    out.append("| class | n (support) | precision | recall | F1 |")
    out.append("|---|---|---|---|---|")
    for c in L:
        m = rep["per_class"][c]
        out.append(f"| {c} | {m['support']} | {_pct(m['precision'])} | {_pct(m['recall'])} | {_pct(m['f1'])} |")
    out.append("")

    out.append("## Confusion matrix (rows = model predicted, cols = radiologist truth)")
    out.append("| pred \\ true | " + " | ".join(L) + " |")
    out.append("|" + "---|" * (len(L) + 1))
    for i, p in enumerate(L):
        row = rep["confusion_matrix"]["matrix"][i]
        out.append(f"| **{p}** | " + " | ".join(str(x) for x in row) + " |")
    out.append("")

    out.append(f"## Critical misses ({len(rep['critical_misses'])})")
    out.append("_Cases the radiologist flagged as a clinically critical miss — the highest-priority failures._")
    if rep["critical_misses"]:
        out.append("| case id | idx | model said | correct | finding missed | note |")
        out.append("|---|---|---|---|---|---|")
        for c in rep["critical_misses"]:
            out.append(f"| {c.get('case_id') or '—'} | {c['idx']} | {c['model_prediction']} | {c.get('correct_label')} | "
                       f"{c.get('finding')} | {(c.get('rationale') or '').replace(chr(10), ' ')} |")
    else:
        out.append("None flagged.")
    out.append("")

    out.append(f"## Failure cases ({len(rep['failure_cases'])})")
    if rep["failure_cases"]:
        out.append("| case id | idx | verdict | model said | correct | note |")
        out.append("|---|---|---|---|---|---|")
        for c in rep["failure_cases"]:
            out.append(f"| {c.get('case_id') or '—'} | {c['idx']} | {c['verdict']} | {c['model_prediction']} | "
                       f"{c.get('correct_label')} | {(c.get('rationale') or '').replace(chr(10), ' ')} |")
    else:
        out.append("None.")
    out.append("")

    if rep["incomplete_cases"]:
        out.append(f"## Incomplete cases ({len(rep['incomplete_cases'])}) — excluded from metrics, need fixing")
        out.append("| idx | verdict | model said | reason |")
        out.append("|---|---|---|---|")
        for c in rep["incomplete_cases"]:
            out.append(f"| {c['idx']} | {c.get('verdict')} | {c.get('model_prediction')} | {c['reason']} |")
        out.append("")

    out.append("## Read this before quoting the numbers")
    for c in rep["caveats"]:
        out.append(f"- {c}")
    out.append("")
    return "\n".join(out)


def render_cases_csv(rep: dict) -> str:
    cols = ["case_id", "idx", "disposition", "model_prediction", "ground_truth", "verdict",
            "correct_label", "critical_miss", "finding", "confidence", "rationale", "image"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for c in rep["cases"]:
        w.writerow(c)
    return buf.getvalue()
