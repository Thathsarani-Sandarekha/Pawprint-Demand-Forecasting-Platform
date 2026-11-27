"""Read-only local model registration status; does not deserialize artifacts."""
from fastapi import APIRouter

from app.mlops.registry import verify

router = APIRouter(prefix="/mlops")


@router.get("/model")
def model_registry_status() -> dict:
    return verify()
