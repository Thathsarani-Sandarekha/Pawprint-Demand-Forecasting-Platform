"""Read only, version-aware LightGBM training results (never fit in HTTP)."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.connection import get_database_url, get_engine
from app.db.status import database_status
from app.forecasting.lightgbm_model import ML_PREVIEW_FILE, ML_REPORT_FILE, MODEL_FILE
from app.forecasting.uncertainty import week_slices

router = APIRouter(prefix="/forecasting/ml")


def _status():
    if not ML_REPORT_FILE.is_file() or not ML_PREVIEW_FILE.is_file() or not MODEL_FILE.is_file():
        return {"status": "not_run", "message": "Run python -m app.forecasting.lightgbm_model from backend."}
    try:
        report = json.loads(ML_REPORT_FILE.read_text(encoding="utf-8"))
        if report.get("status") != "ready" or report.get("model_type") != "trained_ml_comparison":
            raise ValueError("Invalid training report")
        bands = report.get("prediction_intervals")
        if not isinstance(bands, dict) or len(bands.get("daily_quantiles", [])) != report.get("horizon_days") or len(bands.get("weekly_quantiles", [])) != len(week_slices(report["horizon_days"])):
            return {"status": "stale", "message": "Existing artifacts lack calibrated intervals; rerun LightGBM training"}
        if hashlib.sha256(MODEL_FILE.read_bytes()).hexdigest() != report.get("model_artifact_sha256"):
            return {"status": "invalid", "message": "Trained model artifact checksum does not match report"}
    except (OSError, ValueError, TypeError):
        return {"status": "invalid", "message": "ML artifacts unavailable or malformed; retrain"}
    db = database_status()
    if db["status"] == "ready":
        if report.get("source") != "postgresql" or report.get("dataset_version") != db.get("dataset_version"):
            return {"status": "stale", "message": "ML artifacts do not match imported PostgreSQL dataset"}
        try:
            with get_engine(get_database_url()).connect() as conn:
                sha = conn.execute(text("SELECT source_sha256 FROM dataset_imports WHERE id=1")).scalar_one()
            if sha != report.get("source_sha256"):
                return {"status": "stale", "message": "Training dataset fingerprint changed"}
        except Exception:
            return {"status": "unavailable", "message": "Could not verify ML provenance"}
    elif report.get("source") == "postgresql":
        return {"status": "stale", "message": "Trained model requires the PostgreSQL dataset"}
    elif db.get("configured"):
        return {"status": "unavailable", "message": "Configured PostgreSQL is unavailable"}
    return report


@router.get("/status")
def ml_status() -> dict:
    return _status()


@router.get("/series")
def ml_series(sku_id: str = Query(min_length=2, max_length=80),
              warehouse_id: str = Query(min_length=2, max_length=30)) -> dict:
    status = _status()
    if status["status"] != "ready":
        raise HTTPException(status_code=503, detail=status.get("message", "Training not complete"))
    try:
        frame = pd.read_csv(ML_PREVIEW_FILE, dtype={"sku_id": "string", "warehouse_id": "string"})
        filtered = frame[(frame.sku_id == sku_id) & (frame.warehouse_id == warehouse_id)]
        if filtered.empty:
            raise HTTPException(status_code=404, detail="Unknown forecast series")
        if len(filtered) != status["horizon_days"] or not filtered["predicted_units"].ge(0).all():
            raise ValueError("Incomplete preview")
        filtered = filtered.sort_values("date")
        required = ["predicted_units", "lower_units", "upper_units", "uncertainty_scale"]
        if not set(required).issubset(filtered.columns) or not np.isfinite(filtered[required].to_numpy(dtype=float)).all():
            raise ValueError("Missing calibrated interval data")
        if (filtered.lower_units > filtered.predicted_units).any() or (filtered.upper_units < filtered.predicted_units).any() or (filtered.lower_units < 0).any():
            raise ValueError("Invalid calibrated intervals")
        scale = float(filtered.uncertainty_scale.iloc[0])
        if scale < 1 or not np.allclose(filtered.uncertainty_scale, scale, atol=1e-3):
            raise ValueError("Inconsistent historical scale")
        interval_meta = status["prediction_intervals"]
        weekly = []
        for i, chunk in enumerate(week_slices(len(filtered))):
            group = filtered.iloc[chunk]
            pred = float(group.predicted_units.sum())
            radius = float(interval_meta["weekly_quantiles"][i]) * scale * len(group)
            weekly.append({"week": i+1, "start_date": str(group.date.iloc[0]),
                           "end_date": str(group.date.iloc[-1]),
                           "predicted_units": round(pred, 2),
                           "lower_units": round(max(0, pred - radius), 2),
                           "upper_units": round(pred + radius, 2)})
        return {
            "sku_id": sku_id, "warehouse_id": warehouse_id,
            "model": status["selected_model"], "model_version": status["model_version"],
            "trained_ml_model": True, "synthetic_data": True,
            "train_through": status["future"]["cutoff"],
            "forecast": filtered[["date", "predicted_units", "lower_units", "upper_units"]].to_dict(orient="records"),
            "weekly_forecast": weekly,
            "nominal_coverage_pct": interval_meta["nominal_coverage_pct"],
            "note": "Empirical 90% nominal intervals derived from prior rolling folds; weekly bands calibrated directly on week-total errors. Coverage may differ from target, particularly for intermittent products and stockout-censored observed sales.",
        }
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError):
        raise HTTPException(status_code=503, detail="Incomplete ML preview; rerun training") from None
