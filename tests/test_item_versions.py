"""
Re-uploading never destroys work, from HEALTH's side: the portal upload route, the content
hash it compares, and the rule that a superseded version is history: never counted,
delivered or scored again. The database function itself is proved in
tests/sql/test_item_versions_sql.py.
"""
import asyncio
import io
from unittest.mock import patch

import pytest
from fastapi import HTTPException, UploadFile

from _fakedb import FakeDB
from app.services import scoring
from app.api.v1 import project as proj
from app.services import items
from app.services import report as report_svc
from app.services import storage

PID = "proj-v"


def test_a_fresh_signed_url_is_not_a_change_but_a_new_image_is():
    a = {"image": "https://x/sign?token=1", "_image_key": "k", "_image_sha256": "s1", "prediction": "N"}
    assert items.content_hash(a) == items.content_hash({**a, "image": "https://x/sign?token=2"})
    assert items.content_hash(a) != items.content_hash({**a, "_image_sha256": "s2"})
    assert items.content_hash(a) != items.content_hash({**a, "prediction": "A"})
    # Without an image hash the image URL is the content, and counts.
    text = {"image": "https://client/x.png", "prediction": "N"}
    assert items.content_hash(text) != items.content_hash({**text, "image": "https://client/y.png"})


def test_storage_keys_are_content_addressed_and_never_name_the_file():
    k1 = storage._deid_key("c" * 16, "patient-smith-chest.png", b"one")
    assert k1 == storage._deid_key("c" * 16, "other-name.png", b"one")      # same bytes, same object
    assert k1 != storage._deid_key("c" * 16, "patient-smith-chest.png", b"two")  # never overwrites
    assert "smith" not in k1 and k1.startswith("c" * 16 + "/")


def _upload(db, outcome, filename="scan.png"):
    db.rpcs["ingest_item_version"] = lambda p: [{"outcome": outcome, "item_id": "item-1", "task_no": 1_000_000_000,
                                                 "previous_item_id": "item-0" if outcome == "superseded" else None}]
    f = UploadFile(file=io.BytesIO(b"\x89PNG bytes"), filename=filename)
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "verify_token", return_value="c@x.com"), \
            patch.object(storage, "ensure_bucket"), \
            patch.object(storage, "upload_image_bytes", return_value="c" * 16 + "/" + "d" * 32 + ".png"), \
            patch.object(storage, "signed_url", return_value="https://x/sign?token=t"), \
            patch("app.services.audit.record") as audited:
        res = asyncio.run(proj.portal_upload_image(token="t", project_id=PID, idx=4, prediction="Normal",
                                                   study_id="", file=f))
    return res, audited


def _db():
    return FakeDB({"project_submissions": [{"id": PID, "email": "c@x.com"}], "project_items": []})


@pytest.mark.parametrize("outcome,says", [
    ("created", "Uploaded scan.png"),
    ("unchanged", "already uploaded; nothing changed"),
    ("updated", "Uploaded scan.png"),
    ("superseded", "kept on record and the new version will be reviewed in its place"),
])
def test_every_upload_outcome_is_reported_and_audited(outcome, says):
    db = _db()
    res, audited = _upload(db, outcome)
    assert res.ok and says in res.message
    (name, params), = db.rpc_calls
    assert name == "ingest_item_version" and params["p_idx"] == 4
    assert params["p_content"]["_image_key"] and params["p_content"]["_image_sha256"]
    assert audited.call_args.kwargs["value"]["outcome"] == outcome
    # The message to the client never mentions who reviewed anything.
    assert "clinician" not in res.message.lower()


def test_the_upload_never_deletes_a_row():
    db = _db()
    db.rows["project_items"] = [{"id": "item-0", "project_id": PID, "idx": 4, "status": "done"}]
    _upload(db, "superseded")
    assert db.rows["project_items"] == [{"id": "item-0", "project_id": PID, "idx": 4, "status": "done"}]


