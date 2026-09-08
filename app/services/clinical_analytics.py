"""
Clinical analytics layered on top of a project's reviewed items.

The base report (services/report.py) answers "is the model right?". These answer the
questions a clinical-safety buyer actually asks:

  triage_metrics    — did it assign the right URGENCY, and when it was wrong, which
                      direction? Under-triage (too calm) and over-triage (too alarmed)
                      are not the same failure and must never be averaged together.
  severity_summary  — how BAD were the errors? A missed red flag and a clumsy sentence
                      both count as "incorrect" in a plain accuracy score.
  taxonomy_summary  — WHY did it fail, and (crossed with severity) which failure modes
                      carry the dangerous errors.
  slice_summary     — how does performance vary BY clinical domain / case type, so a
                      headline number cannot hide a domain where the model is unsafe.

All of it is declarative: a project opts in through `eval_config.analytics`, and a
project without that key gets exactly the report it got before. Nothing here is
hardcoded to a class list, a severity scale, or a triage vocabulary — the ORDER comes
from the config, because "Emergency outranks Routine" is a clinical fact the operator
declares, not something a library should assume.

Pure Python, like report.py: counts and ratios only, no numpy, no version surface.

Config shape (all sections optional):

    "analytics": {
      "triage": {
        "order": ["Self-care", "Routine", "Urgent", "Emergency"],
        "model_field": "triage",              # the model's level, on item content
        "expected_field": "expected_triage",  # the case's clinician-authored level, on content
        "correct_field": "correct_triage"     # a reviewer's correction, on label (wins)
      },
      "severity": {"field": "severity",
                   "order": ["Minor", "Moderate", "High", "Critical"]},
      "taxonomy": {"field": "error_category"},
      "slice_by": ["clinical_domain", "case_type"]
    }
"""
from collections import Counter, defaultdict

# Errors at or above this rank in a severity scale are the ones a clinical buyer wants
# listed case by case rather than counted. Index from the TOP of the declared order, so
# it works for a 3-level or a 5-level scale without reconfiguring.
_LISTED_SEVERITIES = 2


def _norm(v):
    """A comparable form of a reviewer's answer. Choice fields arrive as a string, but a
    consensus over reviewers can leave a list; take the first, since the caller's config
    declares single-valued fields here."""
    if isinstance(v, list):
        v = v[0] if v else None
    return None if v in (None, "") else str(v)


def _scored_items(items):
    """Items a clinician actually finished. Anything held for adjudication is excluded for
    the same reason the base report excludes it: an unresolved split is not an answer."""
    return [it for it in items if it.get("status") == "done"]


def _case_ref(it, case_id_field=None):
    content = it.get("content") or {}
    cid = None
    if case_id_field:
        cid = content.get(case_id_field)
    if cid is None:
        for k in ("case_id", "study_id", "accession"):
            if content.get(k) is not None:
                cid = content[k]
                break
    return {"idx": it.get("idx"), "case_id": cid}


# ── Triage ───────────────────────────────────────────────────────────────────────

