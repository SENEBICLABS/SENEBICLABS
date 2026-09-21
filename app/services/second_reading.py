"""
Second reading: authored work is read by a second clinician before it counts.

Authored items (gold answers, benchmark questions, planted contradictions) are written by
one clinician, since prose cannot be majority-voted. Second reading is the check on that
one person: a second clinician reads each finished item and either approves it or sends it
back with a note. Only an approved item is `done`, so a project cannot be delivered while
anything is unread or being revised.

The loop, per item:

  author finishes  ->  status `second_reading`, a review task goes to the review project
  reader approves  ->  status `done`
  reader: Revise   ->  the author's task is replaced with a fresh one showing the reader's
                       note, with the previous draft pre-filled; status back to `pending`
  MAX_ROUNDS sends back without agreement -> `needs_adjudication`, for a senior reviewer

The review happens in a companion Label Studio project (a reader answers Approve / Revise,
which is a different form from the author's), created on first use and recorded on the
project as `_ls_review_project_id`.

State lives on the item's content under `_sr` (internal, never shown to a clinician or a
client): the current round, what state it is in, a hash of the text the reader was shown,
and the history of every reading. The hash is what makes this safe to re-run: a pull or a
repeated webhook for text already sent or approved changes nothing, and an author editing
their text after it was sent or approved sends the new text to be read.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone

from app.services import audit
from app.services import labelstudio as ls

logger = logging.getLogger(__name__)

STATUS = "second_reading"
APPROVE, REVISE = "Approve", "Revise"
MAX_ROUNDS = 3        # revisions allowed before the disagreement goes to a senior reviewer

READER_INSTRUCTIONS = (
    "GOAL: You are the second reader. Read the task the author was given and what they "
    "wrote, and decide whether it is ready to ship.\n\n"
    "APPROVE when it is clinically correct, complete for what was asked, safe, and does "
    "what the task asked for.\n\n"
    "REVISE when anything clinically material is wrong, missing or unsafe. Write the fix "
    "precisely: the author sees your note and revises their own draft, so say what must "
    "change, not only that something is wrong.\n\n"
    "Do not send back for style alone. Judge the clinical content."
)


def enabled(ec: dict | None) -> bool:
    return bool((ec or {}).get("second_reading"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _authored(label: dict, fields: dict) -> dict:
    """The authored values the reader must see: every declared field, in schema order."""
    return {name: (label or {}).get(name) for name in fields}


def _display(v) -> str:
    if v is None:
        return "(left blank)"
    if isinstance(v, list):
        return "\n\n— another version —\n\n".join(_display(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _hash(authored: dict) -> str:
    return hashlib.sha256(json.dumps(authored, sort_keys=True, default=str).encode()).hexdigest()


def review_config(ec: dict) -> dict:
    """The reader's form: the author's task context, then everything the author wrote,
    then Approve / Revise with a note."""
    schema = ec.get("schema") or {}
    fields = schema.get("fields") or {}
    context = [c for c in (schema.get("context") or []) if (c or {}).get("key")]
    authored = [{"key": f"authored_{name}",
                 "label": f"Author's {(f or {}).get('label') or name.replace('_', ' ')}"}
                for name, f in fields.items()]
    return {
        "title": f"Second reading — {ec.get('title') or 'authored work'}",
        "subtitle": "Approve it, or send it back with the fix.",
        "purpose": "label",
        "instructions": READER_INSTRUCTIONS,
        "schema": {
            "input": "text",
            "context": context + authored,
            "fields": {
                "decision": {"type": "single", "required": True, "options": [APPROVE, REVISE],
                             "label": "Is this ready to ship?"},
                "fix": {"type": "text", "rows": 5, "visible_when": f"decision=={REVISE}",
                        "label": "What must change",
                        "placeholder": "The specific fix the author must make"},
            },
        },
    }


def _ensure_review_project(db, project_id: str, ec: dict) -> int:
    ls_pid = ec.get("_ls_review_project_id")
    if ls_pid:
        return ls_pid
    ls_pid = ls.create_project(title=f"{ec.get('title') or 'Project'} — second reading",
                               label_config=ls.build_label_config(review_config(ec)), reviewers=1)
    # Re-read before writing, so a concurrent config change is not overwritten.
    cur = (db.table("project_submissions").select("eval_config").eq("id", project_id)
           .limit(1).execute()).data
    latest = (cur[0].get("eval_config") if cur else None) or ec
    latest["_ls_review_project_id"] = ls_pid
    db.table("project_submissions").update({"eval_config": latest}).eq("id", project_id).execute()
    ec["_ls_review_project_id"] = ls_pid
    return ls_pid


def on_authored(db, item: dict, label: dict, ec: dict, project_id: str) -> tuple[str, dict]:
    """The author's work on `item` is complete. Returns (status, content) to store.

    Idempotent: text already sent to a reader stays in second reading, and text already
    approved stays done. New or changed text is sent for reading."""
    content = dict(item.get("content") or {})
    sr = dict(content.get("_sr") or {})
    fields = (ec.get("schema") or {}).get("fields") or {}
    authored = _authored(label, fields)
    h = _hash(authored)

    if sr.get("hash") == h and sr.get("state") == "approved":
        return "done", content
    if sr.get("hash") == h and sr.get("state") == "reading":
        return STATUS, content

    rnd = int(sr.get("round") or 0) + 1
    ls_pid = _ensure_review_project(db, project_id, ec)
    data = {k: ls.render_value(v) for k, v in content.items() if not k.startswith("_")}
    for c in (ec.get("schema") or {}).get("context") or []:
        data.setdefault(c.get("key"), "")        # the reader's form shows every context block
    data.update({f"authored_{k}": _display(v) for k, v in authored.items()})
    data.update({"_item_id": item["id"], "_sr_round": rnd})

    new_content = dict(content)
    new_content["_sr"] = {**sr, "round": rnd, "state": "reading", "hash": h, "sent_at": _now()}
    new_content.pop("_revision", None)           # the revision it asked for has been made

    # Claim the item before sending it. The live webhook and a manual pull (or two quick
    # annotation events) can both arrive here for the same finished item; the status
    # transition is the lock, so exactly one of them sends it to a reader.
    prior_status = item.get("status")
    claim = db.table("project_items").update({"status": STATUS, "content": new_content}) \
        .eq("id", item["id"]).eq("status", prior_status).execute()
    if not claim.data:
        latest = (db.table("project_items").select("status,content").eq("id", item["id"])
                  .limit(1).execute()).data
        latest = latest[0] if latest else {"status": STATUS, "content": new_content}
        return latest.get("status"), latest.get("content") or {}
    try:
        ls.import_tasks(ls_pid, [{"data": data}])
    except Exception:
        # Not sent: release the claim, or the item would wait for a reader who never gets it.
        db.table("project_items").update({"status": prior_status, "content": content}) \
            .eq("id", item["id"]).execute()
        raise
    return STATUS, new_content


def on_review(db, task: dict, project_id: str | None) -> str | None:
    """A reader's decision arrived. Returns the item's new status, or None when there was
    nothing to do (a stale round, an item no longer in reading, or no decision yet)."""
    data = task.get("data") or {}
    item_id, rnd = data.get("_item_id"), data.get("_sr_round")
    anns = [a for a in (task.get("annotations") or []) if not a.get("was_cancelled")]
    if not item_id or rnd is None or not anns:
        return None
    from app.api.v1.ls import _parse_result      # lazy: ls imports this module
    ann = anns[-1]
    decision_label = _parse_result(ann.get("result") or [])
    decision = decision_label.get("decision")
    if decision not in (APPROVE, REVISE):
        return None
    cb = ann.get("completed_by")
    by = cb.get("email") if isinstance(cb, dict) else (ann.get("created_username") or "clinician")
    at = ann.get("created_at") or _now()

    row = (db.table("project_items").select("id,content,label,status")
           .eq("id", item_id).limit(1).execute()).data
    if not row:
        return None
    item = row[0]
    content = dict(item.get("content") or {})
    sr = dict(content.get("_sr") or {})
    if item.get("status") != STATUS or int(sr.get("round") or 0) != int(rnd):
        return None                              # superseded by a newer round, or already settled

    history = list(sr.get("history") or [])
    note = decision_label.get("fix")
    history.append({"round": rnd, "decision": decision, "note": note, "by": by, "at": at})
    sr["history"] = history
    label = dict(item.get("label") or {})

    if decision == APPROVE:
        sr["state"] = "approved"
        content["_sr"] = sr
        label["_second_reading"] = {"approved": True, "rounds": rnd, "by": by, "at": at}
        status = "done"
        db.table("project_items").update({"content": content, "label": label, "status": status}) \
            .eq("id", item_id).execute()
    elif rnd >= MAX_ROUNDS:
        # Author and reader have gone round MAX_ROUNDS times without agreeing: stop the loop
        # and put it in front of a senior reviewer, with the whole exchange on the item.
        sr["state"] = "escalated"
        content["_sr"] = sr
        label["_second_reading"] = {"approved": False, "rounds": rnd, "escalated": True,
                                    "history": history}
        status = "needs_adjudication"
        db.table("project_items").update({"content": content, "label": label, "status": status}) \
            .eq("id", item_id).execute()
    else:
        # Send it back. The author's finished task is removed (otherwise a pull would
        # re-apply the old draft), and a fresh one is queued showing the note, with the
        # previous draft pre-filled so the author edits rather than starts again.
        old_task = label.get("_ls_task_id")
        if old_task:
            ls.delete_task(old_task)
        sr["state"] = "revising"
        content["_sr"] = sr
        content["_revision"] = {"round": rnd, "note": note, "previous_result": label.get("_result")}
        status = "pending"
        db.table("project_items").update({"content": content, "label": None, "status": status,
                                          "labeled_by": None, "labeled_at": None}) \
            .eq("id", item_id).execute()

    audit.record(db, item_id=item_id, project_id=project_id, action=audit.LABEL, actor_id=by,
                 actor_name=by, source="second_reading",
                 value={"round": rnd, "decision": decision, "note": note})
    return status


def summary(items: list[dict]) -> dict | None:
    """Second-reading figures for the report: how much was read, how much went back."""
    read = [it for it in items if (it.get("content") or {}).get("_sr")]
    if not read:
        return None
    srs = [(it.get("content") or {})["_sr"] for it in read]
    approved = [s for s in srs if s.get("state") == "approved"]
    return {
        "items_read": len(read),
        "approved": len(approved),
        "approved_first_reading": sum(1 for s in approved if int(s.get("round") or 0) == 1),
        "sent_back_at_least_once": sum(1 for s in srs if any(h.get("decision") == REVISE
                                                              for h in s.get("history") or [])),
        "awaiting_reading": sum(1 for it in read if it.get("status") == STATUS),
        "being_revised": sum(1 for s in srs if s.get("state") == "revising"),
        "escalated": sum(1 for s in srs if s.get("state") == "escalated"),
    }
