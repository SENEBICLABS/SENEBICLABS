"""
Project intake API — companies submit data-annotation projects.

POST /project/submit  — a company submits a project (stored in Supabase, emails fired)
"""

import logging
import secrets
import hmac
import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Header, File, Form, UploadFile
from pydantic import BaseModel, EmailStr

from app.core.config import settings
from app.services.supabase_client import get_client
from app.services import email_service
from app.services import audit
from app.services import report as report_svc
from app.services import storage
from app.services.portal_tokens import make_token, make_api_key, verify_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/project", tags=["Project Intake"])

# The six customer-facing phases, in order. `stage` on a submission is one of these.
STAGES = ["submitted", "scoping", "agreement", "pilot", "production", "delivered"]

# How long a claimed-but-unsubmitted item is held before it returns to the pool.
CLAIM_TTL_MINUTES = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_PURPOSES = ("evaluate", "label", "create")


def _normalize_purpose(ec: dict) -> str:
    """The project's declared purpose, normalised. Unknown/absent -> 'evaluate' (the default,
    so existing projects keep working)."""
    p = str((ec or {}).get("purpose") or "evaluate").strip().lower()
    return p if p in _PURPOSES else "evaluate"


def _default_reviewers(ec: dict) -> int:
    """Reviewer default: judgments get N-way consensus; PURE free-text creation (gold answers,
    dialogues, authored prompts) is written by a single expert, since you can't majority-vote
    prose. A 'create' task that still makes a categorical pick — a preference/ranking — gets
    consensus. Operator can override per project via POST /admin/reviewers."""
    fields = ((ec or {}).get("schema") or {}).get("fields") or {}
    required = [f for f in fields.values() if (f or {}).get("required")]
    authoring = (_normalize_purpose(ec) == "create" and required
                 and all((f or {}).get("type") in ("text", "spans") for f in required))
    return 1 if authoring else int(settings.DEFAULT_REVIEWERS_PER_ITEM)


def _stale(iso: str | None, minutes: int) -> bool:
    """True if an ISO timestamp is older than `minutes` ago (or missing/unparseable).
    Used to decide whether a background manifest ingest looks stuck and should be re-fired."""
    if not iso:
        return True
    try:
        t = datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except Exception:
        return True
    return (datetime.now(timezone.utc) - t) > timedelta(minutes=minutes)


# The parts of an eval_config that define HOW an item is judged — the guideline and the task
# layout. Once a project has reviewed items these are frozen (see admin_set_eval_config), so a
# mid-batch change can't silently split the data into items judged by different rules. Everything
# NOT listed here — reviewer count, adjudicate, auto_deliver, internal _-markers — stays editable.
_GRADING_KEYS = ("purpose", "instructions", "title", "subtitle")
_GRADING_SCHEMA_KEYS = ("input", "context", "classes", "case_id_field", "media_key", "fields")


def _grading_signature(ec: dict) -> dict:
    """The judgment-defining slice of a config, for comparing whether an edit changes how items
    are graded (vs only an operational setting)."""
    ec = ec or {}
    schema = ec.get("schema") or {}
    sig = {k: ec.get(k) for k in _GRADING_KEYS}
    sig["schema"] = {k: schema.get(k) for k in _GRADING_SCHEMA_KEYS}
    return sig


def _project_has_reviews(db, project_id: str) -> bool:
    """True once any item has been reviewed — a clinician has produced data under the current
    config, so the grading config is frozen for batch consistency."""
    try:
        r = (db.table("project_items").select("id")
             .eq("project_id", project_id)
             .in_("status", ["in_progress", "done", "needs_adjudication"])
             .limit(1).execute())
        return bool(r.data)
    except Exception:
        return False


def _resolve_labeler(db, code: str | None) -> dict | None:
    """Resolve a work access code to a labeler.

    The operator's admin key works (id='admin'); otherwise the code must match an
    active clinician's access_code. Returns {'id', 'name'} or None if unauthorised.
    """
    if not code:
        return None
    if settings.ADMIN_API_KEY and code == settings.ADMIN_API_KEY:
        return {"id": "admin", "name": "Operator"}
    try:
        rows = db.table("clinicians").select("id,name,active").eq("access_code", code).limit(1).execute()
    except Exception as exc:
        logger.error("Clinician lookup failed: %s", exc)
        return None
    if rows.data and rows.data[0].get("active", True):
        c = rows.data[0]
        return {"id": c["id"], "name": c["name"]}
    return None


def _labeler_can_access(db, labeler: dict, project_id: str) -> bool:
    """Slice #2 isolation: a clinician may only touch projects they're assigned to
    (via project_clinicians). The operator (admin) sees everything. Fails CLOSED —
    if the assignment can't be verified, access is denied. Project creation alone no
    longer grants access; a clinician must be assigned. See README onboarding.
    """
    if labeler.get("id") == "admin":
        return True
    try:
        r = (
            db.table("project_clinicians").select("project_id")
            .eq("project_id", project_id).eq("clinician_id", labeler["id"]).limit(1).execute()
        )
        return bool(r.data)
    except Exception as exc:
        logger.error("Access check failed (denying): %s", exc)
        return False


# ── Schemas ────────────────────────────────────────────────────────────────────

class ProjectSubmission(BaseModel):
    name: str
    email: EmailStr
    company: str
    description: str
    data_type: str | None = None
    task_type: str | None = None
    volume: str | None = None
    timeline: str | None = None
    data_sensitivity: str | None = None
    sample_link: str | None = None
    budget_notes: str | None = None


class SubmissionResponse(BaseModel):
    ok: bool
    message: str


class PortalRequest(BaseModel):
    email: EmailStr


class AdminAdvance(BaseModel):
    submission_id: str
    stage: str
    note: str | None = None


class ItemsIn(BaseModel):
    project_id: str
    items: list[dict]          # list of content dicts, e.g. {"prompt": ..., "output": ...}


class LabelIn(BaseModel):
    item_id: str
    label: dict                # e.g. {"score": 4, "unsafe": false, "rationale": "..."}


class SkipIn(BaseModel):
    item_id: str


class ProjectMetaIn(BaseModel):
    project_id: str
    rate_per_item: float | None = None
    difficulty: str | None = None


class ReviewersIn(BaseModel):
    project_id: str
    reviewers_per_item: int


class AdjudicateIn(BaseModel):
    project_id: str
    idx: int                         # the item's stable idx (as shown in the adjudication queue)
    final_label: dict                # the senior reviewer's resolving answer, e.g. {"verdict": "Accurate"}
    note: str | None = None


class ClinicianIn(BaseModel):
    name: str
    email: EmailStr | None = None


class EvalConfigIn(BaseModel):
    project_id: str
    eval_config: dict
    force: bool = False              # override the guideline-lock on a project that already has reviews


class AssignClinicianIn(BaseModel):
    project_id: str
    clinician_id: str


class SourceIn(BaseModel):
    manifest_url: str                # a JSONL manifest in the client's storage (e.g. a pre-signed S3 URL)
    sample: int | None = None        # review a random sample of this many items (default/cap applied)
    mode: str = "sample"             # "sample" = a bounded random sample (evaluate); "all" = every item (label everything)


class IngestIn(BaseModel):
    project_id: str
    items: list[dict] | None = None  # inline items (small batches)
    source: SourceIn | None = None   # OR a manifest pointing at bulk data in the client's storage
    webhook_url: str | None = None   # optional: we POST results here when the batch is delivered


class IngestSourceIn(BaseModel):
    project_id: str
    manifest_url: str
    sample: int | None = None
    key: str | None = None           # the batch's idempotency key — recorded on success so retries skip
    mode: str = "sample"             # "sample" = reservoir-sample; "all" = ingest every item (exhaustive)


class CreateProjectIn(BaseModel):
    name: str
    # Pick an outcome template (and optionally your `classes`) OR hand-author `eval_config`.
    template: str | None = None      # e.g. "model_evaluation" | "data_labeling" | "rlhf_preference" | "gold_answers"
    classes: list[str] | None = None # your label set, when a template needs one
    eval_config: dict | None = None  # full custom config (advanced) — the task schema you define
    webhook_url: str | None = None


class ApiKeyIn(BaseModel):
    project_id: str | None = None    # issue a key from an existing project's email…
    email: str | None = None         # …or directly for an account email (self-serve, no project yet)


class DevLinkIn(BaseModel):
    email: EmailStr                  # self-serve: email a sign-in link to the developer console


class KeyCreateIn(BaseModel):
    token: str                       # the magic-link token that proves the email
    label: str | None = None         # optional human label ("production", "staging")


class KeyRevokeIn(BaseModel):
    token: str
    key_id: str


def _api_client_email(authorization: str | None) -> str | None:
    """Resolve the client email from an `Authorization: Bearer <api_key>` header
    (also accepts the bare key). Returns None if missing, invalid, or revoked."""
    if not authorization:
        return None
    parts = authorization.split()
    token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else authorization
    email = verify_token(token)
    if not email:
        return None
    from app.services import api_keys
    if api_keys.is_revoked(get_client(), token):
        return None
    return email


# ── Submit ─────────────────────────────────────────────────────────────────────

