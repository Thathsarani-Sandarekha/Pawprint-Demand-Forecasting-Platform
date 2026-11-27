"""deterministic order decision and read-only API guards."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.routes import inventory as api
from app.inventory.policy import IncomingOrder, StockLot, recommend
from app.main import app

AS_OF = date(2026, 11, 19)
SKU, WH = "DOG-FOOD-15LB-001", "TX-DAL"


def inputs():
    start = AS_OF + timedelta(days=1)
    daily = [
        {"date": (start + timedelta(days=i)).isoformat(), "predicted_units": 30.0,
         "lower_units": 20.0, "upper_units": 40.0}
        for i in range(28)
    ]
    weekly = [
        {"week": i + 1, "start_date": (start + timedelta(days=7 * i)).isoformat(),
         "end_date": (start + timedelta(days=7 * i + 6)).isoformat(),
         "predicted_units": 210.0, "lower_units": 160.0, "upper_units": 260.0}
        for i in range(4)
    ]
    return {
        "as_of_date": AS_OF, "sku_id": SKU, "warehouse_id": WH,
        "product_name": "Premium Adult Dog Food 15 lb", "supplier_name": "Fictional Pet Supply Partner 01",
        "model_version": "lightgbm-global-v1", "forecast": daily, "weekly_forecast": weekly,
        "on_hand": 180, "reserved": 7, "backorder": 0, "on_order": 444,
        "lead_time_days": 13, "minimum_order_qty": 72, "case_pack": 12,
        "incoming": [IncomingOrder("PO-1", 228, date(2026, 11, 24)),
                     IncomingOrder("PO-2", 216, date(2026, 12, 1))],
        "lots": [StockLot(57, date(2027, 6, 23)), StockLot(123, date(2027, 9, 17))],
    }


def test_explainable_order_does_not_confuse_full_horizon_with_reorder():
    result = recommend(**inputs())
    assert result["demand_next_28_days"] == 840
    assert result["demand_protection_window"] == 600  # 13 + 7 days, not 28
    assert result["on_order_arriving_within_window"] == 444
    assert result["available_before_receipts_units"] == 173
    assert result["inventory_position_for_window"] == 617
    assert result["safety_stock_units"] == 143  # 2*50 + 6/7*50, rounded up
    assert result["target_stock_units"] == 743
    assert result["unconstrained_order_units"] == 126
    assert result["recommended_order_units"] == 132
    assert result["recommended_order_units"] % 12 == 0
    assert result["decision_state"] == "preview_only_not_submitted"
    assert result["point_with_recommendation"]["estimated_unmet_units"] <= result["point_without_new_order"]["estimated_unmet_units"]
    assert result["stress_25pct_with_recommendation"]["estimated_unmet_units"] >= result["point_with_recommendation"]["estimated_unmet_units"]


def test_no_order_when_stock_sufficient_and_not_forced_moq():
    d = inputs()
    d.update(on_hand=2000, reserved=0, on_order=0, incoming=[], lots=[])
    result = recommend(**d)
    assert result["recommended_order_units"] == 0
    assert result["unconstrained_order_units"] == 0


def test_open_po_after_protection_window_is_not_counted():
    d = inputs()
    d["incoming"] = [IncomingOrder("late", 444, date(2026, 12, 15))]
    result = recommend(**d)
    assert result["on_order_arriving_within_window"] == 0
    assert result["recommended_order_units"] >= 444


def test_overdue_open_po_is_not_assumed_to_arrive():
    d = inputs()
    d["incoming"] = [IncomingOrder("overdue", 444, AS_OF)]
    result = recommend(**d)
    assert result["overdue_open_po_units"] == 444
    assert result["on_order_arriving_within_window"] == 0
    assert result["requires_manual_review"]


def test_early_expiry_triggers_manual_review_not_false_automatic_FEFO():
    d = inputs()
    d["lots"] = [StockLot(57, date(2026, 11, 28)), StockLot(123, date(2027, 9, 17))]
    result = recommend(**d)
    assert result["expiring_current_lot_units"] == 57
    assert result["requires_manual_review"]
    assert "Expiry review" in result["expiry_status"]


@pytest.mark.parametrize("field,change", [
    ("on_order", 400), ("reserved", 181), ("lead_time_days", 25),
    ("case_pack", 0),
])
def test_invalid_operational_inputs_fail_closed(field, change):
    d = inputs()
    d[field] = change
    with pytest.raises(ValueError):
        recommend(**d)


def test_future_forecast_gap_rejected():
    d = inputs()
    d["forecast"][7]["date"] = "2026-12-31"
    with pytest.raises(ValueError):
        recommend(**d)


def test_api_is_read_only_and_checks_prerequisites(monkeypatch):
    monkeypatch.setattr(api, "database_status", lambda: {"status": "ready"})
    monkeypatch.setattr(api.ml_forecasting, "_status", lambda: {
        "status": "ready", "model_version": "lightgbm-global-v1", "future": {"cutoff": "2026-11-19"}
    })
    d = inputs()
    monkeypatch.setattr(api, "read_inventory_facts", lambda sku, wh: {
        "as_of_date": d["as_of_date"], "sku_id": sku, "warehouse_id": wh,
        "product_name": d["product_name"], "supplier_name": d["supplier_name"],
        "on_hand_units": d["on_hand"], "reserved_units": d["reserved"],
        "on_order_units": d["on_order"], "backorder_units": d["backorder"],
        "lead_time_days": d["lead_time_days"], "minimum_order_qty": d["minimum_order_qty"],
        "case_pack": d["case_pack"], "incoming": d["incoming"], "lots": d["lots"],
    } if sku == SKU else None)
    monkeypatch.setattr(api.ml_forecasting, "ml_series", lambda **kwargs: {
        "model_version": "lightgbm-global-v1", "forecast": d["forecast"],
        "weekly_forecast": d["weekly_forecast"],
    })
    with TestClient(app) as client:
        assert client.get("/api/v1/inventory/status").json()["status"] == "ready"
        response = client.get("/api/v1/inventory/recommendation", params={"sku_id": SKU, "warehouse_id": WH})
        assert response.status_code == 200
        assert response.json()["recommended_order_units"] == 132
        assert client.post("/api/v1/inventory/recommendation", json={}).status_code == 405
        assert client.get("/api/v1/inventory/recommendation", params={"sku_id": "INVALID", "warehouse_id": WH}).status_code == 404
        assert client.get("/api/v1/inventory/recommendation", params={"sku_id": SKU, "warehouse_id": WH, "review_period_days": 0}).status_code == 422
        monkeypatch.setattr(api.ml_forecasting, "_status", lambda: {"status": "stale"})
        assert client.get("/api/v1/inventory/recommendation", params={"sku_id": SKU, "warehouse_id": WH}).status_code == 503
