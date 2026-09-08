"""
Clinical failure library + regression benchmark.

The evaluation report answers "how did this version do?". This answers the questions
that only exist ACROSS versions:

    "What does this model always get wrong?"
    "What are its most common high-severity failures in respiratory cases?"
    "Was case #47 ever actually fixed, or did it come back?"

A failure and a regression test are the same object at different points in its life —
a clinician finds a clinically meaningful failure, and that case becomes the permanent
test. So one row carries both: the original model input (re-runnable verbatim against a
future version), the clinician's assessment, and a status that moves
open -> fixed -> regressed as versions come and go.

The status transition is the point. `fixed` is only ever written when a later version
actually passed the case, and `regressed` when a case that was once fixed fails again.
That history is what makes the library worth more than a list of bad outputs, and it is
why a case is updated in place rather than re-inserted per evaluation.

Pure functions here take rows and return rows; the DB calls live in the API layer, so
everything below is testable without a database.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone

# A verdict a clinician gives that means "this output was clinically fine". Anything
# else — Incorrect, Partial — is a failure worth remembering: Partial means a clinician
# found something clinically meaningful wrong, and a benchmark must not let that pass.
PASS_VERDICT = "correct"

OPEN, FIXED, REGRESSED = "open", "fixed", "regressed"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(v):
    if isinstance(v, list):
        v = v[0] if v else None
    return None if v in (None, "") else str(v)


def _case_key(content, case_id_field=None):
    if case_id_field and content.get(case_id_field) is not None:
        return str(content[case_id_field])
    for k in ("case_id", "study_id", "accession"):
        if content.get(k) is not None:
            return str(content[k])
    return None


def passed(item) -> bool | None:
    """True/False if a clinician scored it, None if unfinished or held for adjudication.
    An unresolved split is not evidence either way and must not flip a case's status."""
    if item.get("status") != "done":
        return None
    v = _norm((item.get("label") or {}).get("verdict"))
    if v is None:
        return None
    return v.lower().startswith(PASS_VERDICT)


def extract(items, *, client_email, project_id, model_version=None, case_id_field=None):
    """Turn one evaluation's reviewed items into failure records.

    Only scored failures are extracted. A case with no verdict, or one held for
    adjudication, is skipped rather than recorded as a failure — the library is
    evidence, and an unresolved item is not evidence.
    """
    out = []
    for it in items:
        if passed(it) is not False:      # None (unscored) or True (passed) -> not a failure
            continue
        content = it.get("content") or {}
        label = it.get("label") or {}
        key = _case_key(content, case_id_field)
        if key is None:
            continue                     # no stable identity -> cannot be re-run later
        out.append({
            "client_email": client_email,
            "case_key": key,
            "project_id": project_id,
            "model_version": model_version,
            "clinical_domain": _norm(content.get("clinical_domain")),
            "case_type": _norm(content.get("case_type")),
            "severity": _norm(label.get("severity")),
            "error_category": _norm(label.get("error_category")),
            "expected_triage": _norm(label.get("correct_triage")) or _norm(content.get("expected_triage")),
            "model_triage": _norm(content.get("triage")),
            # The whole input, so the case can be replayed verbatim against a new version.
            "content": content,
            "output": {k: content.get(k) for k in ("prediction", "output", "response") if content.get(k) is not None},
            "assessment": {k: v for k, v in label.items() if not k.startswith("_")},
            "rationale": label.get("rationale") or label.get("notes"),
            "status": OPEN,
        })
    return out


def resolved(items, *, case_id_field=None):
    """Case keys that PASSED in this evaluation. Used to close out library entries a new
    version has fixed — the other half of capture, and the half that makes `fixed` mean
    something rather than being set by hand."""
    keys = set()
    for it in items:
        if passed(it) is True:
            k = _case_key(it.get("content") or {}, case_id_field)
            if k:
                keys.add(k)
    return keys


