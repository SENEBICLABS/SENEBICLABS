"""Shared Pydantic schemas for the Senebiclabs data platform.

The data-infrastructure request/response models (ProjectSubmission, SubmissionResponse,
CreateProjectIn, IngestIn, ...) are defined inline in app/api/v1/project.py. This module
holds only the shared system schema.
"""
from datetime import datetime
from pydantic import BaseModel


class SystemHealth(BaseModel):
    status: str
    version: str
    timestamp: datetime
