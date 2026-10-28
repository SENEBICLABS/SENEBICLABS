"""API v1 router — Senebiclabs data infrastructure. Aggregates the sub-routers."""
from fastapi import APIRouter
from app.api.v1 import system, project

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(project.router)
