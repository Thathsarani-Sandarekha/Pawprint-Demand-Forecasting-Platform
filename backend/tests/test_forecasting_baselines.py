"""Leaking future actuals, latent demand, or stale datasets must not be accepted."""
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.routes import forecasting as routes
from app.data_pipeline.generate import build_dataset
from app.forecasting.baselines import (
    evaluate, load_observed_sales, predict_baseline, prepare_panel,
    run_baselines, write_artifacts,
)
from app.main import app


@pytest.fixture
def tiny_data(tmp_path):
    folder = tmp_path / "pet_data"
    build_dataset(n_skus=5, days=210, seed=42, end_date="2026-11-19", out=folder)
    return load_observed_sales(folder)


def test_seasonal_and_moving_baselines_use_past_only():
    arr = np.arange(70.0).reshape((70, 1))
    naive = predict_baseline(arr[:42], 28, "seasonal_naive_7").ravel()
    assert np.array_equal(naive[:7], arr[35:42, 0])
    assert np.array_equal(naive[7:14], naive[:7])
    ma = predict_baseline(arr[:42], 28, "moving_average_28").ravel()
    assert np.allclose(ma, arr[14:42].mean())
    with pytest.raises(ValueError):
        predict_baseline(arr, 5, "wrong")


def test_data_validation_rejects_missing_day_and_duplicates(tiny_data):
    sales, _ = tiny_data
    missing = sales.drop(sales.index[20])
    with pytest.raises(ValueError, match="Incomplete"):
        prepare_panel(missing)
    with pytest.raises(ValueError, match="Duplicate"):
        prepare_panel(pd.concat([sales, sales.iloc[[0]]], ignore_index=True))


def test_future_actual_changes_do_not_affect_earlier_backtests(tiny_data):
    sales, info = tiny_data
    dates, series, panel, _ = prepare_panel(sales)
    baseline = predict_baseline(panel[:140], horizon=14, method="moving_average_28")
    baseline2 = predict_baseline(panel[:140].copy(), horizon=14, method="moving_average_28")
    assert np.array_equal(baseline, baseline2)
    # Entire test period can change without changing predictions at an earlier cutoff.
    mutated = panel.copy()
    mutated[140:, :] = 999_999
    assert np.array_equal(baseline, predict_baseline(mutated[:140], 14, "moving_average_28"))
    assert "latent_demand" not in sales.columns


def test_metrics_avoid_undefined_zero_denominators():
    train = np.zeros((35, 2))
    actual = np.zeros((7, 2))
    pred = np.zeros((7, 2))
    score = evaluate(actual, pred, train)
    assert score["wape_pct"] is None and score["mase"] is None
    assert score["bias_pct"] is None
    result = evaluate(np.array([[2.0], [2.0]]), np.array([[3.0], [3.0]]), np.arange(35.0)[:, None])
    assert result["wape_pct"] == 50.0
    assert result["bias_pct"] == 50.0


def test_backtests_and_holdout_isolation(tiny_data):
    sales, info = tiny_data
    result, preview = run_baselines(sales, info, horizon=14, folds=2, min_history_days=60)
    assert len(result["rolling_folds"]) == 2
    assert result["untouched_test"]["test_start"] > result["rolling_folds"][-1]["validation_end"]
    assert result["selected_baseline"] in ("seasonal_naive_7", "moving_average_28")
    assert result["model_type"] == "baseline_only"
    assert len(preview) == 14 * 5 * 3
    assert "latent_demand" not in preview.columns
    assert preview.predicted_units.ge(0).all()


def test_api_not_run_then_reads_completed_artifacts(monkeypatch, tmp_path, tiny_data):
    r = tmp_path / "forecasting_baselines_v1.json"
    p = tmp_path / "forecasting_baseline_preview_v1.csv"
    monkeypatch.setattr(routes, "REPORT_FILE", r)
    monkeypatch.setattr(routes, "PREVIEW_FILE", p)
    monkeypatch.setattr(routes, "database_status", lambda: {"status": "not_configured", "configured": False})
    with TestClient(app) as client:
        assert client.get("/api/v1/forecasting/status").json()["status"] == "not_run"
        assert client.get("/api/v1/forecasting/series", params={"sku_id": "x1", "warehouse_id": "TX-DAL"}).status_code == 503
        sales, info = tiny_data
        report, preview = run_baselines(sales, info, horizon=14, folds=2, min_history_days=60)
        write_artifacts(report, preview, tmp_path)
        assert client.get("/api/v1/forecasting/status").json()["status"] == "ready"
        sku_id, warehouse_id = preview.loc[0, ["sku_id", "warehouse_id"]]
        response = client.get("/api/v1/forecasting/series", params={"sku_id": sku_id, "warehouse_id": warehouse_id})
        assert response.status_code == 200
        assert len(response.json()["forecast"]) == 14
        assert client.get("/api/v1/forecasting/series", params={"sku_id": "UNKNOWN", "warehouse_id": "TX-DAL"}).status_code == 404
        monkeypatch.setattr(routes, "database_status", lambda: {"status": "ready", "configured": True, "dataset_version": "new-data"})
        assert client.get("/api/v1/forecasting/status").json()["status"] == "stale"