def merge(existing: dict | None, incoming: dict, model_version=None) -> dict:
    """How a newly-seen failure updates what the library already knows.

    A case that was previously `fixed` and fails again becomes `regressed`, never plain
    `open` — losing that distinction would hide the most alarming thing a library can
    tell you, which is that a fix did not hold.
    """
    now = _now()
    if not existing:
        return {**incoming, "occurrences": 1, "first_seen": now, "last_seen": now,
                "updated_at": now}
    status = REGRESSED if existing.get("status") == FIXED else existing.get("status") or OPEN
    return {
        **incoming,
        "status": status,
        "in_benchmark": existing.get("in_benchmark", False),
        "occurrences": int(existing.get("occurrences") or 0) + 1,
        "first_seen": existing.get("first_seen") or now,
        "last_seen": now,
        # Cleared: it is failing again, so the version that "fixed" it no longer holds.
        "fixed_in_version": None,
        "updated_at": now,
    }


def close(existing: dict, model_version=None) -> dict:
    """A later version passed this case. Record which version, and keep it in the
    benchmark — a fixed case is exactly the one you want to keep testing, because that
    is how you find out if the fix survives the next release."""
    now = _now()
    return {"status": FIXED, "fixed_in_version": model_version,
            "last_seen": now, "updated_at": now}


def patterns(rows) -> dict:
    """Aggregate the library — the "what does this model always get wrong" view.

    Reports counts by severity, failure mode, and clinical domain, plus the crossings
    that carry the signal: which domains hold the serious failures, and which failure
    modes are still open rather than fixed.
    """
    rows = list(rows or [])
    if not rows:
        return {"total": 0}
    sev = Counter(r.get("severity") or "ungraded" for r in rows)
    cat = Counter(r.get("error_category") or "uncategorised" for r in rows)
    dom = Counter(r.get("clinical_domain") or "unspecified" for r in rows)
    status = Counter(r.get("status") or OPEN for r in rows)

    serious = {"High", "Critical"}
    by_domain_serious = Counter(
        (r.get("clinical_domain") or "unspecified") for r in rows if r.get("severity") in serious)
    open_by_category = Counter(
        (r.get("error_category") or "uncategorised") for r in rows if r.get("status") != FIXED)
    # A case that was fixed and broke again is the strongest signal in the library.
    regressed = [{"case_key": r.get("case_key"), "severity": r.get("severity"),
                  "error_category": r.get("error_category"),
                  "clinical_domain": r.get("clinical_domain"),
                  "occurrences": r.get("occurrences")}
                 for r in rows if r.get("status") == REGRESSED]

    return {
        "total": len(rows),
        "by_status": dict(status),
        "by_severity": dict(sev.most_common()),
        "by_error_category": dict(cat.most_common()),
        "by_clinical_domain": dict(dom.most_common()),
        "serious_by_domain": dict(by_domain_serious.most_common()),
        "open_by_error_category": dict(open_by_category.most_common()),
        "regressed": {"count": len(regressed), "cases": regressed[:50]},
        "in_benchmark": sum(1 for r in rows if r.get("in_benchmark")),
        "recurring": [
            {"case_key": r.get("case_key"), "occurrences": r.get("occurrences"),
             "severity": r.get("severity"), "status": r.get("status")}
            for r in sorted(rows, key=lambda r: -(r.get("occurrences") or 0))
            if (r.get("occurrences") or 0) > 1
        ][:25],
    }


def as_items(rows, *, start_idx=0):
    """Turn benchmark rows back into ingestable project items, so a suite can be re-run
    against a new version. The stored `content` is replayed verbatim: the point of a
    regression test is that the input does not drift between runs."""
    items = []
    for i, r in enumerate(rows or []):
        content = dict(r.get("content") or {})
        content.setdefault("case_id", r.get("case_key"))
        items.append({"idx": start_idx + i, "content": content, "status": "pending"})
    return items


def client_view(row) -> dict:
    """One library row as the client sees it. Internal ids stay internal."""
    return {
        "case_key": row.get("case_key"),
        "status": row.get("status"),
        "severity": row.get("severity"),
        "error_category": row.get("error_category"),
        "clinical_domain": row.get("clinical_domain"),
        "case_type": row.get("case_type"),
        "expected_triage": row.get("expected_triage"),
        "model_triage": row.get("model_triage"),
        "model_version": row.get("model_version"),
        "fixed_in_version": row.get("fixed_in_version"),
        "in_benchmark": row.get("in_benchmark"),
        "occurrences": row.get("occurrences"),
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "content": row.get("content"),
        "output": row.get("output"),
        "assessment": row.get("assessment"),
        "rationale": row.get("rationale"),
    }
