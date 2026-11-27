"""Read-only PostgreSQL views with intentionally limited operational columns."""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.engine import Connection

from app.db.models import dataset_imports


def current_import(conn: Connection) -> dict | None:
    row = conn.execute(select(dataset_imports).where(dataset_imports.c.id == 1)).mappings().first()
    return dict(row) if row else None


def summary(conn: Connection) -> dict:
    record = current_import(conn)
    if not record:
        return {"status": "empty", "source": "postgresql", "message": "Run the importer first."}
    # Report originates from strict validation; counts are verified after COPY.
    report = record["quality_report"]
    return {**report, "status": "ready", "source": "postgresql",
            "imported_at": record["imported_at"].isoformat(),
            "source_sha256": record["source_sha256"]}


def products_for_warehouse(conn: Connection, warehouse_id: str, limit: int) -> dict:
    records = conn.execute(text("""
        SELECT p.sku_id, p.product_name, p.category, p.supplier_id,
               p.unit_price_usd::float AS unit_price_usd,
               p.unit_cost_usd::float AS unit_cost_usd,
               p.shelf_life_days::float AS shelf_life_days,
               p.repeat_purchase,
               p.base_daily_demand::float AS base_daily_demand,
               s.as_of_date::text AS as_of_date,
               s.on_hand_units, s.reserved_units, s.on_order_units,
               s.backorder_units
        FROM products p
        JOIN inventory_snapshot s ON s.sku_id = p.sku_id
        WHERE s.warehouse_id = :warehouse_id
          AND s.as_of_date = (SELECT MAX(as_of_date) FROM inventory_snapshot)
        ORDER BY p.sku_id LIMIT :limit
    """), {"warehouse_id": warehouse_id, "limit": limit}).mappings().all()
    count = conn.execute(text("SELECT COUNT(*) FROM products")).scalar_one()
    return {"fictional": True, "warehouse_id": warehouse_id, "total": count,
            "items": [dict(row) for row in records]}


def sales_history(conn: Connection, sku_id: str, warehouse_id: str, days: int) -> dict:
    rows = conn.execute(text("""
        SELECT date::text AS date, units_sold, closing_stock, stockout_observed,
               promo_active FROM daily_sales
        WHERE sku_id = :sku AND warehouse_id = :warehouse
        ORDER BY date DESC LIMIT :days
    """), {"sku": sku_id, "warehouse": warehouse_id, "days": days}).mappings().all()
    return {"fictional": True, "sku_id": sku_id, "warehouse_id": warehouse_id,
            "records": [dict(row) for row in reversed(rows)],
            "note": "Observed sales may be censored by stockouts; no latent customer demand is exposed."}
