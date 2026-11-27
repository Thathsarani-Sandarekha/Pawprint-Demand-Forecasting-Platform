"""Read-only client workflow acceptance checks against a running *local* Pawprint API.

This script never sends POST/PUT/PATCH/DELETE requests and cannot approve or place orders.
Run from the repository root:
    python scripts/verify_client_workflows.py
    python scripts/verify_client_workflows.py --base-url http://127.0.0.1:8000
Exit 0: all checks passed; nonzero: one or more readiness checks failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class CheckFailure(Exception):
    """A required live API contract is missing or inconsistent."""


def fetch_json(base: str, endpoint: str, params: dict[str, str] | None = None) -> dict:
    url = f"{base.rstrip('/')}/api/v1/{endpoint.lstrip('/')}"
    if params:
        url += "?" + urlencode(params)
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=35) as response:
            body = response.read(10_000_001)
            if len(body) > 10_000_000:
                raise CheckFailure(f"{endpoint}: response too large (>10 MB)")
            obj = json.loads(body)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise CheckFailure(f"{endpoint}: unavailable or invalid HTTP/JSON response ({type(exc).__name__})") from None
    if not isinstance(obj, dict):
        raise CheckFailure(f"{endpoint}: expected JSON object")
    return obj


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def validate_worklist(worklist: dict) -> tuple[dict, dict]:
    require(worklist.get("status") == "ready", f"Worklist is not fully ready (status={worklist.get('status')}; issues={len(worklist.get('issues') or [])})")
    rows = worklist.get("rows")
    summary = worklist.get("summary")
    require(isinstance(rows, list) and isinstance(summary, dict), "Worklist rows or summary missing")
    require(rows, "Worklist has no rows")
    require(len(rows) == summary.get("sku_locations_evaluated"), "Evaluated series count differs from worklist rows")
    require(not worklist.get("issues"), "Worklist reports incomplete or invalid series")
    keys = [(r.get("sku_id"),r.get("warehouse_id")) for r in rows]
    require(len(set(keys)) == len(keys), "Duplicate SKU–warehouse pairs")
    require(all(isinstance(r.get("recommended_order_units"), int) and r["recommended_order_units"] >= 0 for r in rows), "Missing/negative reorder quantities")
    require(sum(r["recommended_order_units"] for r in rows) == summary.get("total_suggested_units"), "Suggested order sum does not match dashboard metric")
    require(sum(r.get("review_state") != "unreviewed" for r in rows) == summary.get("recorded_reviews"), "Recorded review count inconsistent")
    require(sum(r.get("priority") != "covered" and r.get("review_state") == "unreviewed" for r in rows) == summary.get("awaiting_review"), "Awaiting-review count inconsistent")
    require(sum(r.get("priority") == "expedite" for r in rows) == summary.get("expedite_count"), "Time-critical count inconsistent")
    require(worklist.get("synthetic_data") is True, "Synthetic-data provenance flag missing")
    require(isinstance(worklist.get("as_of_date"), str), "Worklist snapshot date missing")
    date.fromisoformat(worklist["as_of_date"])
    choice = next((r for r in rows if r.get("sku_id") == "DOG-FOOD-15LB-001" and r.get("warehouse_id") == "TX-DAL"),rows[0])
    return summary, choice


def compare_detail(row: dict, detail: dict, worklist: dict) -> None:
    require(detail.get("sku_id") == row.get("sku_id") and detail.get("warehouse_id") == row.get("warehouse_id"), "Detail response is for a different SKU–warehouse pair")
    require(detail.get("as_of_date") == worklist.get("as_of_date"), "Inventory detail snapshot differs from worklist")
    require(detail.get("model_version") == worklist.get("model_version"), "Model versions differ across UI views")
    require(detail.get("recommended_order_units") == row.get("recommended_order_units"), "Worklist and detail recommend different quantities")
    require(detail.get("demand_next_28_days") == row.get("demand_next_28_days"), "Worklist and detail forecast totals differ")
    require(detail.get("review_period_days") == 7, "Unexpected review period in default detail")
    require(isinstance(detail.get("snapshot_hash"), str) and len(detail["snapshot_hash"]) == 64, "Approval snapshot fingerprint missing")
    require(detail.get("recommended_order_units",0) % detail.get("case_pack",1) == 0, "Recommended order breaks supplier case pack")


def run(base: str) -> dict:
    checks = []
    def get(name: str, endpoint: str, params: dict[str,str] | None = None):
        obj = fetch_json(base,endpoint,params)
        checks.append({"name":name,"result":"HTTP OK"})
        return obj
    health=get("API health","health")
    require(health.get("status") == "ok", "Backend health not OK")
    db=get("PostgreSQL connection and import","database/status")
    require(db.get("status") == "ready", "PostgreSQL not ready or dataset not imported")
    ml=get("Trained LightGBM forecast","forecasting/ml/status")
    require(ml.get("status") == "ready", "ML forecast artifacts not ready")
    require(ml.get("source") == "postgresql", "ML report not sourced from current PostgreSQL dataset")
    require(ml.get("dataset_version") == db.get("dataset_version"), "Model and SQL dataset versions differ")
    registry=get("Verified artifact registry","mlops/model")
    require(registry.get("status") == "ready", "Model is not registered or artifact checksums failed")
    require(registry.get("model_version") == ml.get("model_version"), "Registered version does not match active forecast")
    worklist=get("All SKU–warehouse recommendations","inventory/worklist")
    summary, row=validate_worklist(worklist)
    pair={"sku_id":row["sku_id"],"warehouse_id":row["warehouse_id"]}
    detail=get("Detailed reorder calculation","inventory/recommendation",pair)
    compare_detail(row,detail,worklist)
    series=get("Forecast for chosen SKU–warehouse","forecasting/ml/series",pair)
    require(series.get("model_version") == detail.get("model_version"), "Forecast view model differs from recommendation model")
    require(len(series.get("forecast") or []) == 28 and len(series.get("weekly_forecast") or []) == 4, "Future forecast must cover 28 days / four weeks")
    history=get("Saved planner reviews (read-only)","inventory/decisions",pair)
    require(history.get("status") == "ready" and isinstance(history.get("records"),list), "Planner review history unreadable")
    matched=any(x.get("snapshot_hash") == detail["snapshot_hash"] for x in history["records"])
    require((row.get("review_state") != "unreviewed") == matched, "Worklist review state inconsistent with saved history for current snapshot")
    checks.append({"name":"Worklist ⇄ details ⇄ forecasts ⇄ review history","result":"consistent"})
    return {"status":"passed","mode":"READ_ONLY_NO_ORDERS","base_url":base,
            "dataset_version":db.get("dataset_version"),"snapshot_date":worklist.get("as_of_date"),
            "model_version":ml.get("model_version"),"series_checked":summary["sku_locations_evaluated"],
            "sample_sku":row["sku_id"],"sample_warehouse":row["warehouse_id"],
            "sample_reorder_units":row["recommended_order_units"],"checks":checks,
            "limitations":"Synthetic-data pilot verification; not production security, authorization, or live client-data acceptance."}


def main() -> int:
    parser=argparse.ArgumentParser(description="Verify live Pawprint client workflows without writing orders or decisions")
    parser.add_argument("--base-url",default="http://127.0.0.1:8000",help="Local FastAPI base URL")
    args=parser.parse_args()
    try:
        result=run(args.base_url)
        code=0
    except (CheckFailure,ValueError,KeyError,TypeError) as exc:
        result={"status":"failed","mode":"READ_ONLY_NO_ORDERS","error":str(exc)}
        code=1
    print(json.dumps(result,indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
