"""Read supplier, inventory, open PO and lot facts from the imported PostgreSQL snapshot."""
from __future__ import annotations

from sqlalchemy import text

from app.db.connection import get_database_url, get_engine
from app.inventory.policy import IncomingOrder, StockLot


def read_inventory_facts(sku_id: str, warehouse_id: str) -> dict | None:
    url = get_database_url()
    if not url:
        raise RuntimeError("PostgreSQL must be configured for inventory decisions")
    with get_engine(url).connect() as conn:
        facts = conn.execute(text("""
            SELECT i.as_of_date, i.sku_id, i.warehouse_id,
                   i.on_hand_units, i.reserved_units, i.on_order_units, i.backorder_units,
                   p.product_name, p.shelf_life_days,
                   s.supplier_name, s.lead_time_days, s.min_order_qty, s.case_pack
              FROM inventory_snapshot i
              JOIN products p ON p.sku_id = i.sku_id
              JOIN suppliers s ON s.supplier_id = i.supplier_id AND s.supplier_id = p.supplier_id
             WHERE i.sku_id = :sku AND i.warehouse_id = :wh
               AND i.as_of_date = (SELECT MAX(as_of_date) FROM inventory_snapshot)
        """), {"sku": sku_id, "wh": warehouse_id}).mappings().first()
        if facts is None:
            return None
        incoming_rows = conn.execute(text("""
            SELECT po_id, ordered_units, expected_arrival_date FROM purchase_orders
             WHERE sku_id = :sku AND warehouse_id = :wh AND status_as_of_snapshot = 'open'
             ORDER BY expected_arrival_date, po_id
        """), {"sku": sku_id, "wh": warehouse_id}).mappings().all()
        lots_rows = conn.execute(text("""
            SELECT units, expiry_date FROM stock_lots
             WHERE sku_id = :sku AND warehouse_id = :wh ORDER BY expiry_date
        """), {"sku": sku_id, "wh": warehouse_id}).mappings().all()
    result = dict(facts)
    result["minimum_order_qty"] = result.pop("min_order_qty")
    result["incoming"] = [IncomingOrder(po_id=row["po_id"], units=row["ordered_units"],
                                         arrival=row["expected_arrival_date"]) for row in incoming_rows]
    result["lots"] = [StockLot(units=row["units"], expiry=row["expiry_date"]) for row in lots_rows]
    return result
