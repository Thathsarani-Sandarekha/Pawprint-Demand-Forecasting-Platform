"""transparent, deterministic periodic-review replenishment planning.

The forecast is a point prediction of observed sales, not latent demand.
Weekly forecast intervals are marginal: their upper margins can inform a
*heuristic safety buffer*, but sums are NOT a calibrated service-level bound.
Risk scenarios are deterministic stress tests, NOT stockout probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil, isfinite


@dataclass(frozen=True)
class IncomingOrder:
    po_id: str
    units: int
    arrival: date


@dataclass(frozen=True)
class StockLot:
    units: int
    expiry: date


def _ceil_pack(value: int, minimum: int, pack: int) -> int:
    if value <= 0:
        return 0
    return int(ceil(max(value, minimum) / pack) * pack)


def _simulate(*, start: date, opening_available: int, daily: list[float],
              arrivals: list[IncomingOrder], new_order_units: int = 0,
              new_arrival: date | None = None, multiplier: float = 1.0) -> dict:
    """Fill what can be shipped; unfilled demand becomes an unmet-sales estimate.

    Pre-existing backorders are subtracted from opening availability, not
    silently shipped or converted into additional forecast demand here.
    """
    remaining = float(max(0, opening_available))
    initial_backlog = max(0, -opening_available)
    unmet = float(initial_backlog)
    first_shortage: str | None = start.isoformat() if initial_backlog else None
    shortage_days = 0
    received_by_day: dict[date, int] = {}
    for po in arrivals:
        if po.arrival >= start:
            received_by_day[po.arrival] = received_by_day.get(po.arrival, 0) + po.units
    if new_order_units and new_arrival is not None:
        received_by_day[new_arrival] = received_by_day.get(new_arrival, 0) + new_order_units
    for idx, demand in enumerate(daily):
        day = start + timedelta(days=idx)
        remaining += received_by_day.get(day, 0)
        shortage = max(0.0, demand * multiplier - remaining)
        if shortage > 1e-8:
            shortage_days += 1
            if first_shortage is None:
                first_shortage = day.isoformat()
            unmet += shortage
        remaining = max(0.0, remaining - demand * multiplier)
    return {
        "shortage_days": shortage_days,
        "first_shortage_date": first_shortage,
        "estimated_unmet_units": round(unmet, 1),
        "ending_available_units": round(remaining, 1),
    }


def recommend(*, as_of_date: date, sku_id: str, warehouse_id: str,
              product_name: str, supplier_name: str, model_version: str,
              forecast: list[dict], weekly_forecast: list[dict],
              on_hand: int, reserved: int, backorder: int, on_order: int,
              lead_time_days: int, minimum_order_qty: int, case_pack: int,
              incoming: list[IncomingOrder], lots: list[StockLot],
              review_period_days: int = 7) -> dict:
    """Recommend one SKU/location order using a 28-day point forecast.

    Order-up-to target = demand through (lead time + review period) +
    additive weekly upper-margin buffer. Only *timely* open POs count toward
    the target; overdue POs are flagged, not assumed received. This is a
    decision-support heuristic; not a cost-optimized or probabilistic SLA.
    """
    if not (1 <= lead_time_days <= 28 and 1 <= review_period_days <= 28):
        raise ValueError("Lead/review periods must be positive and inside forecast horizon")
    horizon = lead_time_days + review_period_days
    if len(forecast) != 28 or len(weekly_forecast) != 4 or horizon > len(forecast):
        raise ValueError("Require a complete 28-day horizon covering lead plus review time")
    if not (on_hand >= reserved >= 0 and backorder >= 0 and on_order >= 0 and
            minimum_order_qty > 0 and case_pack > 0):
        raise ValueError("Invalid inventory or supplier constraints")
    if any(po.units <= 0 for po in incoming) or sum(po.units for po in incoming) != on_order:
        raise ValueError("Open PO quantities do not reconcile to snapshot on_order_units")
    if any(lot.units < 0 for lot in lots):
        raise ValueError("Negative inventory lot")
    if lots and sum(lot.units for lot in lots) != on_hand:
        raise ValueError("Stock lots do not reconcile to snapshot on_hand_units")
    if lots and any(lot.expiry <= as_of_date for lot in lots):
        raise ValueError("Snapshot contains an already-expired lot; manual reconciliation required")

    start = as_of_date + timedelta(days=1)
    point: list[float] = []
    for idx, row in enumerate(forecast):
        if date.fromisoformat(str(row["date"])) != start + timedelta(days=idx):
            raise ValueError("Forecast must begin the day after inventory snapshot without gaps")
        pred, lo, hi = (float(row[key]) for key in ("predicted_units", "lower_units", "upper_units"))
        if not all(isfinite(x) for x in (pred, lo, hi)) or not (0 <= lo <= pred <= hi):
            raise ValueError("Invalid daily forecast interval")
        point.append(pred)

    safety = 0.0
    for idx, row in enumerate(weekly_forecast):
        expected_start = start + timedelta(days=7 * idx)
        if int(row["week"]) != idx + 1 or date.fromisoformat(str(row["start_date"])) != expected_start:
            raise ValueError("Forecast week ordering or date does not match")
        if date.fromisoformat(str(row["end_date"])) != expected_start + timedelta(days=6):
            raise ValueError("Forecast week is not seven days")
        forecast_week = sum(point[idx * 7: (idx + 1) * 7])
        upper = float(row["upper_units"])
        if not isfinite(upper) or upper + 0.1 < forecast_week:
            raise ValueError("Invalid weekly upper forecast")
        days_in_window = max(0, min(7, horizon - idx * 7))
        # A partial week is prorated. This is NOT a guaranteed joint interval.
        safety += max(0.0, upper - forecast_week) * days_in_window / 7
    safety_units = ceil(safety)
    demand_protection = sum(point[:horizon])
    protection_end = as_of_date + timedelta(days=horizon)
    arrival_new = as_of_date + timedelta(days=lead_time_days)
    overdue = [po for po in incoming if po.arrival <= as_of_date]
    timely = [po for po in incoming if po.arrival > as_of_date]
    arriving_in_window = sum(po.units for po in timely if po.arrival <= protection_end)
    arriving_later = sum(po.units for po in timely if po.arrival > protection_end)
    effective_available = on_hand - reserved - backorder
    inventory_position = effective_available + arriving_in_window
    target_stock = ceil(demand_protection + safety_units)
    raw_need = max(0, target_stock - inventory_position)
    order_qty = _ceil_pack(raw_need, minimum_order_qty, case_pack)

    expiring = [lot for lot in lots if lot.expiry <= protection_end]
    warnings = []
    if overdue:
        warnings.append("Open POs with overdue expected arrivals are excluded until dates are confirmed; reconcile with purchasing.")
    if expiring:
        warnings.append("Some current lots expire during the protection window; review FEFO sell-through. Expiry-driven reductions are not automated in this planning preview.")
    if arriving_later:
        warnings.append("Open POs due after the protection window are excluded from this order-up-to calculation.")
    warnings.append("Safety stock sums/prorates weekly marginal upper margins; this is a heuristic, not a calibrated joint 90% service-level guarantee.")

    before = _simulate(start=start, opening_available=effective_available, daily=point,
                       arrivals=timely)
    after = _simulate(start=start, opening_available=effective_available, daily=point,
                      arrivals=timely, new_order_units=order_qty, new_arrival=arrival_new)
    stress = _simulate(start=start, opening_available=effective_available, daily=point,
                       arrivals=timely, new_order_units=order_qty, new_arrival=arrival_new,
                       multiplier=1.25)
    warnings.append("Shortages below are deterministic point and +25% demand scenarios, not estimated stockout probabilities.")
    if before["first_shortage_date"] and date.fromisoformat(before["first_shortage_date"]) < arrival_new:
        warnings.append("Projected shortage may occur before a new standard-lead-time order can arrive; investigate expedited supply or transfers.")
    if lots and sum(lot.units for lot in lots) == on_hand and not expiring:
        expiry_status = "No current stock lots expire during the protection window"
    else:
        expiry_status = "Expiry review required" if expiring else "No stock-lot records available"

    return {
        "status": "ready", "fictional": True,
        "sku_id": sku_id, "warehouse_id": warehouse_id,
        "product_name": product_name, "supplier_name": supplier_name,
        "model_version": model_version, "as_of_date": as_of_date.isoformat(),
        "forecast_start": start.isoformat(),
        "new_order_expected_arrival": arrival_new.isoformat(),
        "protection_end": protection_end.isoformat(),
        "forecast_horizon_days": len(point), "lead_time_days": lead_time_days,
        "review_period_days": review_period_days,
        "on_hand_units": on_hand, "reserved_units": reserved,
        "backorder_units": backorder, "on_order_units": on_order,
        "on_order_arriving_within_window": arriving_in_window,
        "overdue_open_po_units": sum(po.units for po in overdue),
        "available_before_receipts_units": effective_available,
        "inventory_position_for_window": inventory_position,
        "demand_next_28_days": round(sum(point), 2),
        "demand_protection_window": round(demand_protection, 2),
        "safety_stock_units": safety_units,
        "target_stock_units": target_stock,
        "unconstrained_order_units": raw_need,
        "minimum_order_qty": minimum_order_qty, "case_pack": case_pack,
        "recommended_order_units": order_qty,
        "expiry_status": expiry_status,
        "expiring_current_lot_units": sum(lot.units for lot in expiring),
        "point_without_new_order": before,
        "point_with_recommendation": after,
        "stress_25pct_with_recommendation": stress,
        "requires_manual_review": bool(expiring or overdue or after["shortage_days"]),
        "decision_state": "preview_only_not_submitted",
        "warnings": warnings,
        "calculation": "max(0, ceil(protection-period forecast + heuristic safety stock) - (on hand - reserved - backorders + timely open POs)); then apply MOQ and pack multiple",
        "notes": "Read-only planning preview. Uses observed-sales forecasts and synthetic operational data; it does not issue purchase orders, guarantee service levels, or measure real business savings.",
    }
