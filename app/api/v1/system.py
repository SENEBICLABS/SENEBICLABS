import os
from datetime import datetime, timezone
from fastapi import APIRouter
from app.core.config import settings
from app.models.schemas import SystemHealth

router = APIRouter(tags=["System"])

COMMIT_CHARS = 7


def deployed_commit() -> str:
    """The seven characters that say which build is answering, or "unknown".

    `RENDER_GIT_COMMIT` is a documented default environment variable on every Render
    runtime, holding the commit SHA for the deploy. `settings.VERSION` is a constant in the
    source and has never distinguished one deploy from another, so until now there was no
    way to tell from outside which commit was running: a post-merge audit could confirm what
    was on `main` and nothing at all about what was serving requests.

    Whatever the variable holds is reported as it is, truncated, rather than validated. If it
    is ever set to something that is not a SHA, seeing the wrong value is more useful than
    seeing "unknown", which would hide the misconfiguration. Only an absent or blank value
    reads as unknown, because that is the honest answer when nothing said.
    """
    return (os.environ.get("RENDER_GIT_COMMIT") or "").strip()[:COMMIT_CHARS] or "unknown"


@router.get("/health", response_model=SystemHealth, summary="System health check")
def health():
    return SystemHealth(
        status="ok",
        version=settings.VERSION,
        timestamp=datetime.now(timezone.utc),
        commit=deployed_commit(),
    )
