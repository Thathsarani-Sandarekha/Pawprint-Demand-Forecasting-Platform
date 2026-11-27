"""replay safeguards."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.inventory.ml_simulation import simulate_ml_series, report_status
from app.api.routes import inventory as routes


def params():
    return dict(training_demand=[10] * 28, replay_demand=[8] * 28,
                opening_stock=5, lead_days=3, minimum=6, pack=6,
                unit_cost_usd=10.0)


def forecast(days=11, rate=10):
    return {t: [rate] * days for t in (0, 7, 14, 21)}


def test_ml_orders_apply_pack_and_arrive_after_lead():
    p = params()
    value = simulate_ml_series(**p, forecast_by_review=forecast(), return_orders=True)
    assert value['orders_placed'] > 0
    assert all(x['ordered_units'] % 6 == 0 for x in value['order_audit'])
    assert all(x['expected_arrival_day'] == x['review_day'] + 3 for x in value['order_audit'])
    assert 0 <= value['fill_rate_pct'] <= 100


def test_future_observed_demand_never_affects_day_zero_order():
    p = params()
    a = simulate_ml_series(**p, forecast_by_review=forecast(), return_orders=True)
    p['replay_demand'][5] = 200
    b = simulate_ml_series(**p, forecast_by_review=forecast(), return_orders=True)
    assert a['order_audit'][0]['ordered_units'] == b['order_audit'][0]['ordered_units']
    assert b['unmet_observed_units'] > a['unmet_observed_units']


def test_forecasts_affect_orders_not_future_actuals():
    a = simulate_ml_series(**params(), forecast_by_review=forecast(rate=8), return_orders=True)
    b = simulate_ml_series(**params(), forecast_by_review=forecast(rate=18), return_orders=True)
    assert b['order_audit'][0]['ordered_units'] > a['order_audit'][0]['ordered_units']


def test_missing_invalid_forecasts_rejected():
    with pytest.raises(ValueError):
        simulate_ml_series(**params(), forecast_by_review={0: [10] * 11})
    wrong = forecast()
    wrong[0][2] = float('nan')
    with pytest.raises(ValueError):
        simulate_ml_series(**params(), forecast_by_review=wrong)
    wrong = forecast()
    wrong[0][2] = -1
    with pytest.raises(ValueError):
        simulate_ml_series(**params(), forecast_by_review=wrong)


def test_report_not_run(tmp_path, monkeypatch):
    from app.inventory import ml_simulation
    monkeypatch.setattr(ml_simulation, 'REPORT', tmp_path / 'missing.json')
    assert report_status()['status'] == 'not_run'


def test_ml_status_detects_stale_dataset(monkeypatch):
    monkeypatch.setattr(routes, 'ml_replay_report_status', lambda: {
        'status':'ready', 'dataset_version':'old'})
    monkeypatch.setattr(routes, 'database_status', lambda: {'status':'ready','dataset_version':'new'})
    with TestClient(app) as client:
        response=client.get('/api/v1/inventory/simulation/ml/status')
    assert response.status_code == 200
    assert response.json()['status'] == 'stale'
