"""Relational tables for the fictional pet-retail operational dataset.

Never add the evaluation_only latent-demand file to this schema: it is
synthetic ground truth unavailable to forecasting applications.
"""
from sqlalchemy import (
    Column, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, MetaData, Numeric, SmallInteger, String, Table, Text,
    Boolean, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

suppliers = Table(
    "suppliers", metadata,
    Column("supplier_id", String(30), primary_key=True),
    Column("supplier_name", Text, nullable=False),
    Column("lead_time_days", Integer, nullable=False),
    Column("min_order_qty", Integer, nullable=False),
    Column("case_pack", Integer, nullable=False),
    CheckConstraint("lead_time_days > 0", name="ck_suppliers_lead"),
    CheckConstraint("min_order_qty > 0 AND case_pack > 0", name="ck_suppliers_min_pack"),
)

warehouses = Table(
    "warehouses", metadata,
    Column("warehouse_id", String(30), primary_key=True),
    Column("city", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("region", Text, nullable=False),
    Column("demand_factor", Numeric(9, 4), nullable=False),
)

products = Table(
    "products", metadata,
    Column("sku_id", String(80), primary_key=True),
    Column("product_name", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("supplier_id", String(30), ForeignKey("suppliers.supplier_id"), nullable=False),
    Column("unit_price_usd", Numeric(12, 2), nullable=False),
    Column("unit_cost_usd", Numeric(12, 2), nullable=False),
    Column("shelf_life_days", Numeric(8, 1), nullable=True),
    Column("repeat_purchase", Boolean, nullable=False),
    Column("base_daily_demand", Numeric(12, 3), nullable=False),
    Index("ix_products_category", "category"),
)

promotions = Table(
    "promotions", metadata,
    Column("promotion_id", String(40), primary_key=True),
    Column("sku_id", String(80), ForeignKey("products.sku_id"), nullable=False),
    Column("start_date", Date, nullable=False),
    Column("end_date", Date, nullable=False),
    Column("discount_pct", Numeric(5, 4), nullable=False),
    CheckConstraint("end_date >= start_date", name="ck_promo_date"),
    Index("ix_promotions_sku_start", "sku_id", "start_date"),
)

daily_sales = Table(
    "daily_sales", metadata,
    Column("date", Date, primary_key=True),
    Column("sku_id", String(80), ForeignKey("products.sku_id"), primary_key=True),
    Column("warehouse_id", String(30), ForeignKey("warehouses.warehouse_id"), primary_key=True),
    Column("units_sold", Integer, nullable=False),
    Column("opening_stock", Integer, nullable=False),
    Column("received_units", Integer, nullable=False),
    Column("closing_stock", Integer, nullable=False),
    Column("stockout_observed", SmallInteger, nullable=False),
    Column("promo_active", SmallInteger, nullable=False),
    Column("discount_pct", Numeric(5, 4), nullable=False),
    Column("unit_price_usd", Numeric(12, 2), nullable=False),
    CheckConstraint("units_sold >= 0 AND opening_stock >= 0 AND received_units >= 0 AND closing_stock >= 0", name="ck_sales_stock_nonnegative"),
    CheckConstraint("stockout_observed IN (0,1) AND promo_active IN (0,1)", name="ck_sales_boolean_flags"),
    CheckConstraint("opening_stock + received_units - units_sold = closing_stock", name="ck_sales_balance"),
    Index("ix_sales_sku_warehouse_date", "sku_id", "warehouse_id", "date"),
    Index("ix_sales_warehouse_date", "warehouse_id", "date"),
)

purchase_orders = Table(
    "purchase_orders", metadata,
    Column("po_id", String(40), primary_key=True),
    Column("sku_id", String(80), ForeignKey("products.sku_id"), nullable=False),
    Column("warehouse_id", String(30), ForeignKey("warehouses.warehouse_id"), nullable=False),
    Column("supplier_id", String(30), ForeignKey("suppliers.supplier_id"), nullable=False),
    Column("order_date", Date, nullable=False),
    Column("ordered_units", Integer, nullable=False),
    Column("expected_arrival_date", Date, nullable=False),
    Column("scheduled_arrival_date", Date, nullable=False),
    Column("status_as_of_snapshot", String(12), nullable=False),
    CheckConstraint("ordered_units > 0", name="ck_po_units"),
    CheckConstraint("status_as_of_snapshot IN ('open', 'received')", name="ck_po_status"),
    Index("ix_po_location_status", "sku_id", "warehouse_id", "status_as_of_snapshot"),
)

inventory_snapshot = Table(
    "inventory_snapshot", metadata,
    Column("as_of_date", Date, primary_key=True),
    Column("sku_id", String(80), ForeignKey("products.sku_id"), primary_key=True),
    Column("warehouse_id", String(30), ForeignKey("warehouses.warehouse_id"), primary_key=True),
    Column("on_hand_units", Integer, nullable=False),
    Column("reserved_units", Integer, nullable=False),
    Column("on_order_units", Integer, nullable=False),
    Column("backorder_units", Integer, nullable=False),
    Column("supplier_id", String(30), ForeignKey("suppliers.supplier_id"), nullable=False),
    CheckConstraint("on_hand_units >= 0 AND reserved_units >= 0 AND on_order_units >= 0 AND backorder_units >= 0", name="ck_snapshot_nonnegative"),
    CheckConstraint("reserved_units <= on_hand_units", name="ck_snapshot_reserved"),
    Index("ix_snapshot_warehouse", "warehouse_id", "as_of_date"),
)

stock_lots = Table(
    "stock_lots", metadata,
    Column("lot_id", String(128), primary_key=True),
    Column("sku_id", String(80), ForeignKey("products.sku_id"), nullable=False),
    Column("warehouse_id", String(30), ForeignKey("warehouses.warehouse_id"), nullable=False),
    Column("units", Integer, nullable=False),
    Column("expiry_date", Date, nullable=False),
    CheckConstraint("units >= 0", name="ck_lot_units"),
    Index("ix_lots_sku_warehouse_expiry", "sku_id", "warehouse_id", "expiry_date"),
)

dataset_imports = Table(
    "dataset_imports", metadata,
    Column("id", Integer, primary_key=True),
    Column("dataset_version", String(80), nullable=False),
    Column("source_sha256", String(64), nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("quality_report", JSONB, nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("id = 1", name="ck_single_dataset"),
)

# Strict allowlist and dependency order. There is intentionally no evaluation_only data.
IMPORT_FILES = (
    ("suppliers", "suppliers.csv"),
    ("warehouses", "warehouses.csv"),
    ("products", "products.csv"),
    ("promotions", "promotions.csv"),
    ("daily_sales", "daily_sales.csv.gz"),
    ("purchase_orders", "purchase_orders.csv.gz"),
    ("inventory_snapshot", "inventory_snapshot.csv"),
    ("stock_lots", "stock_lots.csv"),
)
