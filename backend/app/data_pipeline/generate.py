"""Reproducible synthetic US pet-retail dataset.

Run from the repository root:
  python -m backend.app.data_pipeline.generate

The separate latent_demand_truth file is evaluation-only and MUST NEVER be
loaded into production forecasting features. All entities are fictional.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]  # repository root
DEFAULT_OUT = ROOT / "data" / "synthetic"
DEFAULT_END = date.today().isoformat()
WAREHOUSES = [
    {"warehouse_id": "TX-DAL", "city": "Dallas", "state": "Texas", "region": "South", "demand_factor": 1.18},
    {"warehouse_id": "CA-RIV", "city": "Riverside", "state": "California", "region": "West", "demand_factor": 1.30},
    {"warehouse_id": "PA-ALL", "city": "Allentown", "state": "Pennsylvania", "region": "Northeast", "demand_factor": 0.89},
]
# Quantity and prices are synthetic scenario assumptions, not retailer data.
CATEGORY_PLAN = [
    ("Dog Food", 40, 13.0, 49.99, 365),
    ("Cat Food", 30, 10.0, 35.99, 365),
    ("Treats", 30, 5.0, 15.99, 270),
    ("Toys", 20, 2.5, 18.99, None),
    ("Grooming", 20, 3.5, 24.99, 540),
    ("Accessories", 10, 1.8, 29.99, None),
]


def make_catalog(n_skus: int, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    suppliers = []
    for idx in range(1, 13):
        suppliers.append({
            "supplier_id": f"SUP-{idx:02d}",
            "supplier_name": f"Fictional Pet Supply Partner {idx:02d}",
            "lead_time_days": int(rng.integers(4, 17)),
            "min_order_qty": int(rng.choice([24, 48, 72])),
            "case_pack": int(rng.choice([6, 12, 24])),
        })
    products = []
    for idx in range(n_skus):
        cat_index = next(
            (k for k, (_, count, *_rest) in enumerate(CATEGORY_PLAN)
             if idx < sum(x[1] for x in CATEGORY_PLAN[: k + 1])),
            len(CATEGORY_PLAN) - 1,
        ) if idx < 150 else idx % len(CATEGORY_PLAN)
        category, _, base, price, shelf_life = CATEGORY_PLAN[cat_index]
        supplier = suppliers[idx % len(suppliers)]
        local_idx = 1 + sum(1 for row in products if row["category"] == category)
        product_name = (
            "Premium Adult Dog Food 15 lb" if idx == 0 else
            f"{category} {['Essential', 'Premium', 'Everyday'][idx % 3]} Variant {local_idx:02d}"
        )
        products.append({
            "sku_id": "DOG-FOOD-15LB-001" if idx == 0 else f"PET-{idx+1:04d}",
            "product_name": product_name,
            "category": category,
            "supplier_id": supplier["supplier_id"],
            "unit_price_usd": round(float(price * rng.uniform(.8, 1.35)), 2),
            "unit_cost_usd": round(float(price * rng.uniform(.37, .60)), 2),
            "shelf_life_days": shelf_life,
            "repeat_purchase": category in {"Dog Food", "Cat Food", "Treats"},
            "base_daily_demand": round(float(base * rng.uniform(.38, 1.8)), 3),
        })
    return pd.DataFrame(products), pd.DataFrame(suppliers)


def make_promotions(products: pd.DataFrame, dates: pd.DatetimeIndex, rng: np.random.Generator):
    periods = []
    masks = {}
    discounts = {}
    for sku in products["sku_id"]:
        active = np.zeros(len(dates), dtype=bool)
        discount = np.zeros(len(dates), dtype=float)
        for _ in range(max(2, round(len(dates) / 95))):
            start = int(rng.integers(0, max(1, len(dates) - 4)))
            duration = int(rng.integers(3, 11))
            finish = min(len(dates), start + duration)
            pct = float(rng.choice([.10, .15, .20, .25, .30]))
            active[start:finish] = True
            discount[start:finish] = np.maximum(discount[start:finish], pct)
            periods.append({
                "promotion_id": f"PROMO-{len(periods)+1:05d}",
                "sku_id": sku,
                "start_date": dates[start].date().isoformat(),
                "end_date": dates[finish - 1].date().isoformat(),
                "discount_pct": pct,
            })
        masks[sku], discounts[sku] = active, discount
    return pd.DataFrame(periods), masks, discounts


def build_dataset(n_skus: int = 150, days: int = 730, seed: int = 20261120,
                  end_date: str = DEFAULT_END, out: Path = DEFAULT_OUT) -> dict:
    if not 1 <= n_skus <= 1500:
        raise ValueError("n_skus must be between 1 and 1500")
    if not 28 <= days <= 3650:
        raise ValueError("days must be between 28 and 3650")
    end = date.fromisoformat(end_date)
    dates = pd.date_range(end - timedelta(days=days-1), end, freq="D")
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation_only").mkdir(exist_ok=True)
    products, suppliers = make_catalog(n_skus, rng)
    promo_df, promo_mask, promo_discount = make_promotions(products, dates, rng)
    warehouse_df = pd.DataFrame(WAREHOUSES)
    supplier_map = suppliers.set_index("supplier_id").to_dict("index")

    sales_rows = []
    truth_rows = []
    order_rows = []
    snapshot_rows = []
    lot_rows = []
    n = len(dates)
    weekday = dates.dayofweek.to_numpy()
    months = dates.month.to_numpy()

    for p in products.itertuples(index=False):
        supplier = supplier_map[p.supplier_id]
        promotion = promo_mask[p.sku_id]
        discount = promo_discount[p.sku_id]
        for w in WAREHOUSES:
            regional = w["demand_factor"] * float(rng.uniform(.78, 1.22))
            # Preserve different curves for fast/slow movers and seasonality.
            repeat_phase = int(rng.integers(0, 28))
            repeat_cycle = np.where((np.arange(n) + repeat_phase) % 28 < 5, 1.22, .96) if p.repeat_purchase else 1.0
            annual = 1 + .12 * np.sin(2 * np.pi * (np.arange(n) + 60) / 365.25)
            weekend = np.where(weekday >= 5, 1.12 if p.category in {"Toys", "Treats"} else .98, 1.)
            holiday = np.where(np.isin(months, [11, 12]), 1.55 if p.category in {"Toys", "Accessories"} else 1.08, 1.)
            summer = np.where(np.isin(months, [6, 7, 8]), 1.17 if p.category == "Grooming" else 1., 1.)
            slow_trend = 1 + np.linspace(-.04, .08, n)
            mean_demand = np.maximum(.01, p.base_daily_demand * regional * annual * weekend * holiday * summer * repeat_cycle * slow_trend * (1 + 1.5 * discount))
            if p.category in {"Toys", "Accessories"}:
                # More intermittent sales than frequently replenished pet food.
                spike = rng.random(n) < .19
                mean_demand *= np.where(spike, 2.8, .55)
            latent = rng.poisson(mean_demand).astype(int)
            # Rare unexpected demand spikes provide stress events in ground truth.
            for shock in rng.choice(n, size=max(1, n // 150), replace=False):
                latent[shock] += int(rng.integers(6, 25))
            base = float(p.base_daily_demand) * regional
            lead = int(supplier["lead_time_days"])
            opening = int(math.ceil(base * (lead + 13)))
            pending: dict[int, int] = defaultdict(int)
            po_scheduled: list[dict] = []
            for d, stamp in enumerate(dates):
                receipts = pending.pop(d, 0)
                available = opening + receipts
                sold = min(int(latent[d]), available)
                closing = available - sold
                sales_rows.append((stamp.date().isoformat(), p.sku_id, w["warehouse_id"],
                                   sold, opening, receipts, closing, int(closing == 0),
                                   int(promotion[d]), round(float(discount[d]), 2),
                                   round(p.unit_price_usd * (1 - discount[d]), 2)))
                truth_rows.append((stamp.date().isoformat(), p.sku_id, w["warehouse_id"], int(latent[d]), int(latent[d]) - sold))
                if d % 7 == 0:
                    current_on_order = sum(pending.values())
                    # Fixed-rule historical order policy; no future latent demand used.
                    target = math.ceil(base * (lead + 7) * 1.12 + 1.65 * math.sqrt(max(1., base * (lead + 7))))
                    need = target - (closing + current_on_order)
                    if need > 0:
                        qty = max(int(supplier["min_order_qty"]), math.ceil(need / int(supplier["case_pack"])) * int(supplier["case_pack"]))
                        delay = int(rng.integers(4, 12)) if rng.random() < .06 else 0
                        arrival_day = d + lead + delay
                        pending[arrival_day] += qty
                        order_rows.append({
                            "po_id": f"PO-{len(order_rows)+1:07d}", "sku_id": p.sku_id,
                            "warehouse_id": w["warehouse_id"], "supplier_id": p.supplier_id,
                            "order_date": stamp.date().isoformat(), "ordered_units": qty,
                            "expected_arrival_date": (stamp.date() + timedelta(days=lead)).isoformat(),
                            "scheduled_arrival_date": (stamp.date() + timedelta(days=lead + delay)).isoformat(),
                            "status_as_of_snapshot": "received" if arrival_day < n else "open",
                        })
                opening = closing
            on_order = sum(pending.values())
            reserved = int(min(closing, rng.integers(0, max(2, math.ceil(closing * .05) + 1))))
            snapshot_rows.append({
                "as_of_date": end.isoformat(), "sku_id": p.sku_id, "warehouse_id": w["warehouse_id"],
                "on_hand_units": closing, "reserved_units": reserved,
                "on_order_units": on_order, "backorder_units": 0,
                "supplier_id": p.supplier_id,
            })
            if pd.notna(p.shelf_life_days) and closing > 0:
                qty1 = closing if closing < 2 else int(max(1, closing * rng.uniform(.2, .5)))
                quantities = [qty1] if qty1 == closing else [qty1, closing - qty1]
                for lot_num, lot_qty in enumerate(quantities, 1):
                    remaining = (int(rng.integers(7, 46)) if lot_num == 1 and rng.random() < .23
                                 else int(rng.integers(max(8, int(p.shelf_life_days) // 5), int(p.shelf_life_days) + 1)))
                    lot_rows.append({
                        "lot_id": f"LOT-{p.sku_id}-{w['warehouse_id']}-{lot_num}",
                        "sku_id": p.sku_id, "warehouse_id": w["warehouse_id"],
                        "units": lot_qty, "expiry_date": (end + timedelta(days=remaining)).isoformat(),
                    })

    sales = pd.DataFrame(sales_rows, columns=["date", "sku_id", "warehouse_id", "units_sold", "opening_stock", "received_units", "closing_stock", "stockout_observed", "promo_active", "discount_pct", "unit_price_usd"])
    truth = pd.DataFrame(truth_rows, columns=["date", "sku_id", "warehouse_id", "latent_demand", "unobserved_lost_sales"])
    products.to_csv(out / "products.csv", index=False)
    suppliers.to_csv(out / "suppliers.csv", index=False)
    warehouse_df.to_csv(out / "warehouses.csv", index=False)
    promo_df.to_csv(out / "promotions.csv", index=False)
    sales.to_csv(out / "daily_sales.csv.gz", index=False, compression="gzip")
    pd.DataFrame(order_rows).to_csv(out / "purchase_orders.csv.gz", index=False, compression="gzip")
    pd.DataFrame(snapshot_rows).to_csv(out / "inventory_snapshot.csv", index=False)
    pd.DataFrame(lot_rows, columns=["lot_id", "sku_id", "warehouse_id", "units", "expiry_date"]).to_csv(out / "stock_lots.csv", index=False)
    truth.to_csv(out / "evaluation_only" / "latent_demand_truth.csv.gz", index=False, compression="gzip")
    metadata = {
        "dataset_version": "synthetic-pet-retail-v1", "fictional": True,
        "seed": seed, "start_date": dates[0].date().isoformat(), "end_date": end.isoformat(),
        "days": days, "products": n_skus, "warehouses": len(WAREHOUSES),
        "generated_sales_rows": len(sales),
        "warning": "All data are synthetic. Latent demand truth is evaluation-only, never a forecasting feature.",
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def main():
    p = argparse.ArgumentParser(description="Generate synthetic pet retail demand and stock movements")
    p.add_argument("--skus", type=int, default=150)
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--seed", type=int, default=20261120)
    p.add_argument("--end-date", default=DEFAULT_END)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()
    meta = build_dataset(args.skus, args.days, args.seed, args.end_date, args.out)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