@router.post("/submit", response_model=SubmissionResponse, summary="Submit an annotation project")
def submit(submission: ProjectSubmission):
    db = get_client()
    if db is None:
        logger.warning("Project submission ignored — Supabase not configured (email=%s)", submission.email)
        raise HTTPException(
            status_code=503,
            detail="Submissions are temporarily unavailable. Please try again shortly.",
        )

    try:
        db.table("project_submissions").insert(
            {
                "name": submission.name,
                "email": submission.email.lower(),
                "company": submission.company,
                "description": submission.description,
                "data_type": submission.data_type,
                "task_type": submission.task_type,
                "volume": submission.volume,
                "timeline": submission.timeline,
                "data_sensitivity": submission.data_sensitivity,
                "sample_link": submission.sample_link,
                "budget_notes": submission.budget_notes,
                "status": "new",
            }
        ).execute()
    except Exception as exc:
        logger.error("Project submission insert failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save your submission. Please try again.")

    # Emails — fire after DB write succeeds
    email_service.send_project_submission_confirmation(
        name=submission.name,
        email=submission.email,
    )
    email_service.send_project_submission_admin_alert(submission=submission)

    logger.info("Project submission received: %s (%s)", submission.company, submission.email)
    return SubmissionResponse(
        ok=True,
        message="Submission received. We will be in touch within one business day to scope your pilot.",
    )


# ── Customer portal ────────────────────────────────────────────────────────────

@router.post("/portal/request", response_model=SubmissionResponse, summary="Email a portal sign-in link")
def portal_request(req: PortalRequest):
    """Email a magic-link to a company that has submitted a project.

    Always returns ok (even if no project exists for the email) so the endpoint
    never reveals whether an address is in the system.
    """
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="The portal is temporarily unavailable. Please try again shortly.")

    email = req.email.lower()
    try:
        rows = db.table("project_submissions").select("id").eq("email", email).limit(1).execute()
    except Exception as exc:
        logger.error("Portal request lookup failed: %s", exc)
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

    if rows.data:
        link = f"{settings.SITE_URL.rstrip('/')}/portal?token={make_token(email)}"
        email_service.send_portal_link(email=email, link=link)
        logger.info("Portal link sent to %s", email)
    else:
        logger.info("Portal request for unknown email %s (no link sent)", email)

    return SubmissionResponse(ok=True, message="If that email has a project with us, a sign-in link is on its way.")


