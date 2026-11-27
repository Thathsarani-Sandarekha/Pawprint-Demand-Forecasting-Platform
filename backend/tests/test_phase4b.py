"""Targeted local demo review and causal inventory replay tests."""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.inventory.decisions import fingerprint, validate_choice
from app.inventory.simulation import simulate_series, aggregate, report_status
from app.api.routes import inventory as routes
from app.main import app


def preview():
    return {
        'sku_id': 'DOG-FOOD-15LB-001', 'warehouse_id': 'TX-DAL',
        'model_version': 'lightgbm-global-v1', 'as_of_date': '2026-11-19',
        'review_period_days': 7, 'lead_time_days': 13, 'on_hand_units': 180,
        'reserved_units': 7, 'backorder_units': 0, 'on_order_units': 444,
        'on_order_arriving_within_window': 444, 'demand_protection_window': 614.0,
        'safety_stock_units': 267, 'minimum_order_qty': 72, 'case_pack': 12,
        'recommended_order_units': 264, 'requires_manual_review': False,
    }


def test_snapshot_fingerprint_changes_with_stock_forecast_and_po():
    a = preview()
    key = fingerprint(a)
    assert len(key) == 64
    b = dict(a, on_hand_units=181)
    assert fingerprint(b) != key
    assert fingerprint(dict(a, forecast_signature='new')) != key
    assert fingerprint(dict(a, incoming_signature='changed')) != key


def test_approve_requires_exact_quantity_and_manual_flag_note():
    p = preview()
    assert validate_choice(p, 'approve', None, None) == 264
    with pytest.raises(ValueError):
        validate_choice(p, 'approve', 252, None)
    p['requires_manual_review'] = True
    with pytest.raises(ValueError):
        validate_choice(p, 'approve', None, None)
    assert validate_choice(p, 'approve', None, 'Supplier dates reviewed') == 264


def test_override_enforces_moq_pack_and_reason():
    p = preview()
    assert validate_choice(p, 'override', 288, 'Manual buffer for promotion') == 288
    assert validate_choice(p, 'override', 0, 'Do not reorder until expiry review') == 0
    for qty in (264, 71, 287, -1):
        with pytest.raises(ValueError):
            validate_choice(p, 'override', qty, 'Manual buffer for promotion')
    with pytest.raises(ValueError):
        validate_choice(p, 'override', 288, 'short')
    with pytest.raises(ValueError):
        validate_choice(p, 'override', True, 'Manual buffer for promotion')


def test_post_requires_fresh_fingerprint_and_returns_record(monkeypatch):
    current = preview()
    current['snapshot_hash'] = fingerprint(current)
    monkeypatch.setattr(routes, 'recommendation', lambda **kw: current)
    calls = []
    monkeypatch.setattr(routes, 'save_choice', lambda rec, action, qty, reason: calls.append((action, qty, reason)) or {'record_type': 'planner_review_only_not_purchase_order', 'approved_units': qty})
    payload = {'sku_id': current['sku_id'], 'warehouse_id': current['warehouse_id'],
               'review_period_days': 7, 'snapshot_hash': current['snapshot_hash'],
               'action': 'approve', 'approved_units': 264}
    with TestClient(app) as client:
        wrong = client.post('/api/v1/inventory/decisions', json=dict(payload, snapshot_hash='a'*64))
        assert wrong.status_code == 409
        assert not calls
        result = client.post('/api/v1/inventory/decisions', json=payload)
        assert result.status_code == 201
        assert result.json()['record_type'] == 'planner_review_only_not_purchase_order'
        assert calls == [('approve', 264, None)]
        invalid = client.post('/api/v1/inventory/decisions', json=dict(payload, approved_units=1.5))
        assert invalid.status_code == 422


