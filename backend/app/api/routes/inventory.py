"""Inventory recommendations plus local planner review (never places purchase orders)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
import hashlib
import json
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from app.db.connection import get_engine, get_database_url

from app.api.routes import ml_forecasting
from app.db.status import database_status
from app.inventory.policy import recommend
from app.inventory.records import read_inventory_facts
from app.inventory.decisions import fingerprint, save_choice, list_choices, DecisionConflict
from app.inventory.simulation import report_status
from app.inventory.ml_simulation import report_status as ml_replay_report_status

router = APIRouter(prefix="/inventory")


@router.get("/status")
def status() -> dict:
    db = database_status()
    model = ml_forecasting._status()
    return {
        "status": "ready" if db["status"] == "ready" and model["status"] == "ready" else "prerequisites_missing",
        "database": db["status"], "forecast_model": model["status"],
        "approval_workflow": "local_demo_review_only", "mode": "no_purchase_orders_issued",
    }




@router.get("/worklist")
def inventory_worklist() -> dict:
    """Operational queue for all SKU-locations; uses the same order policy as detail pages."""
    from app.forecasting.lightgbm_model import ML_PREVIEW_FILE
    from app.inventory.worklist import build_worklist
    import pandas as pd

    if database_status()["status"] != "ready":
        raise HTTPException(503, detail="Imported PostgreSQL inventory data is required")
    model = ml_forecasting._status()
    if model["status"] != "ready":
        raise HTTPException(503, detail="A current trained forecast with calibrated intervals is required")
    try:
        with get_engine(get_database_url()).connect() as conn:
            snapshots = [dict(r) for r in conn.execute(text("""
                SELECT i.*, p.product_name, p.category, s.supplier_name, s.lead_time_days,
                       s.min_order_qty, s.case_pack, w.city, w.state
                  FROM inventory_snapshot i
                  JOIN products p ON p.sku_id=i.sku_id
                  JOIN suppliers s ON s.supplier_id=i.supplier_id AND s.supplier_id=p.supplier_id
                  JOIN warehouses w ON w.warehouse_id=i.warehouse_id
                 WHERE i.as_of_date=(SELECT MAX(as_of_date) FROM inventory_snapshot)
            """)).mappings().all()]
            pos = [dict(r) for r in conn.execute(text("""
                SELECT po_id, sku_id, warehouse_id, ordered_units, expected_arrival_date
                  FROM purchase_orders WHERE status_as_of_snapshot='open'
            """)).mappings().all()]
            lots = [dict(r) for r in conn.execute(text("""
                SELECT sku_id, warehouse_id, units, expiry_date FROM stock_lots
            """)).mappings().all()]
            reviews = [dict(r) for r in conn.execute(text("""
                SELECT sku_id, warehouse_id, as_of_date, model_version, review_period_days,
                       snapshot_hash, action, approved_units FROM planner_decisions
            """)).mappings().all()]
        preview = pd.read_csv(ML_PREVIEW_FILE, dtype={"sku_id": "string", "warehouse_id": "string"})
        return build_worklist(snapshots, pos, lots, preview, model, decisions=reviews)
    except (SQLAlchemyError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, detail="Inventory worklist unavailable or inconsistent; check the current import and forecast artifacts") from None


@router.get("/recommendation")
def recommendation(sku_id: str = Query(min_length=2, max_length=80),
                   warehouse_id: str = Query(min_length=2, max_length=30),
                   review_period_days: int = Query(default=7, ge=1, le=14)) -> dict:
    if database_status()["status"] != "ready":
        raise HTTPException(503, detail="Imported PostgreSQL inventory data is required")
    model = ml_forecasting._status()
    if model["status"] != "ready":
        raise HTTPException(503, detail="A current trained forecast with calibrated intervals is required")
    try:
        facts = read_inventory_facts(sku_id, warehouse_id)
        if not facts:
            raise HTTPException(404, detail="SKU–warehouse snapshot not found")
        series = ml_forecasting.ml_series(sku_id=sku_id, warehouse_id=warehouse_id)
        if series["model_version"] != model["model_version"]:
            raise ValueError("Model version changed while generating recommendation")
        if model["future"]["cutoff"] != facts["as_of_date"].isoformat():
            raise ValueError("Forecast cutoff must equal inventory snapshot date")
        result = recommend(
            as_of_date=facts["as_of_date"], sku_id=sku_id, warehouse_id=warehouse_id,
            product_name=facts["product_name"], supplier_name=facts["supplier_name"],
            model_version=series["model_version"],
            forecast=series["forecast"], weekly_forecast=series["weekly_forecast"],
            on_hand=facts["on_hand_units"], reserved=facts["reserved_units"],
            on_order=facts["on_order_units"], backorder=facts["backorder_units"],
            lead_time_days=facts["lead_time_days"],
            minimum_order_qty=facts["minimum_order_qty"], case_pack=facts["case_pack"],
            incoming=facts["incoming"], lots=facts["lots"],
            review_period_days=review_period_days,
        )
        # Hash the exact dated forecast + incoming deliveries as well as aggregate facts.
        result["forecast_signature"] = hashlib.sha256(json.dumps(
            {"forecast": series["forecast"], "weekly": series["weekly_forecast"]},
            sort_keys=True, default=str).encode()).hexdigest()
        result["incoming_signature"] = hashlib.sha256(json.dumps(
            [(x.po_id, x.units, x.arrival.isoformat()) for x in facts["incoming"]],
            sort_keys=True).encode()).hexdigest()
        result["snapshot_hash"] = fingerprint(result)
        return result
    except HTTPException:
        raise
    except (SQLAlchemyError, OSError):
        raise HTTPException(503, detail="Inventory source currently unavailable") from None
    except (ValueError, KeyError, TypeError, OverflowError):
        raise HTTPException(409, detail="Inventory or forecast snapshot inconsistent; review inputs before ordering") from None


class PlannerReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku_id: str = Field(min_length=2, max_length=80)
    warehouse_id: str = Field(min_length=2, max_length=30)
    review_period_days: int = Field(default=7, ge=1, le=14)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: str = Field(pattern=r"^(approve|override)$")
    approved_units: StrictInt | None = Field(default=None, ge=0, le=1000000)
    reason: str | None = Field(default=None, max_length=500)


@router.post("/decisions", status_code=201)
def create_planner_review(body: PlannerReview) -> dict:
    # Do not accept the browser's supplied quantities as a computed recommendation.
    # Recompute from current PostgreSQL / approved trained model on every write.
    current = recommendation(sku_id=body.sku_id,
                             warehouse_id=body.warehouse_id,
                             review_period_days=body.review_period_days)
    if current["snapshot_hash"] != body.snapshot_hash:
        raise HTTPException(409, detail="Recommendation changed; refresh and review the new calculation")
    try:
        return save_choice(current, body.action, body.approved_units, body.reason)
    except DecisionConflict as exc:
        raise HTTPException(409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    except (RuntimeError, SQLAlchemyError, OSError):
        raise HTTPException(503, detail="Decision store unavailable; no review saved") from None


@router.get("/decisions")
def planner_reviews(sku_id: str = Query(min_length=2, max_length=80),
                    warehouse_id: str = Query(min_length=2, max_length=30)) -> dict:
    if database_status()["status"] != "ready":
        raise HTTPException(503, detail="PostgreSQL unavailable")
    try:
        return {"status": "ready", "records": list_choices(sku_id, warehouse_id),
                "note": "Local-demo planner reviews only. No real purchase orders have been placed."}
    except (RuntimeError, SQLAlchemyError, OSError):
        raise HTTPException(503, detail="Decision store unavailable") from None


@router.get("/simulation/status")
def simulation_status() -> dict:
    result = report_status()
    # Never show a report generated against an unrelated dataset after reimport.
    db = database_status()
    if result["status"] == "ready":
        if db.get("status") != "ready" or db.get("dataset_version") != result.get("dataset_version"):
            return {"status": "stale", "message": "Re-run the inventory policy replay for the current dataset"}
        try:
            with get_engine(get_database_url()).connect() as conn:
                imported_sha = conn.execute(text("SELECT source_sha256 FROM dataset_imports WHERE id=1")).scalar_one()
            if imported_sha != result.get("dataset_sha256"):
                return {"status": "stale", "message": "Dataset fingerprint changed; re-run the policy replay"}
        except (SQLAlchemyError, RuntimeError, OSError, TypeError):
            return {"status": "unavailable", "message": "Unable to verify policy replay provenance"}
    return result


@router.get("/simulation/ml/status")
def ml_simulation_status() -> dict:
    """Expose generated offline ML policy replay only for the current imported dataset."""
    result = ml_replay_report_status()
    if result.get("status") != "ready":
        return result
    db = database_status()
    if db.get("status") != "ready" or db.get("dataset_version") != result.get("dataset_version"):
        return {"status": "stale", "message": "Dataset changed; rerun ML policy replay"}
    try:
        with get_engine(get_database_url()).connect() as conn:
            sha = conn.execute(text("SELECT source_sha256 FROM dataset_imports WHERE id=1")).scalar_one()
        if sha != result.get("dataset_sha256"):
            return {"status": "stale", "message": "Dataset fingerprint changed; rerun ML policy replay"}
    except (SQLAlchemyError, RuntimeError, OSError, TypeError):
        return {"status": "unavailable", "message": "Cannot verify policy replay provenance"}
    return result
