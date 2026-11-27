"""Read-only inspection of synthetic pet-retail operational data."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.data_pipeline.generate import DEFAULT_OUT, ROOT
from app.db.connection import get_database_url, get_engine
from app.db.status import database_status
from app.db import read as db_read

router = APIRouter(prefix="/data")
REPORT = ROOT / "reports" / "data_quality_report.json"


def _db_ready() -> bool:
    return database_status()["status"] == "ready"


def _db_engine():
    url = get_database_url()
    if not url:
        raise HTTPException(status_code=503, detail="PostgreSQL is not configured")
    return get_engine(url)



@router.get("/summary")
def dataset_summary() -> dict:
    if _db_ready():
        with _db_engine().connect() as conn:
            return db_read.summary(conn)
    if not REPORT.exists():
        return {"status": "not_generated", "source": "synthetic_csv", "message": "Run the data generator and validator."}
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("status") != "passed" or not (DEFAULT_OUT / "daily_sales.csv.gz").exists():
        return {"status": "unavailable", "source": "synthetic_csv", "message": "Data files missing or failed validation; rerun validation."}
    return {**report, "status": "ready", "source": "synthetic_csv"}


@router.get("/products")
def list_products(limit: int = Query(15, ge=1, le=200), warehouse_id: str = "TX-DAL") -> dict:
    if warehouse_id not in {"TX-DAL", "CA-RIV", "PA-ALL"}:
        raise HTTPException(status_code=422, detail="Unknown warehouse ID")
    if dataset_summary()["status"] != "ready":
        raise HTTPException(status_code=503, detail="Synthetic data is not ready")
    if _db_ready():
        with _db_engine().connect() as conn:
            return db_read.products_for_warehouse(conn, warehouse_id=warehouse_id, limit=limit)
    products = pd.read_csv(DEFAULT_OUT / "products.csv")
    stock = pd.read_csv(DEFAULT_OUT / "inventory_snapshot.csv")
    rows = products.merge(stock[stock["warehouse_id"] == warehouse_id], on="sku_id", how="left")
    return {"fictional": True, "warehouse_id": warehouse_id, "total": int(len(rows)),
            "items": rows.head(limit).fillna({"shelf_life_days": 0}).to_dict(orient="records")}


@router.get("/history")
def sku_history(sku_id: str, warehouse_id: str = "TX-DAL", days: int = Query(90, ge=7, le=365)) -> dict:
    if dataset_summary()["status"] != "ready":
        raise HTTPException(status_code=503, detail="Synthetic data is not ready")
    if _db_ready():
        with _db_engine().connect() as conn:
            result = db_read.sales_history(conn, sku_id=sku_id, warehouse_id=warehouse_id, days=days)
            if not result["records"]:
                raise HTTPException(status_code=404, detail="SKU or warehouse history not found")
            return result
    products = pd.read_csv(DEFAULT_OUT / "products.csv", usecols=["sku_id"])
    if sku_id not in set(products["sku_id"]) or warehouse_id not in {"TX-DAL", "CA-RIV", "PA-ALL"}:
        raise HTTPException(status_code=404, detail="SKU or warehouse does not exist")
    records = []
    for chunk in pd.read_csv(DEFAULT_OUT / "daily_sales.csv.gz", chunksize=60000):
        matches = chunk[(chunk["sku_id"] == sku_id) & (chunk["warehouse_id"] == warehouse_id)]
        if not matches.empty:
            records.extend(matches[["date", "units_sold", "closing_stock", "stockout_observed", "promo_active"]].to_dict(orient="records"))
    return {"fictional": True, "sku_id": sku_id, "warehouse_id": warehouse_id,
            "records": records[-days:], "note": "Observed sales may be censored by stockouts; no latent customer demand is exposed."}
