"""
Scoring: turn a task's annotations into its item's label and status.

Shared by every task store. A Label Studio task (the manual pull and the live webhook in
api/v1/ls.py) and a native task (services/tasks.py) arrive in the same shape, with the
answers in Label Studio's result format, so parsing, consensus, adjudication holds,
second reading and the audit record are identical for both. Only where the pool is found,
and what the task id is called, differ.

Nothing here talks to Label Studio.
"""
from collections import Counter

from app.services import audit
from app.services import items as items_svc
from app.services import second_reading


def collapse_structured(label: dict) -> dict:
    """Collapse a 'structured' field's two Label Studio controls into one object.

    build_label_config renders a structured field (e.g. critical_miss) as a Yes/No flag
    plus a '<name>_finding' picker. Those arrive as two flat sibling keys; here we merge
    them into label[name] = {"present": bool, "finding": <class or None>} so a critical
    miss lands as structured data, not two disconnected keys. Handles an orphan finding
    (finding chosen but flag left blank) by reporting present=None.
    """
    for fk in [k for k in list(label) if k != "_result" and k.endswith("_finding")]:
        base = fk[: -len("_finding")]
        finding = label.pop(fk)
        flag = label.get(base)
        present = (flag == "Yes") if isinstance(flag, str) else flag
        label[base] = {"present": present, "finding": finding}
    return label


def parse_result(result: list) -> dict:
    """Flatten a Label Studio annotation result into a label dict (any task type)."""
    label: dict = {"_result": result}
    for r in result:
        fn = r.get("from_name")
        v = r.get("value", {}) or {}
        if "rating" in v:
            val = v["rating"]
        elif "number" in v:
            val = v["number"]
        elif "choices" in v:
            c = v["choices"]
            val = c[0] if isinstance(c, list) and len(c) == 1 else c
        elif "labels" in v:
            # A highlighted span. Checked before "text" because a span also carries the
            # highlighted `text`; read as a text answer it lost its start/end and its tag.
            val = {"labels": v.get("labels"), "start": v.get("start"), "end": v.get("end"), "text": v.get("text")}
        elif "text" in v:
            t = v["text"]
            val = t[0] if isinstance(t, list) and len(t) == 1 else t
        else:
            val = v
        if fn in label and fn != "_result":
            if not isinstance(label[fn], list):
                label[fn] = [label[fn]]
            label[fn].append(val)
        else:
            label[fn] = val
    return collapse_structured(label)


def is_span_value(x) -> bool:
    """True if a value is an in-text span highlight, or a list of them (start/end + labels,
    not a structured yes/no). Spans are preserved across reviewers, never majority-voted."""
    if isinstance(x, dict):
        return ("start" in x or "labels" in x) and "present" not in x
    if isinstance(x, list):
        return any(isinstance(e, dict) and ("start" in e or "labels" in e) for e in x)
    return False


def primary_field(ec: dict | None) -> str:
    """The field whose spread across reviewers defines agreement for a project. Declared as
    schema.primary_field; failing that, the first required choice field (every task schema has
    one — it is the answer the task exists to collect); failing that, 'verdict', the historical
    default from when every project was an X-ray evaluation."""
    schema = (ec or {}).get("schema") or {}
    declared = schema.get("primary_field")
    if declared:
        return str(declared)
    for name, f in (schema.get("fields") or {}).items():
        if (f or {}).get("type") in ("single", "from_classes") and (f or {}).get("required"):
            return name
    return "verdict"


