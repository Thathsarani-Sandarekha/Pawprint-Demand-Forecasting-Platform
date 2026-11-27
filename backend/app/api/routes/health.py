"""Connectivity endpoint; this does not claim ML/data systems are ready."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import APP_VERSION

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="pet-demand-api", version=APP_VERSION)