@router.get("/portal/projects", summary="List a company's projects (magic-link token)")
def portal_projects(token: str):
    email = verify_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="This link has expired or is invalid. Request a new one.")

    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="The portal is temporarily unavailable. Please try again shortly.")

    try:
        rows = (
            db.table("project_submissions")
            .select("id,company,description,task_type,stage,stage_note,created_at")
            .eq("email", email)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error("Portal projects fetch failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not load your projects. Please try again.")

    subs = rows.data or []
    counts: dict[str, dict[str, int]] = {}
    if subs:
        try:
            its = db.table("project_items").select("project_id,status").in_("project_id", [s["id"] for s in subs]).execute()
            for it in (its.data or []):
                c = counts.setdefault(it["project_id"], {"total": 0, "done": 0})
                c["total"] += 1
                if it.get("status") == "done":
                    c["done"] += 1
        except Exception as exc:
            logger.error("Portal item counts failed: %s", exc)

    projects = [
        {
            "id": r["id"],
            "company": r.get("company"),
            "description": r.get("description"),
            "task_type": r.get("task_type"),
            "stage": r.get("stage") or "submitted",
            "stage_note": r.get("stage_note"),
            "created_at": r.get("created_at"),
            "total": counts.get(r["id"], {}).get("total", 0),
            "done": counts.get(r["id"], {}).get("done", 0),
        }
        for r in subs
    ]
    return {"ok": True, "email": email, "projects": projects}


class PortalItemsIn(BaseModel):
    token: str
    project_id: str
    items: list[dict]


@router.post("/portal/items", response_model=SubmissionResponse, summary="Customer uploads data for their own project")
def portal_add_items(body: PortalItemsIn):
    email = verify_token(body.token)
    if not email:
        raise HTTPException(status_code=401, detail="This session has expired. Request a new sign-in link.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="The portal is temporarily unavailable. Please try again shortly.")
    if not body.items:
        return SubmissionResponse(ok=True, message="No items to upload.")

    # The customer may only upload to a project that belongs to their email.
    try:
        sub = db.table("project_submissions").select("id,email").eq("id", body.project_id).limit(1).execute()
    except Exception as exc:
        logger.error("Portal upload ownership check failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not upload. Please try again.")
    if not sub.data or sub.data[0].get("email") != email:
        raise HTTPException(status_code=403, detail="That project is not on this account.")
    _guard_item_keys(db, body.project_id, body.items)

    try:
        existing = (
            db.table("project_items").select("idx").eq("project_id", body.project_id)
            .order("idx", desc=True).limit(1).execute()
        )
        start = (existing.data[0]["idx"] + 1) if existing.data else 0
        rows_in = [{"project_id": body.project_id, "idx": start + i, "content": c} for i, c in enumerate(body.items)]
        db.table("project_items").insert(rows_in).execute()
    except Exception as exc:
        logger.error("Portal upload insert failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not upload your data. Please try again.")

    logger.info("Customer %s uploaded %d items to %s", email, len(body.items), body.project_id)
    return SubmissionResponse(ok=True, message=f"Uploaded {len(body.items)} items.")


@router.post("/portal/upload-image", response_model=SubmissionResponse, summary="Customer uploads one image file for their own project")
async def portal_upload_image(
    token: str = Form(...),
    project_id: str = Form(...),
    idx: int = Form(...),
    prediction: str = Form(default=""),
    study_id: str = Form(default=""),
    file: UploadFile = File(...),
):
    """Self-serve image intake: the client uploads one image + its manifest row (idx,
    prediction, study id). Stored under the client's private prefix with a de-identified key,
    then a review task is created — the same isolation/de-id the operator script does."""
    email = verify_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="This session has expired. Request a new sign-in link.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="The portal is temporarily unavailable. Please try again shortly.")
    # The customer may only upload to a project that belongs to their email.
    try:
        sub = db.table("project_submissions").select("id,email").eq("id", project_id).limit(1).execute()
    except Exception as exc:
        logger.error("Portal image-upload ownership check failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not upload. Please try again.")
    if not sub.data or sub.data[0].get("email") != email:
        raise HTTPException(status_code=403, detail="That project is not on this account.")

    try:
        client_id = storage.client_id_for(email)
        storage.ensure_bucket(db)
        data = await file.read()
        key = storage.upload_image_bytes(client_id, file.filename or "image.png", data, db)
        url = storage.signed_url(key, 30 * 24 * 3600, db)  # 30-day review window
        content = {"image": url, "prediction": (prediction or None)}
        if study_id:
            content["study_id"] = study_id
        # Idempotent per row: re-uploading the same idx replaces it, never duplicates
        # (guards against double-clicks and re-runs of the same batch).
        db.table("project_items").delete().eq("project_id", project_id).eq("idx", int(idx)).execute()
        db.table("project_items").insert({"project_id": project_id, "idx": int(idx), "content": content}).execute()
    except Exception as exc:
        logger.error("Portal image upload failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not upload that image. Please try again.")
    return SubmissionResponse(ok=True, message=f"Uploaded {file.filename}")


def _client_item(row: dict) -> dict:
    """A delivered item, stripped of internal (_-prefixed) keys so a client NEVER
    receives gold answers (content._gold_expected) or reviewer-level detail and
    identities (label._annotations carries each reviewer's `by`). Reviewers stay
    private to clients; aggregate QA lives in the report, not the per-item payload."""
    content = {k: v for k, v in (row.get("content") or {}).items() if not str(k).startswith("_")}
    label = {k: v for k, v in (row.get("label") or {}).items() if not str(k).startswith("_")}
    out = {"idx": row.get("idx"), "content": content, "label": label}
    if "labeled_at" in row:
        out["labeled_at"] = row.get("labeled_at")
    return out


@router.get("/portal/results", summary="Customer downloads their delivered results (magic-link token)")
def portal_results(token: str, project_id: str):
    email = verify_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="This session has expired. Request a new sign-in link.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="The portal is temporarily unavailable. Please try again shortly.")

    # Ownership check — a customer may only download results for their own project.
    try:
        sub = db.table("project_submissions").select("id,email,company,stage").eq("id", project_id).limit(1).execute()
    except Exception as exc:
        logger.error("Portal results lookup failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not load results. Please try again.")
    if not sub.data or sub.data[0].get("email") != email:
        raise HTTPException(status_code=403, detail="That project is not on this account.")
    s = sub.data[0]
    if s.get("stage") != "delivered":
        raise HTTPException(status_code=409, detail="Results are not ready yet. You can download them once the project is delivered.")

    try:
        rows = (
            db.table("project_items").select("idx,content,label,labeled_at")
            .eq("project_id", project_id).order("idx").execute()
        )
    except Exception as exc:
        logger.error("Portal results fetch failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not load results. Please try again.")
    # The computed model-performance report, so the client sees accuracy + critical misses
    # in the portal itself, not just the raw reviewed data.
    try:
        rep = report_svc.build_report(db, project_id)
    except Exception as exc:
        logger.error("Portal report build failed: %s", exc)
        rep = None
    return {"ok": True, "company": s.get("company"),
            "items": [_client_item(r) for r in (rows.data or [])], "report": rep}


# ── Self-serve API keys (magic-link verified) ────────────────────────────────────
# A developer verifies their email via a magic link, then creates/lists/revokes
# their own API keys — no operator in the loop. Keys are revocable (see api_keys).

@router.post("/portal/dev-link", response_model=SubmissionResponse, summary="Email a sign-in link to the developer console")
def portal_dev_link(body: DevLinkIn):
    email = body.email.strip().lower()
    link = f"{settings.SITE_URL.rstrip('/')}/developers?token={make_token(email)}"
    try:
        email_service.send_portal_link(email=email, link=link)
        logger.info("Developer sign-in link sent to %s", email)
    except Exception as exc:
        logger.error("Dev-link send failed for %s: %s", email, exc)
    # Never reveal whether the send succeeded — same anti-enumeration posture as the portal.
    return SubmissionResponse(ok=True, message="Check your email for a sign-in link.")


@router.get("/portal/keys", summary="List the account's API keys (magic-link token)")
def portal_list_keys(token: str):
    email = verify_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="This session has expired. Request a new sign-in link.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Temporarily unavailable. Please try again shortly.")
    from app.services import api_keys
    try:
        keys = api_keys.list_for(db, email)
    except Exception as exc:
        logger.error("List keys failed for %s: %s", email, exc)
        keys = []
    return {"ok": True, "email": email, "keys": keys}


@router.post("/portal/keys", summary="Create a new API key (magic-link token)")
def portal_create_key(body: KeyCreateIn):
    email = verify_token(body.token)
    if not email:
        raise HTTPException(status_code=401, detail="This session has expired. Request a new sign-in link.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Temporarily unavailable. Please try again shortly.")
    from app.services import api_keys
    try:
        out = api_keys.create(db, email, body.label)
    except Exception as exc:
        logger.error("Create key failed for %s: %s", email, exc)
        raise HTTPException(status_code=500, detail="Could not create the key. Please try again.")
    # api_key is returned once here and never again — the client must copy it now.
    return {"ok": True, **out}


@router.post("/portal/keys/revoke", response_model=SubmissionResponse, summary="Revoke an API key (magic-link token)")
def portal_revoke_key(body: KeyRevokeIn):
    email = verify_token(body.token)
    if not email:
        raise HTTPException(status_code=401, detail="This session has expired. Request a new sign-in link.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Temporarily unavailable. Please try again shortly.")
    from app.services import api_keys
    if not api_keys.revoke(db, email, body.key_id):
        raise HTTPException(status_code=404, detail="Key not found on this account.")
    return SubmissionResponse(ok=True, message="Key revoked. It can no longer be used.")


# ── Programmatic API (Bearer API key) ────────────────────────────────────────────
# Same capabilities as the portal, for clients who integrate by code instead of the
# dashboard: push items, poll status/results, and (optionally) get a webhook on delivery.

@router.get("/templates", summary="API: list outcome templates you can create a project from")
def api_templates():
    """The 'pick what you want to achieve' catalog. Create a project from one with
    POST /projects {"name": "...", "template": "<name>", "classes": [...]} — no config
    authoring needed for the common case."""
    from app.services import templates as tmpl
    return {"ok": True, "templates": tmpl.list_templates()}


@router.post("/projects", summary="API: create a project from a template or your own task config (Bearer API key)")
def api_create_project(body: CreateProjectIn, authorization: str | None = Header(default=None)):
    """Self-serve task creation: the client defines the task schema (eval_config) and
    creates the project by API, instead of an operator setting it up in the dashboard.
    The project is owned by the API key's account email."""
    email = _api_client_email(authorization)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    # Start from an outcome template (the "pick what you want" path) or a hand-authored
    # config. A template sets the right purpose + fields; the client only supplies `classes`.
    if body.template:
        from app.services import templates as tmpl
        eval_config = tmpl.config_from_template(body.template, classes=body.classes)
        if eval_config is None:
            names = ", ".join(t["name"] for t in tmpl.list_templates())
            raise HTTPException(status_code=422, detail=f"Unknown template '{body.template}'. Available: {names}.")
    elif body.eval_config:
        eval_config = body.eval_config
    else:
        raise HTTPException(status_code=422, detail="Provide a `template` (with optional `classes`) or a custom `eval_config`.")
    # Validate the schema renders before we save it — a broken config fails here.
    try:
        from app.services import labelstudio as ls
        ls.build_label_config(eval_config)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid eval_config: {exc}")
    ec = dict(eval_config)
    # Purpose is the keystone: it decides the reviewer workflow and the deliverable shape.
    # 'evaluate' judges a model's output (accuracy scorecard); 'label' categorises data;
    # 'create' produces new data (gold answers / preferences). Defaults to 'evaluate'.
    ec["purpose"] = _normalize_purpose(ec)
    # How many clinicians review each item is our quality call, not the client's — it is
    # the main cost/quality lever. Judgments (evaluate/label/preference) get N-way consensus;
    # pure free-text creation is authored by one expert. Operator tunes it per project via
    # POST /admin/reviewers.
    ec["reviewers_per_item"] = _default_reviewers(ec)
    if body.webhook_url:
        ec["_webhook_url"] = body.webhook_url
        ec.setdefault("_webhook_secret", secrets.token_hex(32))
    try:
        res = db.table("project_submissions").insert({
            "name": body.name,
            "company": body.name,
            "email": email,
            "description": "Created via API.",
            "eval_config": ec,
            "stage": "submitted",
            "status": "new",
        }).execute()
        pid = res.data[0]["id"]
    except Exception as exc:
        logger.error("API create project failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not create the project.")
    logger.info("API project created: %s (%s)", pid, email)
    resp = {"ok": True, "project_id": pid}
    if body.webhook_url:
        resp["webhook_secret"] = ec["_webhook_secret"]
    return resp


@router.post("/ingest", response_model=SubmissionResponse, summary="API: push items to a project (Bearer API key)")
def api_ingest(
    body: IngestIn,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
):
    email = _api_client_email(authorization)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    sub = db.table("project_submissions").select("id,email,eval_config").eq("id", body.project_id).limit(1).execute()
    if not sub.data or sub.data[0].get("email") != email:
        raise HTTPException(status_code=403, detail="That project is not on this API key.")
    ec = sub.data[0].get("eval_config") or {}

    # Idempotency (both paths): a repeated batch key is a retry — do not ingest twice.
    if idempotency_key and idempotency_key in (ec.get("_ingest_keys") or []):
        return SubmissionResponse(ok=True, message="Batch already ingested (idempotent).")

    # Register the delivery webhook (both paths).
    if body.webhook_url:
        ec["_webhook_url"] = body.webhook_url
        ec.setdefault("_webhook_secret", secrets.token_hex(32))
        db.table("project_submissions").update({"eval_config": ec}).eq("id", body.project_id).execute()

    # ── Bulk path: a manifest URL pointing at data in the client's storage (e.g. S3). ──
    # The data never flows through this API. We stream the manifest in the background and
    # ingest lightweight references — either a bounded random sample (mode "sample", to
    # evaluate) or every item (mode "all", to label everything). Either way the bytes stay
    # in the client's bucket, and /sync-pending feeds Label Studio a bounded rolling window.
    if body.source and body.source.manifest_url:
        try:
            import httpx
            with httpx.stream("GET", body.source.manifest_url, timeout=15) as r:
                r.raise_for_status()
                next(r.iter_lines(), None)
        except Exception as exc:
            logger.warning("Manifest not reachable for %s: %s", body.project_id, exc)
            raise HTTPException(status_code=400, detail="Could not read manifest_url. Check the URL is correct and readable (e.g. a valid pre-signed S3 URL).")
        mode = "all" if (body.source.mode or "sample").lower() == "all" else "sample"
        # If the same manifest job is already in flight (recent pending marker), this call
        # is a client retry — don't fire a second worker. The safety net re-fires only if
        # the marker goes stale (the original trigger was missed or its worker died).
        mp = ec.get("_manifest_pending")
        if idempotency_key and mp and mp.get("key") == idempotency_key and not _stale(mp.get("at"), settings.MANIFEST_STALE_MINUTES):
            return SubmissionResponse(ok=True, message="Already ingesting from this manifest.")
        # Record the pending marker (NOT the idempotency key — that goes on only when the
        # worker finishes, so a missed trigger can still be retried). This survives a lost
        # background trigger: GET /results re-fires the worker while this marker is unfinished.
        ec["_manifest_pending"] = {
            "manifest_url": body.source.manifest_url,
            "sample": body.source.sample,
            "mode": mode,
            "key": idempotency_key,
            "at": _now_iso(),
        }
        db.table("project_submissions").update({"eval_config": ec}).eq("id", body.project_id).execute()
        _kick_ingest_source(body.project_id, body.source.manifest_url, body.source.sample, idempotency_key, mode)
        if mode == "all":
            logger.info("API ingest (manifest, exhaustive) for %s", body.project_id)
            return SubmissionResponse(ok=True, message="Ingesting every item from the manifest — this runs in the background and streams into review.")
        n = min(int(body.source.sample or settings.DEFAULT_SAMPLE_SIZE), settings.MAX_SAMPLE_SIZE)
        logger.info("API ingest (manifest, sample) for %s: up to %d", body.project_id, n)
        return SubmissionResponse(ok=True, message=f"Ingesting from manifest — reviewing a sample of up to {n} items.")

    # ── Inline path: items in the request body (small batches). ──
    if not body.items:
        return SubmissionResponse(ok=True, message="No items to add.")
    if len(body.items) > settings.MAX_ITEMS_PER_INGEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Batch too large: {len(body.items)} items. Send at most "
                f"{settings.MAX_ITEMS_PER_INGEST} per call — split a big upload into several "
                f"calls (each with its own Idempotency-Key), or use manifest-based ingestion for bulk."
            ),
        )

    _guard_item_keys(db, body.project_id, body.items)

    try:
        existing = (
            db.table("project_items").select("idx").eq("project_id", body.project_id)
            .order("idx", desc=True).limit(1).execute()
        )
        start = (existing.data[0]["idx"] + 1) if existing.data else 0
        rows = [{"project_id": body.project_id, "idx": start + i, "content": c} for i, c in enumerate(body.items)]
        for i in range(0, len(rows), 500):
            db.table("project_items").insert(rows[i:i + 500]).execute()
    except Exception as exc:
        logger.error("API ingest failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not add items.")

    if idempotency_key:
        keys = ec.get("_ingest_keys") or []
        keys.append(idempotency_key)
        ec["_ingest_keys"] = keys
        db.table("project_submissions").update({"eval_config": ec}).eq("id", body.project_id).execute()

    _kick_sync(body.project_id)
    logger.info("API ingest: %d items to %s (queued for background sync)", len(body.items), body.project_id)
    return SubmissionResponse(ok=True, message=f"Ingested {len(body.items)} items.")