def consensus(labels: list[dict], text_fields: set | None = None,
              primary: str = "verdict") -> tuple[dict, float, bool]:
    """Combine N reviewer labels into a majority-vote consensus, plus the agreement
    on the `primary` field (fraction of reviewers who chose the top answer) and whether
    they disagreed (no strict majority). Structured dict fields like
    critical_miss are voted on `present` + majority finding. Free-text fields (named in
    `text_fields`) are NEVER voted — two clinicians can write the same correction in
    different words, so every distinct version is surfaced rather than one being silently
    picked and the rest hidden. Prose is reviewed, not merged."""
    text_fields = text_fields or set()
    n = len(labels) or 1
    verdicts = [str(l.get(primary)) for l in labels if l.get(primary) is not None]
    if verdicts:
        _top, top_n = Counter(verdicts).most_common(1)[0]
        agreement = top_n / n
        disagreed = top_n * 2 <= n
    else:
        # The primary field resolved to nothing (misconfigured schema, or a task whose answer
        # is entirely free text). We cannot measure a split, so we must not manufacture one:
        # defaulting to "disagreed" would route every unanimous item to adjudication and
        # exclude it from the client's metrics.
        agreement, disagreed = 0.0, False
    consensus: dict = {}
    keys = {k for l in labels for k in l if not k.startswith("_")}
    for k in keys:
        vals = [l.get(k) for l in labels if l.get(k) is not None]
        if not vals:
            continue
        # In-text spans don't vote — keep the union of every reviewer's highlights.
        if any(is_span_value(v) for v in vals):
            merged: list = []
            for v in vals:
                merged.extend(v if isinstance(v, list) else [v])
            consensus[k] = merged
            continue
        # A structured field only becomes a dict when the reviewer picked a finding,
        # and a reviewer answering "No" never picks one, so their answer stays the
        # bare string. That mixture skipped the majority rule below and fell through
        # to a plain string vote, where a one-to-one tie went to whichever answer was
        # read first. One reviewer in three could therefore set critical_miss present
        # on a delivered result. Normalise the flag answers to the same shape so the
        # majority rule actually applies.
        if any(isinstance(v, dict) and "present" in v for v in vals):
            vals = [
                v if isinstance(v, dict)
                else {"present": True, "finding": None} if v == "Yes"
                else {"present": False, "finding": None} if v == "No"
                else v
                for v in vals
            ]
        if all(isinstance(v, dict) for v in vals):
            present = sum(1 for v in vals if v.get("present")) * 2 > n
            findings = [v.get("finding") for v in vals if v.get("present") and v.get("finding")]
            consensus[k] = {"present": present,
                            "finding": (Counter(findings).most_common(1)[0][0] if (present and findings) else None)}
        elif k in text_fields:
            # Free text isn't votable — keep every distinct version, never hide one behind a
            # majority pick. One version -> that string; several -> the list of all of them.
            distinct, seen = [], set()
            for v in vals:
                s = str(v)
                if s not in seen:
                    seen.add(s)
                    distinct.append(v)
            consensus[k] = distinct[0] if len(distinct) == 1 else distinct
        else:
            by_str = {str(v): v for v in vals}          # keep original value, vote by string
            top_key = Counter(str(v) for v in vals).most_common(1)[0][0]
            consensus[k] = by_str[top_key]
    return consensus, round(agreement, 3), disagreed


# ── a project's scoring configuration ────────────────────────────────────────────

def ls_project_of(db, project_id: str) -> int | None:
    try:
        r = (db.table("project_submissions").select("ls_project_id").eq("id", project_id)
             .limit(1).execute()).data
        return r[0].get("ls_project_id") if r else None
    except Exception:
        return None


def text_field_names(ec: dict | None) -> set:
    """Names of the free-text fields in a config's schema. These are surfaced per-reviewer in
    the consensus, never majority-voted — written prose can't be merged by string match."""
    fields = ((ec or {}).get("schema") or {}).get("fields") or {}
    return {name for name, f in fields.items() if (f or {}).get("type") == "text"}


def reviewers_target(db, project_id: str | None) -> int:
    if not project_id:
        return 1
    try:
        sub = db.table("project_submissions").select("eval_config").eq("id", project_id).limit(1).execute()
        return int(((sub.data[0].get("eval_config") if sub.data else None) or {}).get("reviewers_per_item") or 1)
    except Exception:
        return 1


def qa_settings(db, project_id: str | None) -> tuple[int, bool, set, str]:
    """(reviewers_target, adjudicate, text_fields, primary_field) for a project, read in one
    query. `adjudicate` (opt-in via eval_config.adjudicate) holds a disagreed item for a senior
    reviewer instead of shipping the majority vote. `text_fields` are surfaced per-reviewer,
    never voted. `primary_field` is the answer whose spread defines agreement. Off by
    default, so existing projects behave exactly as before."""
    return qa_from_ec(project_ec(db, project_id))


def project_ec(db, project_id: str | None) -> dict:
    if not project_id:
        return {}
    try:
        sub = db.table("project_submissions").select("eval_config").eq("id", project_id).limit(1).execute()
        return (sub.data[0].get("eval_config") if sub.data else None) or {}
    except Exception:
        return {}


def qa_from_ec(ec: dict) -> tuple[int, bool, set, str]:
    return (int(ec.get("reviewers_per_item") or 1), bool(ec.get("adjudicate")),
            text_field_names(ec), primary_field(ec))


# ── scoring one task ─────────────────────────────────────────────────────────────

