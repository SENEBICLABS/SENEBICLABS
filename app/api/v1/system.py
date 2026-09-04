from datetime import datetime, timezone
from fastapi import APIRouter
from app.core.config import settings
from app.models.schemas import SystemHealth

router = APIRouter(tags=["System"])


@router.get("/health", response_model=SystemHealth, summary="System health check")
def health():
    return SystemHealth(
        status="ok",
        version=settings.VERSION,
        timestamp=datetime.now(timezone.utc),
    )