def test_uploads_pause_rather_than_destroy_when_the_migration_is_missing():
    db = _db()

    def missing(_):
        raise RuntimeError("PGRST202 Could not find the function public.ingest_item_version")
    with pytest.raises(HTTPException) as e:
        db.rpcs["ingest_item_version"] = missing
        f = UploadFile(file=io.BytesIO(b"x"), filename="a.png")
        with patch.object(proj, "get_client", return_value=db), \
                patch.object(proj, "verify_token", return_value="c@x.com"), \
                patch.object(storage, "ensure_bucket"), \
                patch.object(storage, "upload_image_bytes", return_value="k"), \
                patch.object(storage, "signed_url", return_value="u"):
            asyncio.run(proj.portal_upload_image(token="t", project_id=PID, idx=0, prediction="", study_id="", file=f))
    assert e.value.status_code == 503


# ── superseded versions are history ─────────────────────────────────────────────

def _project_db():
    return FakeDB({
        "project_submissions": [{"id": PID, "email": "c@x.com", "stage": "delivered", "eval_config": {}}],
        "project_items": [
            {"id": "old", "project_id": PID, "idx": 0, "status": "superseded", "version": 1,
             "content": {"prediction": "Normal"}, "label": {"verdict": "Correct"}, "labeled_at": "t"},
            {"id": "new", "project_id": PID, "idx": 0, "status": "done", "version": 2,
             "content": {"prediction": "Abnormal"}, "label": {"verdict": "Incorrect", "correct_label": "Normal"},
             "labeled_at": "t"},
        ],
    })


def test_a_superseded_version_is_not_counted_toward_delivery():
    assert proj._item_statuses(_project_db(), PID) == ["done"]


def test_a_superseded_version_is_not_delivered_or_scored():
    db = _project_db()
    with patch.object(proj, "get_client", return_value=db), \
            patch.object(proj, "verify_token", return_value="c@x.com"):
        res = proj.portal_results(token="t", project_id=PID)
    assert [i["content"]["prediction"] for i in res["items"]] == ["Abnormal"]
    rep = report_svc.build_report(db, PID)
    assert rep is None or "Correct" not in str([c.get("verdict") for c in rep.get("cases", [])])


def test_a_late_answer_does_not_rewrite_a_superseded_version():
    db = _project_db()
    task = {"id": 5, "project": 3, "data": {"_item_id": "old"},
            "annotations": [{"id": 1, "result": [{"from_name": "verdict", "value": {"choices": ["Partial"]}}],
                             "completed_by": {"email": "x"}}]}
    with patch("app.services.audit.record"):
        assert scoring.apply_task_annotations(db, task, 1, PID) == "superseded"
    assert db.rows["project_items"][0]["label"] == {"verdict": "Correct"}


def test_answers_given_before_supersession_are_still_scored_onto_the_old_version():
    db = _project_db()
    old = db.rows["project_items"][0]
    old.update({"label": None, "superseded_at": "2026-10-24T10:00:00+00:00"})
    before = {"id": 1, "created_at": "2026-10-24T09:59:00Z", "completed_by": {"email": "a"},
              "result": [{"from_name": "verdict", "value": {"choices": ["Correct"]}}]}
    after = {"id": 2, "created_at": "2026-10-24T10:05:00.12345+00:00", "completed_by": {"email": "b"},
             "result": [{"from_name": "verdict", "value": {"choices": ["Incorrect"]}}]}
    task = {"id": 5, "project": 3, "data": {"_item_id": "old"}, "annotations": [before, after]}
    with patch("app.services.audit.record"):
        status = scoring.apply_task_annotations(db, task, 1, PID)
    assert status == "superseded" and old["status"] == "superseded"
    assert old["label"]["verdict"] == "Correct"          # the answer given before, not the one after


def test_given_before_reads_both_stores_timestamps():
    cut = "2026-10-24T10:00:00+00:00"
    anns = [{"created_at": "2026-10-24T09:00:00Z"}, {"created_at": "2026-10-24T09:30:00.95209+00:00"},
            {"created_at": "2026-10-24T11:00:00Z"}, {"created_at": None}]
    assert items.given_before(anns, cut) == anns[:2]
    assert items.given_before(anns, None) == []