def _kick_sync(project_id: str) -> None:
    """Fire-and-forget trigger for the background /sync-pending run on this service.
    We send the request and let the read time out — Cloud Run keeps processing the
    triggered request on its own, so ingest returns instantly. Best-effort by design."""
    if not settings.ADMIN_API_KEY:
        return
    url = f"{settings.SELF_URL.rstrip('/')}/api/v1/project/sync-pending"
    try:
        import httpx
        httpx.post(url, params={"project_id": project_id},
                   headers={"X-Admin-Key": settings.ADMIN_API_KEY},
                   timeout=httpx.Timeout(5.0, read=0.5))
    except Exception:
        pass   # the read-timeout is expected; the sync request is now running independently


def _kick_ingest_source(project_id: str, manifest_url: str, sample: int | None, key: str | None = None, mode: str = "sample") -> None:
    """Fire-and-forget trigger for the background manifest ingest (stream + sample/ingest-all)."""
    if not settings.ADMIN_API_KEY:
        return
    url = f"{settings.SELF_URL.rstrip('/')}/api/v1/project/ingest-source"
    try:
        import httpx
        httpx.post(url, json={"project_id": project_id, "manifest_url": manifest_url, "sample": sample, "key": key, "mode": mode},
                   headers={"X-Admin-Key": settings.ADMIN_API_KEY},
                   timeout=httpx.Timeout(8.0, read=0.5))
    except Exception:
        pass


def _finalize_manifest_job(db, project_id: str, key: str | None) -> None:
    """Mark a manifest ingest complete: record its idempotency key (so any retry skips) and
    clear the pending marker (so the /results safety net stops re-firing it). Reloads
    eval_config first so we don't clobber a concurrent update (e.g. webhook registration)."""
    try:
        cur = db.table("project_submissions").select("eval_config").eq("id", project_id).limit(1).execute()
        ec = (cur.data[0].get("eval_config") if cur.data else None) or {}
        if key:
            keys = ec.get("_ingest_keys") or []
            if key not in keys:
                keys.append(key)
            ec["_ingest_keys"] = keys
        ec.pop("_manifest_pending", None)
        db.table("project_submissions").update({"eval_config": ec}).eq("id", project_id).execute()
    except Exception as exc:
        logger.error("finalize manifest job failed for %s: %s", project_id, exc)


@router.post("/sync-pending", response_model=SubmissionResponse, summary="Background: push pending items to Label Studio in chunks (internal)")
def sync_pending(project_id: str, x_admin_key: str | None = Header(default=None)):
    """Push one chunk of a project's not-yet-synced ('pending') items into Label Studio,
    mark them 'queued', and chain the next chunk. Self-triggered after ingest and by the
    GET /results safety net — drains any backlog with no operator and no scheduler."""
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    CHUNK = 500
    sub = db.table("project_submissions").select("id,company,ls_project_id,eval_config").eq("id", project_id).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="Project not found.")
    s = sub.data[0]
    ec = s.get("eval_config")
    if not ec:
        return SubmissionResponse(ok=True, message="No config yet; nothing to sync.")

    # Ensure the LS project exists once, up front (stored on the submission).
    try:
        from app.services import labelstudio as ls
        label_config = ls.build_label_config(ec)
        reviewers = int(ec.get("reviewers_per_item") or 1)
        ls_pid = s.get("ls_project_id")
        if not ls_pid:
            title = f"{s.get('company') or 'Senebiclabs project'} — eval"
            ls_pid = ls.create_project(title=title, label_config=label_config, reviewers=reviewers)
            db.table("project_submissions").update({"ls_project_id": ls_pid}).eq("id", project_id).execute()
    except Exception as exc:
        logger.error("sync-pending setup failed for %s: %s", project_id, exc)
        raise HTTPException(status_code=502, detail="Sync setup failed; will retry.")

    # Rolling window (backpressure): keep at most LS_ACTIVE_WINDOW tasks ACTIVE in Label
    # Studio at once (status 'queued' or 'in_progress'). The full backlog lives in our DB;
    # we only top LS up to the free capacity. When clinicians finish a task the webhook marks
    # it 'done', a slot frees, and a refill kick tops the window back up — so LS load stays
    # flat no matter how big the job is. This is what makes exhaustive bulk non-stressful.
    active = (db.table("project_items").select("id", count="exact")
              .eq("project_id", project_id).in_("status", ["queued", "in_progress"]).limit(1).execute()).count or 0
    capacity = settings.LS_ACTIVE_WINDOW - active
    if capacity <= 0:
        logger.info("sync-pending: window full for %s (active=%d) — waiting for reviews to complete", project_id, active)
        return SubmissionResponse(ok=True, message=f"Label Studio window full ({active} active); refills as reviews complete.")
    take = min(CHUNK, capacity)

    # Atomically CLAIM up to `take` pending items: read them, then transition only rows STILL
    # 'pending' to 'queued'. Concurrent sync runs each claim a disjoint set (the status='pending'
    # filter makes the row transition the lock), so items are never double-pushed to LS.
    cand = (db.table("project_items").select("id,content")
            .eq("project_id", project_id).eq("status", "pending").order("idx").limit(take).execute()).data or []
    if not cand:
        return SubmissionResponse(ok=True, message="Nothing pending to sync.")
    claimed_ids = []
    for i in range(0, len(cand), 100):
        chunk_ids = [c["id"] for c in cand[i:i + 100]]
        res = db.table("project_items").update({"status": "queued"}).in_("id", chunk_ids).eq("status", "pending").execute()
        claimed_ids.extend(r["id"] for r in (res.data or []))
    claimed = set(claimed_ids)
    items = [c for c in cand if c["id"] in claimed]
    if not items:
        _kick_sync(project_id)     # a concurrent run took this chunk; keep draining
        return SubmissionResponse(ok=True, message="Chunk already claimed by a concurrent sync.")

    try:
        ls.push_tasks(ls_pid, items)
    except Exception as exc:
        # Push failed — return the claimed items to 'pending' so they get re-synced.
        for i in range(0, len(claimed_ids), 100):
            db.table("project_items").update({"status": "pending"}).in_("id", claimed_ids[i:i + 100]).execute()
        logger.error("sync-pending push failed for %s: %s", project_id, exc)
        raise HTTPException(status_code=502, detail="Sync push failed; items returned to pending for retry.")

    # Chain the next chunk only while the window still has room AND work is pending. Once the
    # window is full we stop; completions (via the webhook refill kick) resume the draining.
    remaining_capacity = capacity - len(items)
    more = db.table("project_items").select("id").eq("project_id", project_id).eq("status", "pending").limit(1).execute()
    if more.data and remaining_capacity > 0:
        _kick_sync(project_id)
    logger.info("sync-pending: pushed %d for %s (active_before=%d, window=%d, more_pending=%s)",
                len(items), project_id, active, settings.LS_ACTIVE_WINDOW, bool(more.data))
    return SubmissionResponse(ok=True, message=f"Synced {len(items)} items.")


