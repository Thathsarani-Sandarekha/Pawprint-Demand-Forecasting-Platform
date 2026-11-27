"""Read-only operational queue from the same inputs and policy as single-SKU recommendations.

No extra model is trained. Results are indicative, dated, and for planning review only.
A failed series is reported as an issue, never silently treated as safe or as zero demand.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from datetime import date
from math import isfinite

import pandas as pd

from app.forecasting.uncertainty import week_slices
from app.inventory.policy import IncomingOrder, StockLot, recommend
from app.inventory.decisions import fingerprint


PRIORITY_RANK = {"expedite": 0, "review": 1, "reorder": 2, "covered": 3}


def build_worklist(snapshot_rows: list[dict], purchase_orders: list[dict], lots: list[dict],
                   forecast_data: pd.DataFrame, report: dict, review_period_days: int = 7, decisions: list[dict] | None = None) -> dict:
    """Compose a dated, sorted list of SKU–warehouse decisions with exact policy math."""
    model_version = report["model_version"]
    cutoff = report["future"]["cutoff"]
    quantiles = report["prediction_intervals"]["weekly_quantiles"]
    horizon = int(report["horizon_days"])
    if horizon != 28 or len(quantiles) != 4:
        raise ValueError("Inventory worklist requires supported 28-day forecasts and four weekly bands")
    review_by_key = {(str(d["sku_id"]),str(d["warehouse_id"])):d for d in (decisions or [])
                     if str(d.get("as_of_date")) == cutoff and d.get("model_version") == model_version
                     and int(d.get("review_period_days", 7)) == review_period_days}
    pos = defaultdict(list)
    for po in purchase_orders:
        key = (po["sku_id"], po["warehouse_id"])
        pos[key].append(IncomingOrder(str(po["po_id"]), int(po["ordered_units"]), po["expected_arrival_date"]))
    stock_lots = defaultdict(list)
    for lot in lots:
        key = (lot["sku_id"], lot["warehouse_id"])
        stock_lots[key].append(StockLot(int(lot["units"]), lot["expiry_date"]))

    series = {}
    for (sku, wh), group in forecast_data.groupby(["sku_id", "warehouse_id"], sort=False):
        series[(str(sku), str(wh))] = group.sort_values("date")

    for values in pos.values():
        values.sort(key=lambda p: (p.arrival, p.po_id))
    rows = []
    issues = []
    for fact in snapshot_rows:
        sku, wh = str(fact["sku_id"]), str(fact["warehouse_id"])
        try:
            subset = series.get((sku, wh))
            if subset is None or len(subset) != horizon:
                raise ValueError("Missing or incomplete model forecast")
            required = ["predicted_units", "lower_units", "upper_units", "uncertainty_scale"]
            if not set(required).issubset(subset.columns):
                raise ValueError("Missing calibrated interval columns")
            if not subset[required].apply(pd.to_numeric, errors="coerce").notna().all().all():
                raise ValueError("Non-numeric forecast or interval")
            scale = float(subset["uncertainty_scale"].iloc[0])
            if not isfinite(scale) or scale < 1 or any(abs(float(s) - scale) > 0.001 for s in subset["uncertainty_scale"]):
                raise ValueError("Inconsistent forecast interval scale")
            forecast = subset[["date", "predicted_units", "lower_units", "upper_units"]].to_dict(orient="records")
            weeks = []
            for i, chunk in enumerate(week_slices(horizon)):
                block = subset.iloc[chunk]
                predicted = float(block.predicted_units.sum())
                radius = float(quantiles[i]) * scale * len(block)
                weeks.append({
                    "week": i + 1, "start_date": str(block.date.iloc[0]), "end_date": str(block.date.iloc[-1]),
                    "predicted_units": round(predicted, 2), "lower_units": round(max(0, predicted - radius), 2),
                    "upper_units": round(predicted + radius, 2),
                })
            if fact["as_of_date"].isoformat() != cutoff:
                raise ValueError("Inventory and forecast snapshots do not match")
            result = recommend(
                as_of_date=fact["as_of_date"], sku_id=sku, warehouse_id=wh,
                product_name=str(fact["product_name"]), supplier_name=str(fact["supplier_name"]),
                model_version=model_version, forecast=forecast, weekly_forecast=weeks,
                on_hand=int(fact["on_hand_units"]), reserved=int(fact["reserved_units"]),
                backorder=int(fact["backorder_units"]), on_order=int(fact["on_order_units"]),
                lead_time_days=int(fact["lead_time_days"]), minimum_order_qty=int(fact["min_order_qty"]),
                case_pack=int(fact["case_pack"]), incoming=pos[(sku, wh)], lots=stock_lots[(sku, wh)],
                review_period_days=review_period_days,
            )
            # Match persisted review only when the exact same model, dated vector,
            # incoming deliveries, and calculation are still current.
            result["forecast_signature"] = hashlib.sha256(json.dumps(
                {"forecast": forecast, "weekly": weeks}, sort_keys=True, default=str
            ).encode()).hexdigest()
            result["incoming_signature"] = hashlib.sha256(json.dumps(
                [(x.po_id,x.units,x.arrival.isoformat()) for x in pos[(sku,wh)]], sort_keys=True
            ).encode()).hexdigest()
            revision = review_by_key.get((sku,wh))
            has_review = bool(revision and revision.get("snapshot_hash") == fingerprint(result))
            shortage = result["point_without_new_order"]
            if shortage["first_shortage_date"] and shortage["first_shortage_date"] < result["new_order_expected_arrival"]:
                priority = "expedite"  # standard lead time cannot avoid earliest projected shortage
            elif result["requires_manual_review"]:
                priority = "review"
            elif result["recommended_order_units"] > 0:
                priority = "reorder"
            else:
                priority = "covered"
            rows.append({
                "sku_id": sku, "warehouse_id": wh, "product_name": str(fact["product_name"]),
                "category": str(fact["category"]), "warehouse_name": f"{fact['city']}, {fact['state']}",
                "supplier_name": str(fact["supplier_name"]), "priority": priority,
                "on_hand_units": result["on_hand_units"], "on_order_units": result["on_order_units"],
                "demand_next_28_days": result["demand_next_28_days"],
                "recommended_order_units": result["recommended_order_units"],
                "lead_time_days": result["lead_time_days"],
                "new_order_expected_arrival": result["new_order_expected_arrival"],
                "projected_shortage_days_no_order": shortage["shortage_days"],
                "projected_shortage_units_no_order": shortage["estimated_unmet_units"],
                "first_shortage_date_no_order": shortage["first_shortage_date"],
                "shortage_days_with_order": result["point_with_recommendation"]["shortage_days"],
                "expiring_lot_units": result["expiring_current_lot_units"],
                "manual_review_required": result["requires_manual_review"],
                "review_state": str(revision["action"]) if has_review else "unreviewed",
                "reviewed_units": int(revision["approved_units"]) if has_review else None,
            })
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            issues.append({"sku_id": sku, "warehouse_id": wh, "issue": str(exc)})
    rows.sort(key=lambda r: (PRIORITY_RANK[r["priority"]], -r["projected_shortage_days_no_order"], -r["recommended_order_units"], r["sku_id"], r["warehouse_id"]))
    summary = {
        "sku_locations_evaluated": len(rows),
        "requires_attention": sum(r["priority"] != "covered" for r in rows),
        "expedite_count": sum(r["priority"] == "expedite" for r in rows),
        "review_count": sum(r["priority"] == "review" for r in rows),
        "replenishment_count": sum(r["recommended_order_units"] > 0 for r in rows),
        "total_suggested_units": sum(r["recommended_order_units"] for r in rows),
        "expiry_review_count": sum(r["expiring_lot_units"] > 0 for r in rows),
        "recorded_reviews": sum(r["review_state"] != "unreviewed" for r in rows),
        "awaiting_review": sum(r["priority"] != "covered" and r["review_state"] == "unreviewed" for r in rows),
    }
    return {
        "status": "partial" if issues else "ready", "as_of_date": cutoff,
        "model_version": model_version, "synthetic_data": True,
        "summary": summary, "rows": rows, "issues": issues,
        "note": "Priorities are deterministic planning indicators, not calibrated stockout probabilities. No purchase orders are submitted.",
    }
