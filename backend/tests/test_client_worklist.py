"""Client UI worklist: only backed by exactly the existing policy calculation."""
from datetime import date, timedelta

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import inventory as api
from app.inventory.worklist import build_worklist


def fixtures():
    origin = date(2026, 11, 19)
    sk = "DOG-FOOD-15LB-001"
    wh = "TX-DAL"
    forecast = pd.DataFrame([
        {"sku_id": sk, "warehouse_id": wh, "date": (origin+timedelta(days=i+1)).isoformat(),
         "predicted_units": 30.0, "lower_units": 20.0, "upper_units": 40.0, "uncertainty_scale":1.0}
        for i in range(28)
    ])
    fact = [{"as_of_date": origin, "sku_id":sk, "warehouse_id":wh,
             "product_name":"Premium Adult Dog Food 15 lb", "category":"Dog food", "supplier_name":"Demo supplier",
             "city":"Dallas", "state":"Texas", "on_hand_units":180, "reserved_units":7, "on_order_units":444,
             "backorder_units":0, "lead_time_days":13, "min_order_qty":72, "case_pack":12}]
    pos = [{"po_id":"PO-1","sku_id":sk,"warehouse_id":wh,"ordered_units":228,"expected_arrival_date":date(2026,11,24)},
           {"po_id":"PO-2","sku_id":sk,"warehouse_id":wh,"ordered_units":216,"expected_arrival_date":date(2026,12,1)}]
    lots = [{"sku_id":sk,"warehouse_id":wh,"units":180,"expiry_date":date(2027,9,17)}]
    report = {"model_version":"lightgbm-global-v1","future":{"cutoff":"2026-11-19"},
              "prediction_intervals":{"weekly_quantiles":[50/7]*4},"horizon_days":28}
    return fact,pos,lots,forecast,report


def test_queue_recommends_exact_same_qty_as_detail_policy():
    result = build_worklist(*fixtures())
    assert result["status"] == "ready"
    assert result["summary"]["sku_locations_evaluated"] == 1
    assert result["rows"][0]["recommended_order_units"] == 132
    assert result["rows"][0]["demand_next_28_days"] == 840
    assert result["rows"][0]["warehouse_name"] == "Dallas, Texas"
    assert result["summary"]["total_suggested_units"] == 132


def test_bad_series_is_flagged_not_treated_as_covered():
    facts, po, lots, frame, report = fixtures()
    result = build_worklist(facts,po,lots,frame.head(27),report)
    assert result["status"] == "partial"
    assert result["summary"]["sku_locations_evaluated"] == 0
    assert result["rows"] == []
    assert len(result["issues"]) == 1


def test_worklist_fails_closed_without_imported_database(monkeypatch):
    monkeypatch.setattr(api, "database_status", lambda: {"status":"unavailable"})
    with TestClient(app) as client:
        res=client.get("/api/v1/inventory/worklist")
    assert res.status_code == 503
