"""Safe database status: never show credentials or raw connection exceptions."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.connection import get_database_url, get_engine
from app.db.read import current_import


def database_status() -> dict:
    try:
        url = get_database_url()
    except ValueError:
        return {"status": "invalid_config", "configured": True, "source": "postgresql"}
    if url is None:
        return {"status": "not_configured", "configured": False, "source": "postgresql"}
    try:
        with get_engine(url).connect() as conn:
            conn.execute(text("SELECT 1"))
            if not conn.execute(text("SELECT to_regclass('public.dataset_imports') IS NOT NULL")).scalar_one():
                return {"status": "not_migrated", "configured": True, "source": "postgresql"}
            result = current_import(conn)
            if result is None:
                return {"status": "empty", "configured": True, "source": "postgresql"}
            return {"status": "ready", "configured": True, "source": "postgresql",
                    "dataset_version": result["dataset_version"],
                    "data_as_of_date": result["as_of_date"].isoformat(),
                    "imported_at": result["imported_at"].isoformat()}
    except (SQLAlchemyError, ImportError, OSError):
        return {"status": "unavailable", "configured": True, "source": "postgresql"}
