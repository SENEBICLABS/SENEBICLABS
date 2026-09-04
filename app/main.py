"""
Senebiclabs — Data Infrastructure for Medical AI.
FastAPI entry point.

Start with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Docs:
    http://localhost:8000/docs
    http://localhost:8000/redoc
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings

app = FastAPI(
    title       = "Senebiclabs — Data Infrastructure for Medical AI",
    description = (
        "Clinician-grade data for medical AI. Certified clinicians evaluate, correct, and "
        "create the data that medical models are trained and measured against, with the "
        "consensus, adjudication, and provenance that make it trustworthy.\n\n"
        "Create a project, ingest data, expert review (N-way consensus + adjudication), "
        "and receive a QA'd deliverable by API or signed webhook."
    ),
    version     = settings.VERSION,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins     = _origins,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
def root():
    return {
        "system":  "Senebiclabs — Data Infrastructure for Medical AI",
        "version": settings.VERSION,
        "docs":    "/docs",
    }