def triage_metrics(items, cfg, case_id_field=None):
    """Ordinal triage accuracy: direction and distance, not just right/wrong.

    A confusion matrix alone cannot answer "did it under-triage?" because it does not know
    Emergency outranks Routine. `order` supplies that ranking, lowest urgency first, and
    every metric here derives from the signed rank difference (model - expected):

        negative -> UNDER-triage (the model was calmer than the patient needed)
        positive -> OVER-triage  (the model escalated further than needed)

    missed_emergency is called out separately: the expected level was the top of the scale
    and the model chose anything lower. That is the failure that reaches a coroner, so it
    is counted on its own rather than folded into under_triage.
    """
    order = [str(o) for o in (cfg.get("order") or [])]
    if len(order) < 2:
        return None
    rank = {lvl: i for i, lvl in enumerate(order)}
    model_field = cfg.get("model_field") or "triage"
    expected_field = cfg.get("expected_field") or "expected_triage"
    correct_field = cfg.get("correct_field") or "correct_triage"

    compared, unrankable = 0, 0
    exact = 0
    under, over = [], []
    missed_emergency = []
    deltas = Counter()
    matrix = defaultdict(Counter)   # matrix[expected][model]
    top = order[-1]

    for it in _scored_items(items):
        content, label = it.get("content") or {}, it.get("label") or {}
        model = _norm(content.get(model_field))
        # A reviewer's correction is the ground truth when present; otherwise the level the
        # case was authored with.
        expected = _norm(label.get(correct_field)) or _norm(content.get(expected_field))
        if model is None or expected is None:
            continue
        if model not in rank or expected not in rank:
            unrankable += 1
            continue

        compared += 1
        matrix[expected][model] += 1
        d = rank[model] - rank[expected]
        deltas[d] += 1
        ref = {**_case_ref(it, case_id_field), "expected": expected, "model": model,
               "levels_off": d, "rationale": label.get("rationale")}
        if d == 0:
            exact += 1
        elif d < 0:
            under.append(ref)
            if expected == top:
                missed_emergency.append(ref)
        else:
            over.append(ref)

    if not compared:
        return None

    # Worst first, and under-triage before over-triage at equal distance: being too calm is
    # the more dangerous direction, so it should lead the list a client reads top-down.
    under.sort(key=lambda r: r["levels_off"])
    over.sort(key=lambda r: -r["levels_off"])

    total_off = sum(abs(d) * n for d, n in deltas.items())
    return {
        "levels": order,
        "compared": compared,
        "unrankable": unrankable,
        "accuracy": round(exact / compared, 3),
        "exact": exact,
        "under_triage": {"count": len(under), "rate": round(len(under) / compared, 3),
                         "cases": under[:50]},
        "over_triage": {"count": len(over), "rate": round(len(over) / compared, 3),
                        "cases": over[:50]},
        "missed_emergency": {"level": top, "count": len(missed_emergency),
                             "cases": missed_emergency[:50]},
        "mean_levels_off": round(total_off / compared, 3),
        "delta_distribution": {str(d): n for d, n in sorted(deltas.items())},
        "matrix": {"orientation": "rows=expected, cols=model", "labels": order,
                   "matrix": [[matrix[e][m] for m in order] for e in order]},
    }


# ── Severity ─────────────────────────────────────────────────────────────────────

def severity_summary(items, cfg, case_id_field=None):
    """Distribution of clinician-assigned error severity, plus the serious cases listed.

    Ordered by the declared scale rather than alphabetically, so "Critical" cannot sort
    next to "Minor" and be read past. The top two levels are listed case by case: those
    are the ones a clinical team needs to see individually, not as a count.
    """
    field = cfg.get("field") or "severity"
    order = [str(o) for o in (cfg.get("order") or [])]
    counts, listed, unranked = Counter(), [], Counter()
    serious = set(order[-_LISTED_SEVERITIES:]) if order else set()

    for it in _scored_items(items):
        label = it.get("label") or {}
        v = _norm(label.get(field))
        if v is None:
            continue
        if order and v not in order:
            unranked[v] += 1
            continue
        counts[v] += 1
        if v in serious:
            listed.append({**_case_ref(it, case_id_field), "severity": v,
                           "rationale": label.get("rationale") or label.get("notes")})

    if not counts and not unranked:
        return None
    scale = order or sorted(counts)
    graded = sum(counts.values())
    listed.sort(key=lambda r: -scale.index(r["severity"]) if r["severity"] in scale else 0)
    return {
        "scale": scale,
        "graded": graded,
        "distribution": {lvl: counts.get(lvl, 0) for lvl in scale},
        "off_scale": dict(unranked),
        "serious": {"levels": sorted(serious, key=scale.index) if serious else [],
                    "count": len(listed), "cases": listed[:50]},
    }


