"""PostgreSQL readiness endpoint. Does not assert forecasting readiness."""
from fastapi import APIRouter

from app.db.status import database_status

router = APIRouter(prefix="/database")


@router.get("/status")
def get_status() -> dict:
    return database_status()