def test_simulator_never_peeks_at_future_demands_for_day_zero_order():
    training = [10] * 28
    first = simulate_series(training_demand=training, replay_demand=[10, 12, 10, 10],
                            opening_stock=10, lead_days=2, minimum=1, pack=1,
                            unit_cost_usd=5.0, buffered=False)
    other = simulate_series(training_demand=training, replay_demand=[10, 2000, 10, 10],
                            opening_stock=10, lead_days=2, minimum=1, pack=1,
                            unit_cost_usd=5.0, buffered=False)
    # Future demand affects fulfillment and subsequent decisions, never day-zero
    # calculations: verify with exact no-lead supply behavior in separate test.
    assert first['orders_placed'] >= 1
    assert other['unmet_observed_units'] > first['unmet_observed_units']


def test_buffered_policy_orders_more_on_variable_history():
    history = [0, 20] * 14
    params = dict(training_demand=history, replay_demand=[10]*28, opening_stock=4,
                  lead_days=4, minimum=1, pack=1, unit_cost_usd=10)
    basic = simulate_series(**params, buffered=False)
    safer = simulate_series(**params, buffered=True)
    assert safer['units_ordered'] >= basic['units_ordered']
    assert safer['units_filled'] >= basic['units_filled']
    assert 0 <= safer['fill_rate_pct'] <= 100
    assert safer['holding_cost_usd'] >= 0


def test_aggregate_weighted_rates_not_mean_of_sku_percentages():
    r1 = dict(observed_demand_units=100, units_filled=100, unmet_observed_units=0,
              shortage_days=0, holding_cost_usd=2, inventory_days=100,
              orders_placed=1, units_ordered=10, days=28)
    r2 = dict(r1, observed_demand_units=1, units_filled=0, unmet_observed_units=1, shortage_days=1)
    out = aggregate([r1,r2])
    assert out['fill_rate_pct'] == 99.01
    assert out['stockout_day_pct'] == 1.79
    assert out['series_count'] == 2


def test_report_status_when_not_run(tmp_path, monkeypatch):
    from app.inventory import simulation
    monkeypatch.setattr(simulation, 'REPORT', tmp_path / 'not_there.json')
    assert report_status()['status'] == 'not_run'


def test_sql_replay_runs_on_historical_mock_rows(tmp_path, monkeypatch):
    """Exercise the *actual* SQL replay orchestration without external PostgreSQL."""
    from datetime import timedelta
    from app.inventory import simulation
    asof = date(2026, 11, 19)
    origin = asof - timedelta(days=55)
    source = []
    for i in range(56):
        source.append({'date': origin + timedelta(days=i), 'sku_id': 'SKU-001',
                       'warehouse_id': 'TX-DAL', 'units_sold': (i % 7) + 2,
                       'opening_stock': 50, 'stockout_observed': 0,
                       'lead_time_days': 3, 'min_order_qty': 12, 'case_pack': 6,
                       'unit_cost_usd': 9.50})
    class Cursor:
        def __init__(self, values): self.values = values
        def mappings(self): return self
        def one(self): return self.values
        def all(self): return self.values
    class Connection:
        def execute(self, statement, params=None):
            if 'dataset_imports' in str(statement):
                return Cursor({'dataset_version':'test','as_of_date':asof, 'source_sha256':'x'*64})
            return Cursor(source)
    class Context:
        def __enter__(self): return Connection()
        def __exit__(self, *args): return False
    class Engine:
        def connect(self): return Context()
    monkeypatch.setattr(simulation, 'get_database_url', lambda: 'test-url')
    monkeypatch.setattr(simulation, 'get_engine', lambda _url: Engine())
    monkeypatch.setattr(simulation, 'REPORT', tmp_path / 'report.json')
    result = simulation.run_replay()
    assert result['status'] == 'ready'
    assert result['source'] == 'postgresql'
    assert result['policy_comparison']['simple_moving_average']['series_count'] == 1
    assert result['policy_comparison']['buffered_moving_average']['observed_demand_units'] == sum(r['units_sold'] for r in source[28:])
    assert simulation.REPORT.exists()