# ── Failure taxonomy ─────────────────────────────────────────────────────────────

def taxonomy_summary(items, cfg, severity_cfg=None, case_id_field=None):
    """Which failure modes occur, and — crossed with severity — which ones are dangerous.

    Handles a multi-select category (a value that arrives as a list) by counting every
    category on the item, so an error that is both a triage failure and a missed red flag
    is counted under both rather than being forced into one.

    The cross-tab is the point: "hallucination x12" is interesting, "hallucination x12, of
    which 9 Critical" is actionable.
    """
    field = cfg.get("field") or "error_category"
    sev_field = (severity_cfg or {}).get("field") or "severity"
    sev_order = [str(o) for o in ((severity_cfg or {}).get("order") or [])]

    counts = Counter()
    by_severity = defaultdict(Counter)
    examples = defaultdict(list)

    for it in _scored_items(items):
        label = it.get("label") or {}
        raw = label.get(field)
        if raw in (None, ""):
            continue
        cats = raw if isinstance(raw, list) else [raw]
        sev = _norm(label.get(sev_field))
        for c in cats:
            c = _norm(c)
            if c is None:
                continue
            counts[c] += 1
            if sev:
                by_severity[c][sev] += 1
            if len(examples[c]) < 5:
                examples[c].append({**_case_ref(it, case_id_field), "severity": sev,
                                    "rationale": label.get("rationale") or label.get("notes")})

    if not counts:
        return None
    ranked = counts.most_common()
    out = {
        "field": field,
        "categorised": sum(counts.values()),
        "distribution": dict(ranked),
        "ranked": [c for c, _ in ranked],
        "examples": {c: examples[c] for c, _ in ranked},
    }
    if by_severity:
        out["by_severity"] = {c: dict(by_severity[c]) for c, _ in ranked}
        if sev_order:
            serious = set(sev_order[-_LISTED_SEVERITIES:])
            out["serious_by_category"] = {
                c: sum(n for s, n in by_severity[c].items() if s in serious)
                for c, _ in ranked
            }
    return out


# ── Slices ───────────────────────────────────────────────────────────────────────

def slice_summary(items, keys, case_id_field=None, severity_cfg=None):
    """Performance broken down by a content field — clinical domain, case type, source.

    A single headline accuracy can hide a domain the model is unsafe in. This splits the
    same verdicts by whatever the operator declares, and reports each slice's own n so a
    thin slice cannot be read as a finding.
    """
    keys = [k for k in (keys or []) if k]
    if not keys:
        return None
    sev_field = (severity_cfg or {}).get("field") or "severity"
    sev_order = [str(o) for o in ((severity_cfg or {}).get("order") or [])]
    serious = set(sev_order[-_LISTED_SEVERITIES:]) if sev_order else set()

    out = {}
    for key in keys:
        buckets = defaultdict(lambda: {"n": 0, "correct": 0, "critical_miss": 0, "serious": 0})
        for it in _scored_items(items):
            content, label = it.get("content") or {}, it.get("label") or {}
            v = _norm(content.get(key))
            if v is None:
                continue
            b = buckets[v]
            b["n"] += 1
            verdict = _norm(label.get("verdict"))
            if verdict and verdict.lower() == "correct":
                b["correct"] += 1
            cm = label.get("critical_miss")
            if isinstance(cm, dict) and cm.get("present"):
                b["critical_miss"] += 1
            if serious and _norm(label.get(sev_field)) in serious:
                b["serious"] += 1
        if not buckets:
            continue
        out[key] = {
            name: {**b, "accuracy": (round(b["correct"] / b["n"], 3) if b["n"] else None)}
            for name, b in sorted(buckets.items(), key=lambda kv: -kv[1]["n"])
        }
    return out or None


# ── Entry point ──────────────────────────────────────────────────────────────────

