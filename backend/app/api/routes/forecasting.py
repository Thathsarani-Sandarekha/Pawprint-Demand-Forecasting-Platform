"""Read-only API exposing saved, versioned forecasting BASELINE artifacts.

These endpoints never train models on web requests, never leak synthetic latent
customer demand, and do not claim ML forecasts or calibrated uncertainty.
"""
from __future__ import annotations

import json

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.db.connection import get_database_url, get_engine
from app.db.status import database_status
from app.forecasting.baselines import REPORT_FILE, PREVIEW_FILE
from sqlalchemy import text

router = APIRouter(prefix="/forecasting")


def _artifact_status() -> dict:
    if not REPORT_FILE.is_file() or not PREVIEW_FILE.is_file():
        return {"status": "not_run", "model_type": "baseline_only",
                "message": "Run python -m app.forecasting.baselines from backend."}
    try:
        report = json.loads(REPORT_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"status": "invalid", "message": "Forecast report unreadable; rerun baselines."}
    if report.get("status") != "ready" or report.get("model_type") != "baseline_only":
        return {"status": "invalid", "message": "Unexpected baseline report format."}
    db = database_status()
    if db["status"] == "ready":
        if report.get("source") != "postgresql" or report.get("dataset_version") != db.get("dataset_version"):
            return {"status": "stale", "message": "Forecast artifacts do not match the imported PostgreSQL dataset; rerun baselines."}
        try:
            with get_engine(get_database_url()).connect() as conn:
                sha = conn.execute(text("SELECT source_sha256 FROM dataset_imports WHERE id=1")).scalar_one()
            if report.get("source_sha256") != sha:
                return {"status": "stale", "message": "Imported dataset fingerprint changed; rerun baselines."}
        except Exception:
            return {"status": "unavailable", "message": "Could not verify forecast artifact provenance."}
    elif report.get("source") == "postgresql":
        return {"status": "stale", "message": "Artifacts are SQL-based but PostgreSQL is not ready."}
    elif db.get("configured"):
        return {"status": "unavailable", "message": "Configured PostgreSQL is not ready; cannot validate forecasts against it."}
    return report


@router.get("/status")
def forecasting_status() -> dict:
    """Forecast metrics and metadata from a completed offline baseline run."""
    return _artifact_status()


@router.get("/series")
def baseline_series(sku_id: str = Query(min_length=2, max_length=80),
                    warehouse_id: str = Query(min_length=2, max_length=30)) -> dict:
    status = _artifact_status()
    if status["status"] != "ready":
        raise HTTPException(status_code=503, detail=status["message"])
    try:
        df = pd.read_csv(PREVIEW_FILE, dtype={"sku_id": "string", "warehouse_id": "string"})
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Baseline preview unavailable; rerun baselines") from None
    selected = df[(df["sku_id"] == sku_id) & (df["warehouse_id"] == warehouse_id)]
    if selected.empty:
        raise HTTPException(status_code=404, detail="Forecast series not found")
    if len(selected) != status["horizon_days"]:
        raise HTTPException(status_code=503, detail="Forecast artifact incomplete; rerun baselines")
    return {"sku_id": sku_id, "warehouse_id": warehouse_id,
            "model": status["selected_baseline"], "model_type": "baseline_only", "synthetic_data": True,
            "train_through": status["future"]["cutoff"],
            "forecast": selected[["date", "predicted_units"]].to_dict(orient="records"),
            "note": "Forecast uses observed historical sales, which can understate demand during stockouts. No confidence intervals yet."}
