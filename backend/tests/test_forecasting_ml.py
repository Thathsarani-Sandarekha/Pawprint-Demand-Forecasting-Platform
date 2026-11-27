"""Leakage, provenance and recursive forecast behavior for real ML training."""
from __future__ import annotations
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.routes import ml_forecasting as api
from app.data_pipeline.generate import build_dataset
from app.forecasting.baselines import load_observed_sales, prepare_panel
from app.forecasting.lightgbm_model import (
    MAX_LAG, FEATURES, TrainConfig, _catalog, _features_at, build_training_rows,
    fit_model, recursive_forecast, run_ml, write_ml_artifacts,
)
from app.main import app


@pytest.fixture(scope="module")
def mini(tmp_path_factory):
    folder = tmp_path_factory.mktemp("ml_pet_synthetic")
    build_dataset(n_skus=5, days=225, seed=54, end_date="2026-11-19", out=folder)
    frame, info = load_observed_sales(folder)
    return folder, frame, info


def test_features_never_use_the_target_or_a_future_actual(mini):
    folder, frame, info = mini
    dates, series, sales, _ = prepare_panel(frame)
    static, schedule = _catalog(folder, series, dates, 14)
    train = sales.copy()
    a = _features_at(train[10:10+MAX_LAG], dates[10+MAX_LAG], static, schedule[10+MAX_LAG])
    train[10+MAX_LAG:] = 999999  # changes target and all later sales
    b = _features_at(train[10:10+MAX_LAG], dates[10+MAX_LAG], static, schedule[10+MAX_LAG])
    np.testing.assert_array_equal(a, b)
    assert "latent_demand" not in FEATURES and "future_stock" not in FEATURES


def test_training_rows_strictly_before_cutoff(mini):
    folder, frame, info = mini
    dates, series, sales, _ = prepare_panel(frame)
    static, schedule = _catalog(folder, series, dates, 14)
    cutoff = 128
    x, y = build_training_rows(dates, sales, static, schedule, cutoff, 100)
    corrupt = sales.copy()
    corrupt[cutoff:] = 999999
    xx, yy = build_training_rows(dates, corrupt, static, schedule, cutoff, 100)
    np.testing.assert_array_equal(x, xx)
    np.testing.assert_array_equal(y, yy)
    assert x.shape[1] == len(FEATURES)


def test_recursive_prediction_is_independent_of_future_sales(mini):
    folder, frame, info = mini
    dates, series, sales, _ = prepare_panel(frame)
    static, schedule = _catalog(folder, series, dates, 14)
    origin = 150
    x, y = build_training_rows(dates, sales, static, schedule, origin, 100)
    config = TrainConfig(horizon=14, folds=2, max_training_days=100, estimators=12, min_history_days=100)
    model = fit_model(x, y, config)
    a = recursive_forecast(model, dates[origin:origin+14], sales[:origin], static, schedule[origin:], 14)
    mutated = sales.copy()
    mutated[origin:] = 999999
    b = recursive_forecast(model, dates[origin:origin+14], mutated[:origin], static, schedule[origin:], 14)
    np.testing.assert_array_equal(a, b)
    assert a.shape == (14, len(series))
    assert np.isfinite(a).all() and (a >= 0).all()


def test_complete_ml_workflow_and_read_only_api(mini, monkeypatch, tmp_path):
    folder, frame, info = mini
    config = TrainConfig(horizon=14, folds=2, max_training_days=100, estimators=12, min_history_days=100)
    report, preview, model = run_ml(frame, info, data_dir=folder, config=config)
    assert report["status"] == "ready"
    assert len(report["rolling_folds"]) == 2
    assert report["untouched_test"]["test_start"] > report["rolling_folds"][-1]["validation_end"]
    assert set(report["backtest_summary"]) == {"lightgbm_global", "seasonal_naive_7", "moving_average_28"}
    assert report["selected_model"] in report["backtest_summary"]
    assert len(preview) == 14 * 5 * 3
    assert (preview.predicted_units >= 0).all()
    out, csv, file = write_ml_artifacts(report, preview, model, tmp_path, tmp_path)
    monkeypatch.setattr(api, "ML_REPORT_FILE", out)
    monkeypatch.setattr(api, "ML_PREVIEW_FILE", csv)
    monkeypatch.setattr(api, "MODEL_FILE", file)
    monkeypatch.setattr(api, "database_status", lambda: {"status": "not_configured", "configured": False})
    with TestClient(app) as client:
        result = client.get("/api/v1/forecasting/ml/status")
        assert result.status_code == 200 and result.json()["status"] == "ready"
        sku, wh = preview.iloc[0][["sku_id", "warehouse_id"]]
        response = client.get("/api/v1/forecasting/ml/series", params={"sku_id": sku, "warehouse_id": wh})
        assert response.status_code == 200 and len(response.json()["forecast"]) == 14
        assert response.json()["nominal_coverage_pct"] == 90
        assert len(response.json()["weekly_forecast"]) == 2
        assert all(week["lower_units"] <= week["predicted_units"] <= week["upper_units"]
                   for week in response.json()["weekly_forecast"])
        assert client.get("/api/v1/forecasting/ml/series", params={"sku_id": "NOT-A-SKU", "warehouse_id": wh}).status_code == 404
        monkeypatch.setattr(api, "database_status", lambda: {"status": "ready", "configured": True, "dataset_version": "other"})
        assert client.get("/api/v1/forecasting/ml/status").json()["status"] == "stale"
        monkeypatch.setattr(api, "database_status", lambda: {"status": "not_configured", "configured": False})
        file.write_bytes(b"altered")
        assert client.get("/api/v1/forecasting/ml/status").json()["status"] == "invalid"