def compute(items, analytics_cfg, case_id_field=None) -> dict | None:
    """Run whichever sections the project declared. Returns None when it declared none,
    so a project that never asked for clinical analytics gets its report unchanged."""
    cfg = analytics_cfg or {}
    if not isinstance(cfg, dict) or not cfg:
        return None
    sev_cfg = cfg.get("severity") or {}
    out = {}
    if cfg.get("triage"):
        r = triage_metrics(items, cfg["triage"], case_id_field)
        if r:
            out["triage"] = r
    if sev_cfg:
        r = severity_summary(items, sev_cfg, case_id_field)
        if r:
            out["severity"] = r
    if cfg.get("taxonomy"):
        r = taxonomy_summary(items, cfg["taxonomy"], sev_cfg, case_id_field)
        if r:
            out["taxonomy"] = r
    if cfg.get("slice_by"):
        r = slice_summary(items, cfg["slice_by"], case_id_field, sev_cfg)
        if r:
            out["slices"] = r
    return out or None


# ── Version comparison / regression ──────────────────────────────────────────────

# What counts as the model having passed a case. Everything else — Incorrect, Partial, or
# an unfinished item — is a failure for regression purposes: "Partial" means a clinician
# found something clinically meaningful wrong, and a benchmark must not let that pass.
_PASS = "correct"


def _passed(it) -> bool | None:
    """True/False if the case was scored, None if it was not (unfinished, or held for
    adjudication). None cases are reported as unscored rather than assumed either way."""
    if it.get("status") != "done":
        return None
    v = _norm((it.get("label") or {}).get("verdict"))
    if v is None:
        return None
    return v.lower() == _PASS


def _key(it, case_id_field=None):
    """The identity a case keeps across runs. Two evaluations of the same benchmark join on
    the CLIENT's case id — never on row position, which changes the moment a case is added
    or reordered."""
    content = it.get("content") or {}
    if case_id_field and content.get(case_id_field) is not None:
        return str(content[case_id_field])
    for k in ("case_id", "study_id", "accession"):
        if content.get(k) is not None:
            return str(content[k])
    return None


def _case_row(it, case_id_field=None):
    label = it.get("label") or {}
    return {
        "case_id": _key(it, case_id_field),
        "severity": _norm(label.get("severity")),
        "error_category": _norm(label.get("error_category")),
        "triage": _norm((it.get("content") or {}).get("triage")),
        "rationale": label.get("rationale") or label.get("notes"),
    }