def apply_task_annotations(db, task: dict, reviewers_target: int, project_id: str | None,
                           adjudicate: bool = False, text_fields: set | None = None,
                           primary: str = "verdict", ec: dict | None = None,
                           sr_wait: float = 0.0) -> str | None:
    """Write a task's annotations to its item: for one reviewer the label is stored
    as-is; for many, a majority consensus + agreement is stored. The item is `done` only
    once `reviewers_target` reviewers are in. When `adjudicate` is set and the reviewers
    disagreed (no majority), the item is held as `needs_adjudication` instead of `done`, so
    an unresolved split is never shipped as a majority-vote answer. Returns the new status
    string ('done' / 'needs_adjudication' / 'in_progress'), or None if skipped. Shared by the
    Label Studio pull and webhook and by the native store, so all handle overlap identically."""
    item_id = (task.get("data") or {}).get("_item_id")
    anns = task.get("annotations") or []
    if not item_id or not anns:
        return None
    cur = (db.table("project_items").select("id,content,label,status")
           .eq("id", item_id).limit(1).execute()).data
    current = cur[0] if cur else {"id": item_id}
    # A senior reviewer's resolution is final. Re-reading the task (a manual pull, or a
    # late annotation event) would otherwise rebuild the original split from the reviewers'
    # annotations and put a settled item back in the adjudication queue.
    if (current.get("label") or {}).get("_adjudicated"):
        return current.get("status")
    # A superseded version is history: its content was replaced after work began. Answers
    # given before that still belong to it and are scored onto it (so the record shows what
    # they concluded), but its status stays superseded and it is never delivered. An answer
    # given after it was replaced (Label Studio may still hold the old task) is ignored.
    superseded = current.get("status") == items_svc.SUPERSEDED
    if superseded:
        cut = (db.table("project_items").select("superseded_at").eq("id", item_id)
               .limit(1).execute()).data
        anns = items_svc.given_before(anns, (cut[0] if cut else {}).get("superseded_at"))
        if not anns:
            return items_svc.SUPERSEDED
    # A task from the native store (services/tasks.py) arrives in this same shape, with the
    # answers in the same result format, so everything below is indifferent to which store
    # it came from. Only where the pool is found, and what the task id is called, differ.
    native = bool(task.get("_native"))
    # Authored work under second reading counts only once the clinician platform records
    # that a different clinician approved it, and only the approved annotation is the
    # answer (services/second_reading.py).
    sr = project_id and second_reading.enabled(ec)
    if sr:
        if native:
            sr_status, sr_record, sr_row = second_reading.decide(
                db, None, task.get("id"), sr_wait, project_id=project_id)
        else:
            sr_status, sr_record, sr_row = second_reading.decide(
                db, task.get("project") or ls_project_of(db, project_id), task.get("id"), sr_wait)
        anns = second_reading.approved_annotations(anns, sr_row)
    parsed = []
    for a in anns:
        cb = a.get("completed_by")
        who = cb.get("email") if isinstance(cb, dict) else (a.get("created_username") or "clinician")
        lbl = parse_result(a.get("result", []))
        parsed.append({"by": who, "at": a.get("created_at") or a.get("updated_at"),
                       "label": lbl})
    if len(parsed) == 1:
        label = dict(parsed[0]["label"])
    else:
        agreed, agreement, disagreed = consensus([p["label"] for p in parsed], text_fields, primary)
        label = {
            **agreed,
            "_result": parsed[0]["label"].get("_result"),
            "_reviewers": len(parsed),
            "_agreement": agreement,
            "_disagreed": disagreed,
            "_annotations": [{"by": p["by"], "at": p["at"],
                              "label": {k: v for k, v in p["label"].items() if k != "_result"}}
                             for p in parsed],
        }
    # Bridge to the workforce system of record, whose task_completions are keyed by this
    # task id: the native task number, or the Label Studio task id for work done there
    # (where every annotation carries the one LS service user, so this join is the only
    # per-clinician attribution). Read back through tasks.task_ref.
    if native:
        label["_task_no"] = task.get("id")
    else:
        label["_ls_task_id"] = task.get("id")
    reached = len(parsed) >= reviewers_target
    disagreed = isinstance(label, dict) and label.get("_disagreed")
    if reached and adjudicate and len(parsed) > 1 and disagreed:
        status = "needs_adjudication"
    elif reached:
        status = "done"
    else:
        status = "in_progress"
    update = {"label": label, "status": status,
              "labeled_by": parsed[-1]["by"], "labeled_at": parsed[-1]["at"]}
    if sr and status == "done":
        status = sr_status
        if sr_record:
            label["_second_reading"] = sr_record
        update["status"] = status
    if superseded:
        # Its conclusion is recorded; it stays history, out of every count and delivery.
        status = update["status"] = items_svc.SUPERSEDED
    db.table("project_items").update(update).eq("id", item_id).execute()
    audit.record(db, item_id=item_id, project_id=project_id, action=audit.LABEL,
                 actor_id=parsed[-1]["by"], actor_name=parsed[-1]["by"],
                 source="native" if native else "label_studio", value=label)
    return status
