"""API v1 router — Senebiclabs data infrastructure. Aggregates the sub-routers."""
from fastapi import APIRouter
from app.api.v1 import system, project, ls

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(project.router)
api_router.include_router(ls.router)
