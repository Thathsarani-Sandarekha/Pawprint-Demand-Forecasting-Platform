"""Validate generated pet-retail CSVs before using them for ML or an API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from app.data_pipeline.generate import DEFAULT_OUT, ROOT

REPORT_PATH = ROOT / "reports" / "data_quality_report.json"


class DatasetValidationError(ValueError):
    pass


def validate_dataset(data_dir: Path = DEFAULT_OUT, report_path: Path | None = REPORT_PATH) -> dict:
    """Fail closed on inconsistent data and produce a versioned audit summary."""
    data_dir = Path(data_dir)
    metadata = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
    products = pd.read_csv(data_dir / "products.csv")
    suppliers = pd.read_csv(data_dir / "suppliers.csv")
    warehouses = pd.read_csv(data_dir / "warehouses.csv")
    promos = pd.read_csv(data_dir / "promotions.csv")
    sales = pd.read_csv(data_dir / "daily_sales.csv.gz", parse_dates=["date"])
    stock = pd.read_csv(data_dir / "inventory_snapshot.csv")
    orders = pd.read_csv(data_dir / "purchase_orders.csv.gz")
    lots = pd.read_csv(data_dir / "stock_lots.csv")
    issues: list[str] = []

    def check(condition: bool, description: str):
        if not bool(condition):
            issues.append(description)

    check(len(products) == metadata["products"] and products["sku_id"].is_unique, "Catalog product count or SKU uniqueness invalid")
    check(len(warehouses) == metadata["warehouses"] and warehouses["warehouse_id"].is_unique, "Warehouse identifiers are invalid")
    check(suppliers["supplier_id"].is_unique, "Supplier IDs must be unique")
    check(products["supplier_id"].isin(suppliers["supplier_id"]).all(), "Unknown supplier in product catalog")
    check(products["base_daily_demand"].gt(0).all(), "Base demand must be positive")
    check(products["unit_price_usd"].gt(products["unit_cost_usd"]).all(), "Product price should exceed unit cost in this scenario")
    check(len(sales) == metadata["days"] * len(products) * len(warehouses), "Daily sales does not cover the full SKU-warehouse-date grid")
    check(not sales[["date", "sku_id", "warehouse_id"]].duplicated().any(), "Duplicate SKU-warehouse-day sales records")
    check(sales["sku_id"].isin(products["sku_id"]).all(), "Unknown SKU in daily sales")
    check(sales["warehouse_id"].isin(warehouses["warehouse_id"]).all(), "Unknown warehouse in daily sales")
    check(sales["date"].min() == pd.Timestamp(metadata["start_date"]) and sales["date"].max() == pd.Timestamp(metadata["end_date"]), "Sales date range differs from metadata")
    check(not sales.isna().any().any(), "Missing values in daily sales")
    for col in ["units_sold", "opening_stock", "received_units", "closing_stock"]:
        check(sales[col].ge(0).all(), f"Negative {col}")
    check((sales["opening_stock"] + sales["received_units"] - sales["units_sold"] == sales["closing_stock"]).all(), "Daily stock balance does not reconcile")
    sales_sorted = sales.sort_values(["sku_id", "warehouse_id", "date"])
    previous_close = sales_sorted.groupby(["sku_id", "warehouse_id"])["closing_stock"].shift(1)
    check(sales_sorted.loc[previous_close.notna(), "opening_stock"].eq(previous_close.dropna()).all(), "Opening stock differs from previous day closing stock")
    check((sales["stockout_observed"] == sales["closing_stock"].eq(0).astype(int)).all(), "Stockout flags do not reflect ending inventory")
    check(sales["discount_pct"].between(0, .5).all(), "Invalid promotion discounts")
    check(sales["promo_active"].isin([0, 1]).all(), "Invalid promotion flag")
    check((sales["promo_active"].eq(0) == sales["discount_pct"].eq(0)).all(), "Daily promotion flag inconsistent with discount")
    check(not sales["unit_price_usd"].le(0).any(), "Non-positive transaction unit price")
    check(not promos.empty and promos["sku_id"].isin(products["sku_id"]).all(), "Promotions missing or referencing unknown SKU")
    check(len(stock) == len(products) * len(warehouses), "Inventory snapshot must cover every SKU-warehouse pair")
    check(not stock[["sku_id", "warehouse_id"]].duplicated().any(), "Duplicate inventory snapshots")
    check(stock[["on_hand_units", "reserved_units", "on_order_units", "backorder_units"]].ge(0).all().all(), "Negative inventory snapshot quantity")
    check(stock["reserved_units"].le(stock["on_hand_units"]).all(), "Reserved inventory exceeds physical stock")
    latest = sales.loc[sales["date"] == pd.Timestamp(metadata["end_date"]), ["sku_id", "warehouse_id", "closing_stock"]]
    actual = stock.merge(latest, on=["sku_id", "warehouse_id"], how="left")
    check(actual["closing_stock"].notna().all() and actual["on_hand_units"].eq(actual["closing_stock"]).all(), "Snapshot stock differs from final daily balance")
    check(orders["po_id"].is_unique, "Purchase order IDs are not unique")
    check(orders["ordered_units"].gt(0).all(), "Purchase order quantities must be positive")
    received_orders = orders[orders["status_as_of_snapshot"] == "received"].copy()
    received_orders["scheduled_arrival_date"] = pd.to_datetime(received_orders["scheduled_arrival_date"])
    expected_receipts = received_orders.groupby(
        ["sku_id", "warehouse_id", "scheduled_arrival_date"], as_index=False)["ordered_units"].sum()
    expected_receipts = expected_receipts.rename(columns={"scheduled_arrival_date": "date", "ordered_units": "ordered_receipts"})
    received_compare = sales.merge(expected_receipts, on=["sku_id", "warehouse_id", "date"], how="left")
    check(received_compare["received_units"].eq(received_compare["ordered_receipts"].fillna(0)).all(), "Daily receipts disagree with scheduled received orders")
    open_orders = orders[orders["status_as_of_snapshot"] == "open"]
    open_totals = open_orders.groupby(["sku_id", "warehouse_id"], as_index=False)["ordered_units"].sum().rename(columns={"ordered_units": "open_po_units"})
    stock_with_po = stock.merge(open_totals, on=["sku_id", "warehouse_id"], how="left").fillna({"open_po_units": 0})
    check(stock_with_po["on_order_units"].eq(stock_with_po["open_po_units"]).all(), "Incoming stock disagrees with open purchase orders")
    if not lots.empty:
        check(lots["units"].gt(0).all(), "Lot quantities must be positive")
        check(pd.to_datetime(lots["expiry_date"]).gt(pd.Timestamp(metadata["end_date"])).all(), "Current stock lot is already expired")
        lot_sums = lots.groupby(["sku_id", "warehouse_id"], as_index=False)["units"].sum().rename(columns={"units": "lot_units"})
        with_lots = stock.merge(lot_sums, on=["sku_id", "warehouse_id"], how="left")
        perishable = products[products["shelf_life_days"].notna()]["sku_id"]
        matching = with_lots[with_lots["sku_id"].isin(perishable)]
        check(matching["on_hand_units"].eq(matching["lot_units"].fillna(0)).all(), "Perishable lots must add to on-hand stock")

    # No evaluation-only truth is read here: ML-facing quality checks do not use latent demand.
    by_wh = sales.groupby("warehouse_id", as_index=False).agg(
        units_sold=("units_sold", "sum"),
        stockout_flag_rate=("stockout_observed", "mean"),
        promo_day_rate=("promo_active", "mean"),
    )
    current_wh = stock.groupby("warehouse_id", as_index=False)["on_hand_units"].sum()
    by_wh = by_wh.merge(current_wh, on="warehouse_id").merge(
        warehouses[["warehouse_id", "city", "state"]], on="warehouse_id")
    by_wh["stockout_flag_rate"] = by_wh["stockout_flag_rate"].round(4)
    by_wh["promo_day_rate"] = by_wh["promo_day_rate"].round(4)

    report = {
        "status": "passed" if not issues else "failed",
        "dataset_version": metadata["dataset_version"],
        "fictional": True,
        "data_as_of_date": metadata["end_date"],
        "start_date": metadata["start_date"],
        "end_date": metadata["end_date"],
        "sku_count": int(len(products)),
        "warehouse_count": int(len(warehouses)),
        "sku_warehouse_series": int(len(products) * len(warehouses)),
        "daily_sales_rows": int(len(sales)),
        "units_sold": int(sales["units_sold"].sum()),
        "stockout_flag_rate": round(float(sales["stockout_observed"].mean()), 4),
        "promotion_day_rate": round(float(sales["promo_active"].mean()), 4),
        "open_purchase_orders": int(len(open_orders)),
        "warehouse_snapshot_units": int(stock["on_hand_units"].sum()),
        "warehouse_metrics": by_wh.to_dict(orient="records"),
        "issues": issues,
        "note": "Synthetic demonstration data; stockout flag represents ending stock of zero, not observed lost demand.",
    }
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if issues:
        raise DatasetValidationError("Validation failed: " + "; ".join(issues))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    result = validate_dataset(args.data_dir, args.report)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