@router.post("/ingest-source", response_model=SubmissionResponse, summary="Background: stream a manifest, sample, and ingest references (internal)")
def ingest_source(body: IngestSourceIn, x_admin_key: str | None = Header(default=None)):
    """Stream a JSONL manifest from the client's storage, reservoir-sample a bounded
    review set, and ingest those as pending items. The bulk data itself never flows
    through this API — only the manifest (references) is read here, then the sample is
    pushed to Label Studio by the usual /sync-pending run. Each manifest line is one
    item (a JSON object whose fields match the task config, e.g. {case_id, audio_url})."""
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    sub = db.table("project_submissions").select("eval_config").eq("id", body.project_id).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="Project not found.")
    ec = sub.data[0].get("eval_config") or {}
    # Idempotency: a completed job recorded its key here, so a re-fire (client retry or
    # the /results safety net) is a no-op instead of ingesting a second sample.
    if body.key and body.key in (ec.get("_ingest_keys") or []):
        return SubmissionResponse(ok=True, message="Manifest already ingested (idempotent).")
    try:
        from app.services import labelstudio as ls
        required = ls.required_data_keys(ec)
    except Exception:
        required = []

    import httpx as _httpx, json as _json, random
    exhaustive = (body.mode or "sample").lower() == "all"
    target = min(int(body.sample or settings.DEFAULT_SAMPLE_SIZE), settings.MAX_SAMPLE_SIZE)

    # Exhaustive mode ingests EVERY valid item, buffered and chunk-inserted as we stream, so
    # memory stays flat no matter how large the manifest is. We keep a running idx from the
    # current max. Sample mode reservoir-samples a bounded set in a single pass.
    next_idx = 0
    if exhaustive:
        existing = (db.table("project_items").select("idx").eq("project_id", body.project_id)
                    .order("idx", desc=True).limit(1).execute())
        next_idx = (existing.data[0]["idx"] + 1) if existing.data else 0

    reservoir: list[dict] = []
    buf: list[dict] = []
    n_seen = skipped = lines_read = inserted = 0
    capped = False

    def _flush_buffer():
        nonlocal next_idx, inserted
        if not buf:
            return
        rows = [{"project_id": body.project_id, "idx": next_idx + i, "content": c} for i, c in enumerate(buf)]
        for i in range(0, len(rows), 500):
            db.table("project_items").insert(rows[i:i + 500]).execute()
        next_idx += len(buf)
        inserted += len(buf)
        buf.clear()

    try:
        with _httpx.stream("GET", body.manifest_url, timeout=_httpx.Timeout(30.0, read=None)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                # Hard cap on lines read so a pathologically huge manifest can't run the
                # worker forever — we ingest/sample what we've read and stop.
                lines_read += 1
                if lines_read > settings.MAX_MANIFEST_LINES:
                    capped = True
                    break
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = _json.loads(line)
                except Exception:
                    skipped += 1
                    continue
                if not isinstance(obj, dict) or (required and any(not obj.get(k) for k in required)):
                    skipped += 1
                    continue
                n_seen += 1
                if exhaustive:
                    buf.append(obj)
                    if len(buf) >= 500:
                        _flush_buffer()
                else:
                    # Reservoir sampling: a uniform random sample of `target` items in a
                    # single streaming pass, without loading the whole manifest into memory.
                    if len(reservoir) < target:
                        reservoir.append(obj)
                    else:
                        j = random.randint(0, n_seen - 1)
                        if j < target:
                            reservoir[j] = obj
            if exhaustive:
                _flush_buffer()
    except Exception as exc:
        logger.error("ingest-source read failed for %s: %s", body.project_id, exc)
        raise HTTPException(status_code=502, detail="Could not read the manifest.")

    # ── Exhaustive: every item is already inserted as pending; hand off to the rolling window. ──
    if exhaustive:
        _finalize_manifest_job(db, body.project_id, body.key)   # clean read = done, even if 0 usable
        if inserted == 0:
            logger.warning("ingest-source (all): no usable items for %s (skipped %d)", body.project_id, skipped)
            return SubmissionResponse(ok=True, message=f"Manifest had no usable items (skipped {skipped}).")
        _kick_sync(body.project_id)   # /sync-pending tops Label Studio up to the rolling window
        seen_note = f"{inserted}+ (line cap reached)" if capped else str(inserted)
        logger.info("ingest-source (all): ingested %s items for %s (skipped %d, capped=%s)", seen_note, body.project_id, skipped, capped)
        return SubmissionResponse(ok=True, message=f"Ingested all {seen_note} items from the manifest into review.")

    # ── Sample: insert the reservoir, then hand off. ──
    if not reservoir:
        # A clean read that yielded nothing usable is still a completed job — finalize so
        # it isn't retried forever.
        _finalize_manifest_job(db, body.project_id, body.key)
        logger.warning("ingest-source: no usable items for %s (skipped %d)", body.project_id, skipped)
        return SubmissionResponse(ok=True, message=f"Manifest had no usable items (skipped {skipped}).")

    existing = (
        db.table("project_items").select("idx").eq("project_id", body.project_id)
        .order("idx", desc=True).limit(1).execute()
    )
    start = (existing.data[0]["idx"] + 1) if existing.data else 0
    rows = [{"project_id": body.project_id, "idx": start + i, "content": c} for i, c in enumerate(reservoir)]
    for i in range(0, len(rows), 500):
        db.table("project_items").insert(rows[i:i + 500]).execute()

    # Success: record the key and clear the pending marker, then push the sample to LS.
    _finalize_manifest_job(db, body.project_id, body.key)
    _kick_sync(body.project_id)
    seen_note = f"{n_seen}+ (line cap reached)" if capped else str(n_seen)
    logger.info("ingest-source: sampled %d of %s for %s (skipped %d, capped=%s)", len(reservoir), seen_note, body.project_id, skipped, capped)
    return SubmissionResponse(ok=True, message=f"Ingested a sample of {len(reservoir)} from {seen_note} manifest items.")


@router.get("/results", summary="API: batch status + results (Bearer API key)")
def api_results(project_id: str, authorization: str | None = Header(default=None)):
    email = _api_client_email(authorization)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    sub = db.table("project_submissions").select("id,email,company,stage,eval_config").eq("id", project_id).limit(1).execute()
    if not sub.data or sub.data[0].get("email") != email:
        raise HTTPException(status_code=403, detail="That project is not on this API key.")
    s = sub.data[0]
    # Safety net: if anything is still waiting to reach Label Studio, nudge the background
    # sync. Best-effort, so a poll never blocks — but it means a missed ingest-trigger
    # self-heals on the client's next poll.
    try:
        if db.table("project_items").select("id").eq("project_id", project_id).eq("status", "pending").limit(1).execute().data:
            _kick_sync(project_id)
    except Exception:
        pass
    # Safety net for bulk: a manifest ingest whose background trigger was missed (or whose
    # worker died) leaves a pending marker that never clears. If it's gone stale, re-fire it
    # and bump the timestamp so we don't re-fire on every poll. This is what makes a lost
    # bulk trigger self-heal instead of silently reporting total: 0 forever.
    try:
        ec = s.get("eval_config") or {}
        mp = ec.get("_manifest_pending")
        if mp and mp.get("key") not in (ec.get("_ingest_keys") or []) and _stale(mp.get("at"), settings.MANIFEST_STALE_MINUTES):
            mp["at"] = _now_iso()
            ec["_manifest_pending"] = mp
            db.table("project_submissions").update({"eval_config": ec}).eq("id", project_id).execute()
            _kick_ingest_source(project_id, mp.get("manifest_url"), mp.get("sample"), mp.get("key"), mp.get("mode") or "sample")
            logger.info("results: re-fired stalled manifest ingest for %s", project_id)
    except Exception:
        pass
    total, done = _progress(db, project_id)
    stage = s.get("stage") or "submitted"
    # Client-facing status only. The internal delivery funnel (scoping/agreement/pilot/…) is
    # never disclosed — the client sees just what matters to them: received, in review, or
    # delivered, alongside the total/done counts for progress.
    client_status = "delivered" if stage == "delivered" else ("received" if stage == "submitted" else "in_review")
    purpose = _normalize_purpose(s.get("eval_config") or {})
    out: dict = {"ok": True, "project_id": project_id, "purpose": purpose,
                 "status": client_status, "total": total, "done": done}
    # Poll-friendly: only 'delivered' carries the report + reviewed items; earlier
    # stages just report status + counts so the client can loop without errors.
    if stage == "delivered":
        try:
            rows = (
                db.table("project_items").select("idx,content,label,labeled_at")
                .eq("project_id", project_id).order("idx").execute()
            )
            out["items"] = [_client_item(r) for r in (rows.data or [])]
            out["report"] = report_svc.build_report(db, project_id)
        except Exception as exc:
            logger.error("API results build failed: %s", exc)
            out["items"] = []
            out["report"] = None
    return out


def _fire_webhook(db, project_id: str) -> None:
    """POST the delivered results to the client's registered webhook, if any.
    Fire-and-forget: a webhook failure never blocks marking the batch delivered."""
    try:
        sub = db.table("project_submissions").select("eval_config,company").eq("id", project_id).limit(1).execute()
        ec = (sub.data[0].get("eval_config") if sub.data else None) or {}
        url = ec.get("_webhook_url")
        if not url:
            return
        rows = (
            db.table("project_items").select("idx,content,label,labeled_at")
            .eq("project_id", project_id).order("idx").execute()
        )
        payload = {
            "event": "results.delivered",
            "project_id": project_id,
            "company": (sub.data[0].get("company") if sub.data else None),
            "report": report_svc.build_report(db, project_id),
            "items": [_client_item(r) for r in (rows.data or [])],
        }
        
        body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        
        headers = {"Content-Type": "application/json"}
        secret = ec.get("_webhook_secret")
        if secret:
            signature = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
            headers["X-Senebiclabs-Signature"] = "sha256=" + signature
        
        
        import httpx
        r = httpx.post(url, content=body_bytes, headers=headers, timeout=15)
        logger.info("Webhook for %s -> %s (%s)", project_id, url, r.status_code)
    except Exception as exc:
        logger.error("Webhook delivery failed for %s: %s", project_id, exc)


# ── Admin ──────────────────────────────────────────────────────────────────────

def _require_admin(x_admin_key: str | None) -> None:
    if not settings.ADMIN_API_KEY or x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Not authorised.")


@router.get("/admin/report/{project_id}", summary="Model-performance report for a project (admin)")
def admin_report(project_id: str, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    try:
        return {"ok": True, "report": report_svc.build_report(db, project_id)}
    except Exception as exc:
        logger.error("Report build failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not build the report.")


@router.get("/admin/adjudication/{project_id}", summary="Items awaiting adjudication — reviewers disagreed (admin)")
def admin_adjudication_queue(project_id: str, x_admin_key: str | None = Header(default=None)):
    """The QA queue: items where reviewers split, held out of the deliverable until resolved.
    Each carries every reviewer's own answer so a senior reviewer can see the disagreement and
    decide. Populated only when a project runs with eval_config.adjudicate on."""
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    rows = (
        db.table("project_items").select("idx,content,label")
        .eq("project_id", project_id).eq("status", "needs_adjudication").order("idx").execute()
    ).data or []
    queue = []
    for r in rows:
        lbl = r.get("label") or {}
        queue.append({
            "idx": r.get("idx"),
            "agreement": lbl.get("_agreement"),
            "reviewers": lbl.get("_reviewers"),
            "consensus_verdict": lbl.get("verdict"),
            "annotations": lbl.get("_annotations"),      # each reviewer's answer — the split, laid out
            "content": r.get("content"),
        })
    return {"ok": True, "project_id": project_id, "count": len(queue), "items": queue}


@router.post("/admin/adjudicate", response_model=SubmissionResponse, summary="Resolve a disagreed item with a final answer (admin)")
def admin_adjudicate(body: AdjudicateIn, x_admin_key: str | None = Header(default=None)):
    """A senior reviewer resolves a split: the final_label wins over the provisional consensus,
    the item is marked done, and — if this was the last thing holding the batch — the sign-off
    gate is re-checked. The reviewers' original answers are preserved on the label for audit."""
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    row = (
        db.table("project_items").select("id,label,status")
        .eq("project_id", body.project_id).eq("idx", body.idx).limit(1).execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail="Item not found in this project.")
    it = row.data[0]
    if it.get("status") != "needs_adjudication":
        raise HTTPException(status_code=409,
                            detail=f"Item {body.idx} is not awaiting adjudication (status: {it.get('status')}).")
    label = dict(it.get("label") or {})
    label.update(body.final_label)                        # the resolving answer replaces the split verdict
    label["_adjudicated"] = True
    label["_adjudicated_at"] = _now_iso()
    if body.note:
        label["_adjudication_note"] = body.note
    db.table("project_items").update(
        {"label": label, "status": "done", "labeled_at": _now_iso()}
    ).eq("id", it["id"]).execute()
    try:
        audit.record(db, item_id=it["id"], project_id=body.project_id, action=audit.LABEL,
                     actor_id="adjudicator", actor_name="adjudicator", source="adjudication", value=label)
    except Exception:
        pass
    try:
        from app.api.v1.ls import _maybe_auto_deliver    # lazy: avoid circular import
        _maybe_auto_deliver(db, body.project_id)
    except Exception:
        pass
    return SubmissionResponse(ok=True, message=f"Item {body.idx} adjudicated and marked done.")


@router.get("/admin/reviewers/{project_id}", summary="Per-reviewer quality: consensus agreement + gold accuracy (admin)")
def admin_reviewer_quality(project_id: str, x_admin_key: str | None = Header(default=None)):
    """Continuous QA on the people: each reviewer's agreement with consensus and accuracy on
    gold (known-answer) items, with anyone under the floor flagged. Read-only; computed from
    what is already stored, so it works on any multi-reviewed project."""
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    rows = (
        db.table("project_items").select("idx,content,label,status,labeled_by")
        .eq("project_id", project_id).execute()
    ).data or []
    # Real per-clinician attribution comes from the workforce system of record: the LS
    # annotations all carry one service user, so who-reviewed-what lives in task_completions.
    # Bridge project -> LS project -> pool(s) -> completions, keyed by the item's _ls_task_id.
    completions_by_task: dict = {}
    clinician_names: dict = {}
    try:
        sub = db.table("project_submissions").select("ls_project_id").eq("id", project_id).limit(1).execute()
        ls_pid = sub.data[0].get("ls_project_id") if sub.data else None
        if ls_pid:
            pools = db.table("pools").select("id").eq("ls_project_id", int(ls_pid)).execute().data or []
            pool_ids = [p["id"] for p in pools]
            if pool_ids:
                comps = (db.table("task_completions").select("clinician_id,ls_task_id,annotation_data")
                         .in_("pool_id", pool_ids).execute().data or [])
                for c in comps:
                    completions_by_task.setdefault(c.get("ls_task_id"), []).append(c)
                cids = list({c.get("clinician_id") for c in comps if c.get("clinician_id")})
                for i in range(0, len(cids), 100):
                    for cl in (db.table("clinicians").select("id,name,email")
                               .in_("id", cids[i:i + 100]).execute().data or []):
                        clinician_names[cl["id"]] = cl.get("email") or cl.get("name") or cl["id"]
    except Exception as exc:
        logger.warning("reviewer_quality attribution join failed for %s: %s", project_id, exc)
    return {"ok": True, "project_id": project_id,
            **report_svc.reviewer_quality(rows, completions_by_task or None, clinician_names or None)}


@router.get("/admin/audit/{project_id}", summary="Audit trail for a project (admin)")
def admin_audit(project_id: str, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    try:
        events = audit.history(db, project_id)
    except Exception as exc:
        logger.error("Audit read failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not load the audit trail (is the audit_events migration applied?).")
    return {"ok": True, "project_id": project_id, "count": len(events), "events": events}


@router.get("/admin/submissions", summary="List all project submissions (admin)")
def admin_submissions(x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    base = "id,name,email,company,description,task_type,volume,timeline,stage,stage_note,status,created_at,updated_at"
    try:
        rows = (
            db.table("project_submissions")
            .select(base + ",rate_per_item,difficulty")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception:
        # rate_per_item / difficulty migration not applied yet — degrade, don't 500.
        logger.warning("rate_per_item/difficulty columns missing; run the migration to enable pay/difficulty.")
        try:
            rows = db.table("project_submissions").select(base).order("created_at", desc=True).execute()
        except Exception as exc:
            logger.error("Admin list failed: %s", exc)
            raise HTTPException(status_code=500, detail="Could not load submissions.")

    subs = rows.data or []
    # Attach real item progress (total / done) to each submission in one query.
    try:
        items = db.table("project_items").select("project_id,status").execute()
        counts: dict[str, dict[str, int]] = {}
        for it in (items.data or []):
            c = counts.setdefault(it["project_id"], {"total": 0, "done": 0})
            c["total"] += 1
            if it.get("status") == "done":
                c["done"] += 1
        for s in subs:
            c = counts.get(s["id"], {"total": 0, "done": 0})
            s["total"], s["done"] = c["total"], c["done"]
    except Exception as exc:
        logger.error("Progress aggregation failed: %s", exc)
        for s in subs:
            s.setdefault("total", 0)
            s.setdefault("done", 0)

    return {"ok": True, "submissions": subs, "stages": STAGES}


@router.post("/admin/advance", response_model=SubmissionResponse, summary="Advance a project's stage (admin)")
def admin_advance(body: AdminAdvance, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    if body.stage not in STAGES:
        raise HTTPException(status_code=422, detail=f"stage must be one of: {', '.join(STAGES)}")

    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")

    # Reality-check the stages the system can verify, so status can't be set to a lie.
    if body.stage in ("pilot", "production", "delivered"):
        total, done = _progress(db, body.submission_id)
        if total == 0:
            raise HTTPException(status_code=422, detail=f"Add items to this project before moving it to “{body.stage}”.")
        if body.stage == "delivered" and done < total:
            raise HTTPException(status_code=422, detail=f"Can’t mark delivered — only {done} of {total} items are labeled.")

    try:
        db.table("project_submissions").update(
            {
                "stage": body.stage,
                "stage_note": body.note,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", body.submission_id).execute()
    except Exception as exc:
        logger.error("Admin advance failed: %s", exc)
        raise HTTPException(status_code=500, detail="Update failed.")

    logger.info("Project %s advanced to %s", body.submission_id, body.stage)
    if body.stage == "delivered":
        _fire_webhook(db, body.submission_id)   # notify the API client, if one is registered
    return SubmissionResponse(ok=True, message=f"Project advanced to {body.stage}.")


@router.post("/admin/project-meta", response_model=SubmissionResponse, summary="Set a project's pay rate + difficulty (admin)")
def admin_set_meta(body: ProjectMetaIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    try:
        db.table("project_submissions").update(
            {"rate_per_item": body.rate_per_item, "difficulty": body.difficulty}
        ).eq("id", body.project_id).execute()
    except Exception as exc:
        logger.error("Set project meta failed: %s", exc)
        if "rate_per_item" in str(exc) or "difficulty" in str(exc) or "42703" in str(exc):
            raise HTTPException(
                status_code=400,
                detail="Pay/difficulty columns are missing. Run the migration (add rate_per_item + difficulty to project_submissions) and try again.",
            )
        raise HTTPException(status_code=500, detail="Could not save.")
    return SubmissionResponse(ok=True, message="Saved.")


@router.post("/admin/reviewers", response_model=SubmissionResponse, summary="Set how many clinicians review each item (admin)")
def admin_set_reviewers(body: ReviewersIn, x_admin_key: str | None = Header(default=None)):
    """We assign the reviewer count for quality; this lets the operator tune it per
    project (e.g. 2 for a cost-sensitive pilot, 5 for a high-stakes safety eval)."""
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    n = max(1, int(body.reviewers_per_item))
    sub = db.table("project_submissions").select("eval_config,ls_project_id").eq("id", body.project_id).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="Project not found.")
    ec = sub.data[0].get("eval_config") or {}
    ec["reviewers_per_item"] = n
    db.table("project_submissions").update({"eval_config": ec}).eq("id", body.project_id).execute()
    # Apply to the live Label Studio project immediately if it exists (best-effort).
    ls_pid = sub.data[0].get("ls_project_id")
    if ls_pid:
        try:
            from app.services import labelstudio as ls
            ls.update_project_config(ls_pid, ls.build_label_config(ec), reviewers=n)
        except Exception as exc:
            logger.warning("Reviewers set to %d for %s but LS update deferred: %s", n, body.project_id, exc)
    return SubmissionResponse(ok=True, message=f"Set to {n} reviewer(s) per item.")


@router.post("/admin/eval-config", response_model=SubmissionResponse, summary="Set a project's eval config / task schema (admin)")
def admin_set_eval_config(body: EvalConfigIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    # Validate it renders BEFORE saving — a broken schema fails here, not later at sync.
    try:
        from app.services import labelstudio as ls
        ls.build_label_config(body.eval_config)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid eval config: {exc}")
    new_ec = dict(body.eval_config)
    try:
        cur = db.table("project_submissions").select("eval_config").eq("id", body.project_id).limit(1).execute()
        old_ec = (cur.data[0].get("eval_config") if cur.data else None) or {}
    except Exception:
        old_ec = {}

    # Guideline-lock: once a project has reviewed items, its guideline + task layout are frozen so
    # a mid-batch change can't silently split the data into items judged by different rules.
    # Operational settings (reviewers, adjudicate, auto_deliver, internal _-markers) stay editable.
    # An operator can override deliberately with force=true (logged) — e.g. to fix a rubric typo.
    grading_changed = _grading_signature(old_ec) != _grading_signature(new_ec)
    if grading_changed and _project_has_reviews(db, body.project_id):
        if not body.force:
            raise HTTPException(status_code=409, detail=(
                "This project already has reviewed items, so its guideline and task layout are "
                "locked to keep every item judged by the same standard. You can still change "
                "operational settings (reviewer count, adjudication, auto-deliver). To change the "
                "rubric, fields, classes, or purpose, start a new batch — or override intentionally "
                "with force=true."))
        logger.warning("Guideline-lock OVERRIDDEN (force=true) on project %s that already has reviews.", body.project_id)

    # Preserve internal _-markers (webhook URL/secret, manifest state, ready flags) that a full
    # config overwrite would otherwise wipe.
    for k, v in old_ec.items():
        if k.startswith("_") and k not in new_ec:
            new_ec[k] = v

    try:
        db.table("project_submissions").update({"eval_config": new_ec}).eq("id", body.project_id).execute()
    except Exception as exc:
        logger.error("Set eval_config failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save the config.")
    msg = "Config saved." + (" Guideline-lock overridden." if (grading_changed and body.force) else "")
    return SubmissionResponse(ok=True, message=msg)


@router.post("/admin/api-key", summary="Generate a long-lived API key for a client (admin)")
def admin_api_key(body: ApiKeyIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    if body.email:
        email = body.email.strip().lower()
    elif body.project_id:
        sub = db.table("project_submissions").select("id,email").eq("id", body.project_id).limit(1).execute()
        if not sub.data:
            raise HTTPException(status_code=404, detail="Project not found.")
        email = sub.data[0]["email"]
    else:
        raise HTTPException(status_code=422, detail="Provide project_id or email.")
    # The key is tied to the account email, so one key can create and drive many projects.
    key = make_api_key(email)
    from app.services import api_keys
    api_keys.record(db, email, key, label="Issued by operator")   # make it revocable + listed
    return {"ok": True, "api_key": key, "email": email, "project_id": body.project_id}


# ── Work: items + labeling ──────────────────────────────────────────────────────

def _progress(db, project_id: str) -> tuple[int, int]:
    rows = db.table("project_items").select("status").eq("project_id", project_id).execute()
    data = rows.data or []
    return len(data), sum(1 for r in data if r.get("status") == "done")


def _guard_item_keys(db, project_id: str, items: list[dict]) -> None:
    """Reject a bulk item add whose rows lack a key the project's config needs
    (e.g. a plain CSV added to an image task), with the exact next step — before any
    broken items are created. No-op when the config imposes no required keys."""
    from app.services import labelstudio as ls
    try:
        sub = db.table("project_submissions").select("eval_config").eq("id", project_id).limit(1).execute()
        eval_config = sub.data[0].get("eval_config") if sub.data else None
    except Exception:
        eval_config = None
    for k in ls.required_data_keys(eval_config):
        n_missing = sum(1 for c in items if not (c or {}).get(k))
        if n_missing:
            hint = (
                " Images are added by dropping the image files alongside your CSV — a plain "
                "CSV can't carry them."
                if k == "image"
                else f" Add a '{k}' column to your file, or pick a config that matches it."
            )
            raise HTTPException(
                status_code=422,
                detail=f"{n_missing} of {len(items)} row(s) have no '{k}', which this task needs.{hint}",
            )


@router.post("/admin/items", response_model=SubmissionResponse, summary="Add work items to a project (admin)")
def admin_add_items(body: ItemsIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    if not body.items:
        return SubmissionResponse(ok=True, message="No items to add.")
    _guard_item_keys(db, body.project_id, body.items)

    try:
        existing = (
            db.table("project_items").select("idx").eq("project_id", body.project_id)
            .order("idx", desc=True).limit(1).execute()
        )
        start = (existing.data[0]["idx"] + 1) if existing.data else 0
        rows = [{"project_id": body.project_id, "idx": start + i, "content": c} for i, c in enumerate(body.items)]
        db.table("project_items").insert(rows).execute()
    except Exception as exc:
        logger.error("Add items failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not add items.")
    return SubmissionResponse(ok=True, message=f"Added {len(body.items)} items.")


@router.get("/admin/progress", summary="Item progress for a project (admin)")
def admin_progress(project_id: str, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    total, done = _progress(db, project_id)
    return {"ok": True, "total": total, "done": done}


def _claim_fallback(db, project_id: str, labeler_id: str) -> dict | None:
    """App-level claim used if the atomic claim_next_item() DB function is absent.

    Compare-and-swap on the row status so two labelers can never take the same item.
    Heavier (several round trips) but correct; the RPC path is preferred.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=CLAIM_TTL_MINUTES)).isoformat()
    db.table("project_items").update({"status": "pending", "assigned_to": None, "claimed_at": None}) \
        .eq("project_id", project_id).eq("status", "in_progress").lt("claimed_at", cutoff).execute()

    held = (
        db.table("project_items").select("id,idx,content")
        .eq("project_id", project_id).eq("status", "in_progress").eq("assigned_to", labeler_id)
        .order("idx").limit(1).execute()
    )
    if held.data:
        return held.data[0]

    for _ in range(30):
        nxt = (
            db.table("project_items").select("id,idx,content")
            .eq("project_id", project_id).eq("status", "pending").order("idx").limit(1).execute()
        )
        if not nxt.data:
            return None
        cand = nxt.data[0]
        claim = (
            db.table("project_items")
            .update({"status": "in_progress", "assigned_to": labeler_id, "claimed_at": _now_iso()})
            .eq("id", cand["id"]).eq("status", "pending").execute()
        )
        if claim.data:
            return {"id": cand["id"], "idx": cand["idx"], "content": cand["content"]}
    return None


@router.get("/work/next", summary="Claim the next item to label (clinician or operator)")
def work_next(project_id: str, x_work_code: str | None = Header(default=None)):
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    labeler = _resolve_labeler(db, x_work_code)
    if not labeler:
        raise HTTPException(status_code=403, detail="Invalid or inactive access code.")
    if not _labeler_can_access(db, labeler, project_id):
        raise HTTPException(status_code=403, detail="You are not assigned to this project.")

    item = None
    try:
        # Preferred: one atomic round trip via FOR UPDATE SKIP LOCKED in Postgres.
        res = db.rpc("claim_next_item", {
            "p_project": project_id,
            "p_labeler": labeler["id"],
            "p_ttl_minutes": CLAIM_TTL_MINUTES,
        }).execute()
        if res.data:
            row = res.data[0]
            item = {"id": row["id"], "idx": row["idx"], "content": row["content"]}
    except Exception as exc:
        logger.warning("claim_next_item RPC unavailable, using fallback: %s", exc)
        try:
            item = _claim_fallback(db, project_id, labeler["id"])
        except Exception as exc2:
            logger.error("Work next fallback failed: %s", exc2)
            raise HTTPException(status_code=500, detail="Could not load the next item.")

    total, done = _progress(db, project_id)
    return {"ok": True, "item": item, "total": total, "done": done, "labeler": labeler["name"]}


@router.post("/work/label", response_model=SubmissionResponse, summary="Save a label and mark item done")
def work_label(body: LabelIn, x_work_code: str | None = Header(default=None)):
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    labeler = _resolve_labeler(db, x_work_code)
    if not labeler:
        raise HTTPException(status_code=403, detail="Invalid or inactive access code.")

    try:
        row = db.table("project_items").select("assigned_to,status,project_id").eq("id", body.item_id).limit(1).execute()
    except Exception as exc:
        logger.error("Label lookup failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save the label.")
    if not row.data:
        raise HTTPException(status_code=404, detail="Item not found.")
    item = row.data[0]
    if not _labeler_can_access(db, labeler, item.get("project_id")):
        raise HTTPException(status_code=403, detail="You are not assigned to this project.")

    # A clinician may only label the item they hold. The operator can label anything.
    if labeler["id"] != "admin" and item.get("assigned_to") != labeler["id"]:
        raise HTTPException(status_code=409, detail="This item is assigned to someone else. Skipping to the next.")

    try:
        db.table("project_items").update(
            {
                "label": body.label,
                "status": "done",
                "labeled_by": labeler["name"],
                "labeled_at": _now_iso(),
            }
        ).eq("id", body.item_id).execute()
    except Exception as exc:
        logger.error("Save label failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save the label.")

    # Audit: a save onto an already-done item is a review/correction, not a first label.
    action = audit.REVIEW if item.get("status") == "done" else audit.LABEL
    audit.record(db, item_id=body.item_id, project_id=item.get("project_id"), action=action,
                 actor_id=labeler["id"], actor_name=labeler["name"], source="app", value=body.label)
    return SubmissionResponse(ok=True, message="Saved.")


@router.post("/work/skip", response_model=SubmissionResponse, summary="Skip the current item (release to back of queue)")
def work_skip(body: SkipIn, x_work_code: str | None = Header(default=None)):
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    labeler = _resolve_labeler(db, x_work_code)
    if not labeler:
        raise HTTPException(status_code=403, detail="Invalid or inactive access code.")

    try:
        row = db.table("project_items").select("project_id,status,assigned_to").eq("id", body.item_id).limit(1).execute()
    except Exception as exc:
        logger.error("Skip lookup failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not skip.")
    if not row.data:
        raise HTTPException(status_code=404, detail="Item not found.")
    it = row.data[0]
    if not _labeler_can_access(db, labeler, it.get("project_id")):
        raise HTTPException(status_code=403, detail="You are not assigned to this project.")
    if it["status"] == "done":
        return SubmissionResponse(ok=True, message="Already done.")
    if labeler["id"] != "admin" and it.get("assigned_to") != labeler["id"]:
        raise HTTPException(status_code=409, detail="This item is not yours.")

    try:
        mx = (
            db.table("project_items").select("idx").eq("project_id", it["project_id"])
            .order("idx", desc=True).limit(1).execute()
        )
        new_idx = (mx.data[0]["idx"] + 1) if mx.data else 0
        db.table("project_items").update(
            {"status": "pending", "assigned_to": None, "claimed_at": None, "idx": new_idx}
        ).eq("id", body.item_id).execute()
    except Exception as exc:
        logger.error("Skip failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not skip.")

    audit.record(db, item_id=body.item_id, project_id=it.get("project_id"), action=audit.SKIP,
                 actor_id=labeler["id"], actor_name=labeler["name"], source="app", value=None)
    return SubmissionResponse(ok=True, message="Skipped.")


@router.get("/work/home", summary="Contributor home: available projects + personal stats")
def work_home(x_work_code: str | None = Header(default=None)):
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    labeler = _resolve_labeler(db, x_work_code)
    if not labeler:
        raise HTTPException(status_code=403, detail="Invalid or inactive access code.")

    name = labeler["name"]

    # Isolation: a clinician only sees projects they're assigned to; the operator sees all.
    allowed = None
    if labeler["id"] != "admin":
        try:
            a = db.table("project_clinicians").select("project_id").eq("clinician_id", labeler["id"]).execute()
            allowed = {r["project_id"] for r in (a.data or [])}
        except Exception as exc:
            logger.error("Assigned-projects lookup failed (scoping to none): %s", exc)
            allowed = set()

    try:
        items = db.table("project_items").select("project_id,status,labeled_by,labeled_at").execute()
    except Exception as exc:
        logger.error("Work home aggregation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not load your work.")

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    days = [now.date() - timedelta(days=i) for i in range(6, -1, -1)]   # oldest → today
    daily_map = {d.isoformat(): 0 for d in days}

    by_proj: dict[str, dict[str, int]] = {}
    mine = 0
    this_week = 0
    my_projects: set[str] = set()
    my_dates: set = set()
    my_by_proj: dict[str, dict[str, int]] = {}   # per-project counts of MY labels (total / this week)
    recent: list[tuple[str, str]] = []   # (labeled_at, project_id)

    for it in (items.data or []):
        pid = it["project_id"]
        if allowed is not None and pid not in allowed:
            continue
        p = by_proj.setdefault(pid, {"total": 0, "done": 0})
        p["total"] += 1
        if it.get("status") == "done":
            p["done"] += 1
        if it.get("labeled_by") == name:
            mine += 1
            my_projects.add(pid)
            mp = my_by_proj.setdefault(pid, {"total": 0, "week": 0})
            mp["total"] += 1
            at = it.get("labeled_at")
            if at:
                recent.append((at, pid))
                try:
                    dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
                    if dt >= week_ago:
                        this_week += 1
                        mp["week"] += 1
                    my_dates.add(dt.date())
                    dk = dt.date().isoformat()
                    if dk in daily_map:
                        daily_map[dk] += 1
                except Exception:
                    pass

    meta: dict[str, dict] = {}
    if by_proj:
        ids = list(by_proj.keys())
        try:
            subs = db.table("project_submissions").select("id,company,rate_per_item,difficulty").in_("id", ids).execute()
            meta = {s["id"]: s for s in (subs.data or [])}
        except Exception:
            # pay/difficulty columns not migrated yet — fall back to names only.
            try:
                subs = db.table("project_submissions").select("id,company").in_("id", ids).execute()
                meta = {s["id"]: s for s in (subs.data or [])}
            except Exception as exc:
                logger.error("Work home meta lookup failed: %s", exc)

    def _name(pid: str) -> str:
        return (meta.get(pid) or {}).get("company") or "Project"

    # current streak: consecutive days with activity ending today (or yesterday)
    streak = 0
    cur = now.date() if now.date() in my_dates else (now.date() - timedelta(days=1))
    while cur in my_dates:
        streak += 1
        cur -= timedelta(days=1)

    # earnings = sum(rate × my labeled items) per project, only where a rate is set
    earned_total = 0.0
    earned_week = 0.0
    for pid, mc in my_by_proj.items():
        rate = (meta.get(pid) or {}).get("rate_per_item")
        if rate:
            earned_total += rate * mc["total"]
            earned_week += rate * mc["week"]

    EST_MIN_PER_ITEM = 1
    projects = []
    for pid, c in by_proj.items():
        m = meta.get(pid) or {}
        pending = c["total"] - c["done"]
        rate = m.get("rate_per_item")
        projects.append({
            "id": pid,
            "company": m.get("company") or "Project",
            "total": c["total"],
            "done": c["done"],
            "pending": pending,
            "difficulty": m.get("difficulty"),
            "payout": round(rate * pending, 2) if rate else None,
            "est_minutes": pending * EST_MIN_PER_ITEM,
        })
    projects.sort(key=lambda x: (-x["pending"], x["company"]))

    recent.sort(reverse=True)
    recent_out = [{"company": _name(pid), "at": at} for at, pid in recent[:6]]
    daily = [daily_map[d.isoformat()] for d in days]

    return {
        "ok": True,
        "name": name,
        "total_labeled": mine,
        "this_week": this_week,
        "streak": streak,
        "earned_total": round(earned_total, 2),
        "earned_week": round(earned_week, 2),
        "active_projects": len(my_projects),
        "daily": daily,
        "recent": recent_out,
        "projects": projects,
    }


@router.get("/work/brief", summary="Project brief + Label Studio link for a clinician")
def work_brief(project_id: str, x_work_code: str | None = Header(default=None)):
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    labeler = _resolve_labeler(db, x_work_code)
    if not labeler:
        raise HTTPException(status_code=403, detail="Invalid or inactive access code.")
    if not _labeler_can_access(db, labeler, project_id):
        raise HTTPException(status_code=403, detail="You are not assigned to this project.")

    try:
        sub = db.table("project_submissions").select("id,company,task_type,ls_project_id,rate_per_item").eq("id", project_id).limit(1).execute()
    except Exception:
        # rate_per_item not migrated yet — fetch without it.
        sub = db.table("project_submissions").select("id,company,task_type,ls_project_id").eq("id", project_id).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="Project not found.")
    s = sub.data[0]
    total, done = _progress(db, project_id)
    pending = total - done
    ls_pid = s.get("ls_project_id")
    ls_link = f"{settings.LS_URL.rstrip('/')}/projects/{ls_pid}/data" if (ls_pid and settings.LS_URL) else None
    rate = s.get("rate_per_item")
    return {
        "ok": True,
        "company": s.get("company") or "Project",
        "task_type": s.get("task_type"),
        "ls_link": ls_link,
        "total": total,
        "done": done,
        "pending": pending,
        "rate_per_item": rate,
        "payout": round(rate * pending, 2) if rate else None,
        "labeler": labeler["name"],
    }


@router.post("/admin/clinicians", summary="Create a clinician + access code (admin)")
def admin_create_clinician(body: ClinicianIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    code = secrets.token_urlsafe(9)
    try:
        row = db.table("clinicians").insert(
            {"name": body.name, "email": body.email, "access_code": code}
        ).execute()
    except Exception as exc:
        logger.error("Create clinician failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not create the clinician.")
    c = row.data[0]
    return {
        "ok": True,
        "clinician": {"id": c["id"], "name": c["name"], "email": c.get("email"), "access_code": code},
    }


@router.post("/admin/assign-clinician", response_model=SubmissionResponse, summary="Assign a clinician to a project (admin)")
def admin_assign_clinician(body: AssignClinicianIn, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    try:
        existing = (
            db.table("project_clinicians").select("id")
            .eq("project_id", body.project_id).eq("clinician_id", body.clinician_id).limit(1).execute()
        )
        if not existing.data:  # idempotent — never double-assign
            db.table("project_clinicians").insert(
                {"project_id": body.project_id, "clinician_id": body.clinician_id}
            ).execute()
    except Exception as exc:
        logger.error("Assign clinician failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not assign the clinician.")
    return SubmissionResponse(ok=True, message="Clinician assigned.")


@router.get("/admin/clinicians", summary="List clinicians (admin)")
def admin_list_clinicians(x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    try:
        rows = (
            db.table("clinicians").select("id,name,email,access_code,active,created_at")
            .order("created_at", desc=True).execute()
        )
    except Exception as exc:
        logger.error("List clinicians failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not load clinicians.")
    return {"ok": True, "clinicians": rows.data or []}


@router.get("/admin/export", summary="Export a project's items + labels (admin)")
def admin_export(project_id: str, x_admin_key: str | None = Header(default=None)):
    _require_admin(x_admin_key)
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    try:
        rows = (
            db.table("project_items").select("idx,content,label,status,labeled_by,labeled_at")
            .eq("project_id", project_id).order("idx").execute()
        )
    except Exception as exc:
        logger.error("Export failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not export.")
    return {"ok": True, "items": rows.data or []}