def compare_runs(baseline, candidate, case_id_field=None, analytics_cfg=None) -> dict:
    """Regression report: run the same benchmark against two model versions and say what
    changed, case by case.

    Four buckets, and the asymmetry between two of them is the whole point:

      fixed      — failed in baseline, passes now. The improvement claim.
      regressed  — passed in baseline, fails now. A NEW break the release introduced, and
                   the thing a release gate must catch. Reported first and never netted
                   off against `fixed`: three fixes and one new critical regression is
                   not "net +2", it is a release you should not ship.
      still_failing / still_passing — unchanged.

    Cases are matched on the client's case id. Anything present in only one run is listed
    separately rather than being silently treated as an improvement or a loss, because a
    benchmark that quietly drops a case can manufacture a clean scorecard.
    """
    a_by = {}
    for it in baseline:
        k = _key(it, case_id_field)
        if k is not None:
            a_by[k] = it
    b_by = {}
    for it in candidate:
        k = _key(it, case_id_field)
        if k is not None:
            b_by[k] = it

    shared = sorted(set(a_by) & set(b_by))
    fixed, regressed, still_failing, still_passing, unscored = [], [], [], [], []

    for k in shared:
        pa, pb = _passed(a_by[k]), _passed(b_by[k])
        if pa is None or pb is None:
            unscored.append({"case_id": k,
                             "baseline": "unscored" if pa is None else ("pass" if pa else "fail"),
                             "candidate": "unscored" if pb is None else ("pass" if pb else "fail")})
            continue
        row = {**_case_row(b_by[k], case_id_field),
               "was": _case_row(a_by[k], case_id_field)}
        if pa and not pb:
            regressed.append(row)
        elif not pa and pb:
            fixed.append(row)
        elif pa and pb:
            still_passing.append({"case_id": k})
        else:
            still_failing.append(row)

    sev_order = [str(s) for s in ((analytics_cfg or {}).get("severity") or {}).get("order") or []]

    def _sev_rank(r):
        s = r.get("severity")
        return sev_order.index(s) if s in sev_order else -1

    # Worst regressions first: a critical new failure must be the first thing read.
    regressed.sort(key=lambda r: -_sev_rank(r))
    still_failing.sort(key=lambda r: -_sev_rank(r))

    def _rate(items):
        scored = [p for p in (_passed(i) for i in items) if p is not None]
        return (round(sum(scored) / len(scored), 3) if scored else None), len(scored)

    a_rate, a_n = _rate([a_by[k] for k in shared])
    b_rate, b_n = _rate([b_by[k] for k in shared])

    out = {
        "matched": len(shared),
        "only_in_baseline": sorted(set(a_by) - set(b_by)),
        "only_in_candidate": sorted(set(b_by) - set(a_by)),
        "unscored": unscored,
        "pass_rate": {"baseline": a_rate, "candidate": b_rate,
                      "delta": (round(b_rate - a_rate, 3) if (a_rate is not None and b_rate is not None) else None),
                      "scored": {"baseline": a_n, "candidate": b_n}},
        "regressed": {"count": len(regressed), "cases": regressed[:100]},
        "fixed": {"count": len(fixed), "cases": fixed[:100]},
        "still_failing": {"count": len(still_failing), "cases": still_failing[:100]},
        "still_passing": {"count": len(still_passing)},
    }

    # Headline clinical deltas, when both runs declared the same analytics.
    if analytics_cfg:
        a_clin = compute([a_by[k] for k in shared], analytics_cfg, case_id_field) or {}
        b_clin = compute([b_by[k] for k in shared], analytics_cfg, case_id_field) or {}
        deltas = {}
        at, bt = a_clin.get("triage"), b_clin.get("triage")
        if at and bt:
            deltas["triage_accuracy"] = {"baseline": at["accuracy"], "candidate": bt["accuracy"],
                                         "delta": round(bt["accuracy"] - at["accuracy"], 3)}
            for k2 in ("missed_emergency", "under_triage", "over_triage"):
                deltas[k2] = {"baseline": at[k2]["count"], "candidate": bt[k2]["count"],
                              "delta": bt[k2]["count"] - at[k2]["count"]}
        asv, bsv = a_clin.get("severity"), b_clin.get("severity")
        if asv and bsv:
            deltas["serious_errors"] = {"baseline": asv["serious"]["count"],
                                        "candidate": bsv["serious"]["count"],
                                        "delta": bsv["serious"]["count"] - asv["serious"]["count"]}
        if deltas:
            out["clinical_deltas"] = deltas

    # A verdict a release gate can act on. Any new regression blocks, regardless of the
    # headline rate: the aggregate can improve while the release breaks a case that
    # matters, and that is exactly what a regression suite exists to catch.
    serious = set(sev_order[-_LISTED_SEVERITIES:]) if sev_order else set()
    serious_regressions = [r for r in regressed if r.get("severity") in serious]
    out["verdict"] = {
        "regressions": len(regressed),
        "serious_regressions": len(serious_regressions),
        "recommendation": ("block" if serious_regressions else
                           "review" if regressed else
                           "pass"),
        "reason": (f"{len(serious_regressions)} case(s) that passed before now fail at "
                   f"{'/'.join(sorted(serious))} severity"
                   if serious_regressions else
                   f"{len(regressed)} case(s) that passed before now fail"
                   if regressed else
                   "no case that passed before fails now"),
    }
    return out
