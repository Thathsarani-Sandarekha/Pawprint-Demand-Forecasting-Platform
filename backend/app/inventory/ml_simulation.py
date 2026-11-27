"""causal, historical-origin LightGBM vs moving-average inventory replay.

Offline diagnostic only, evaluated against stockout-censored observed sales.
The model is fit on history ending BEFORE the evaluated 28 days. It is frozen,
then its 28+ day forecasts are refreshed at each 7-day review using ONLY
observed sales available through that review day. No production model artifact
(trained using post-origin data) is ever used.

The benchmark assumes that the synthetic promotion calendar was published in
advance. Costs, starting inventory and lead times are simulated/illustrative.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from math import ceil, sqrt
from pathlib import Path
from statistics import pstdev

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.data_pipeline.generate import ROOT
from app.db.connection import get_database_url, get_engine
from app.forecasting.baselines import load_observed_sales, prepare_panel
from app.forecasting.lightgbm_model import (
    TrainConfig, _catalog, build_training_rows, fit_model, recursive_forecast,
)
from app.inventory.simulation import simulate_series, aggregate
from app.inventory.policy import _ceil_pack

REPORT = ROOT / "reports" / "inventory_ml_policy_replay_v1.json"
REVIEW_EVERY = 7
REPLAY_DAYS = 28


def simulate_ml_series(*, training_demand: list[int], replay_demand: list[int],
                       forecast_by_review: dict[int, list[float]], opening_stock: int,
                       lead_days: int, minimum: int, pack: int, unit_cost_usd: float,
                       holding_rate_per_year: float = 0.20,
                       review_every: int = REVIEW_EVERY,
                       return_orders: bool = False) -> dict:
    """No future actual demand is used for ordering, only review-origin forecasts.

    Orders are placed after same-day demand, arrive at beginning of day
    t+lead, and the sum of forecasts includes days t+1..t+lead+review.
    """
    if len(training_demand) < 28 or not replay_demand:
        raise ValueError("History must be >=28 days and replay nonempty")
    if any(x < 0 for x in training_demand + replay_demand):
        raise ValueError("Observed demand cannot be negative")
    if opening_stock < 0 or lead_days < 1 or minimum < 1 or pack < 1:
        raise ValueError("Invalid inventory or supplier settings")
    if unit_cost_usd < 0 or not (0 <= holding_rate_per_year <= 1) or review_every < 1:
        raise ValueError("Invalid holding cost or review interval")
    needed_horizon = lead_days + review_every
    review_days = list(range(0, len(replay_demand), review_every))
    for day in review_days:
        forecast = np.asarray(forecast_by_review.get(day, []), dtype=float)
        if len(forecast) < needed_horizon or not np.isfinite(forecast).all() or (forecast < 0).any():
            raise ValueError(f"Missing/invalid at-origin forecasts for review day {day}")

    from collections import defaultdict
    pending: dict[int, int] = defaultdict(int)
    stock = float(opening_stock)
    history = list(training_demand)
    metrics = dict(observed_demand_units=0, units_filled=0., unmet_observed_units=0.,
                   shortage_days=0, holding_cost_usd=0., inventory_days=0.,
                   orders_placed=0, units_ordered=0)
    orders: list[dict] = []
    for t, observed in enumerate(replay_demand):
        stock += pending.pop(t, 0)
        filled = min(stock, observed)
        stock -= filled
        metrics['observed_demand_units'] += observed
        metrics['units_filled'] += filled
        metrics['unmet_observed_units'] += observed - filled
        metrics['shortage_days'] += int(filled + 1e-9 < observed)
        metrics['inventory_days'] += stock
        metrics['holding_cost_usd'] += stock * unit_cost_usd * holding_rate_per_year / 365
        history.append(observed)
        if t % review_every == 0:
            upcoming = forecast_by_review[t]
            forecasted_protection = float(sum(upcoming[:needed_horizon]))
            # Same transparent heuristic as the buffered moving-average policy;
            # NOT a calibrated 90% service-level safety-stock guarantee.
            safety = 1.28 * pstdev(history[-28:]) * sqrt(needed_horizon)
            target = ceil(forecasted_protection + safety)
            inventory_position = stock + sum(pending.values())
            need = max(0, target - inventory_position)
            order = _ceil_pack(need, minimum, pack)
            if order:
                pending[t + lead_days] += order
                metrics['orders_placed'] += 1
                metrics['units_ordered'] += order
            if return_orders:
                orders.append({'review_day': t, 'expected_arrival_day': t + lead_days,
                               'target_units': target, 'ordered_units': order})
    days = len(replay_demand)
    metrics['fill_rate_pct'] = (round(100 * metrics['units_filled'] / metrics['observed_demand_units'], 2)
                                if metrics['observed_demand_units'] else None)
    metrics['stockout_day_pct'] = round(100 * metrics['shortage_days'] / days, 2)
    metrics['average_ending_stock_units'] = round(metrics['inventory_days'] / days, 2)
    metrics['holding_cost_usd'] = round(metrics['holding_cost_usd'], 2)
    for key in ('units_filled', 'unmet_observed_units', 'inventory_days'):
        metrics[key] = round(metrics[key], 2)
    if return_orders:
        metrics['order_audit'] = orders
    return metrics


def _inventory_origins(*, data_dir: Path | None, evaluation_start: pd.Timestamp,
                       series: pd.MultiIndex) -> list[dict]:
    """Read the same historical opening stock, supplier policy and costs for all policies."""
    if data_dir is not None:
        folder = Path(data_dir)
        daily = pd.read_csv(folder / 'daily_sales.csv.gz',
                            usecols=['date', 'sku_id', 'warehouse_id', 'opening_stock'],
                            parse_dates=['date'])
        opening = daily.loc[daily['date'] == evaluation_start].copy()
        products = pd.read_csv(folder / 'products.csv', usecols=['sku_id','supplier_id','unit_cost_usd'])
        suppliers = pd.read_csv(folder / 'suppliers.csv',
                                usecols=['supplier_id','lead_time_days','min_order_qty','case_pack'])
        rows = opening.merge(products, on='sku_id', validate='many_to_one').merge(
            suppliers, on='supplier_id', validate='many_to_one')
    else:
        url = get_database_url()
        if not url:
            raise RuntimeError('PostgreSQL configuration missing; use --data-dir for CSV testing')
        with get_engine(url).connect() as conn:
            rows = pd.read_sql_query(text('''
                SELECT d.sku_id, d.warehouse_id, d.opening_stock,
                       s.lead_time_days, s.min_order_qty, s.case_pack, p.unit_cost_usd
                FROM daily_sales d
                JOIN products p ON p.sku_id = d.sku_id
                JOIN suppliers s ON s.supplier_id = p.supplier_id
                WHERE d.date = :date
                ORDER BY d.sku_id, d.warehouse_id
            '''), conn, params={'date': evaluation_start.date()})
    if rows.duplicated(['sku_id','warehouse_id']).any():
        raise ValueError('Duplicate inventory openings')
    indexed = rows.set_index(['sku_id','warehouse_id'])
    if not indexed.index.is_unique or set(indexed.index) != set(series):
        raise ValueError('Inventory opening/supplier facts missing for forecast series')
    return indexed.loc[series].to_dict('records')


def run_ml_replay(*, data_dir: Path | None = None, out: Path = REPORT,
                  config: TrainConfig = TrainConfig()) -> dict:
    """Fit strictly before the evaluated interval, then generate only causal orders."""
    frame, source = load_observed_sales(data_dir)
    dates, series, sales, _flags = prepare_panel(frame)
    if len(dates) < 120 + REPLAY_DAYS:
        raise ValueError('Insufficient history for historical-origin LightGBM replay')
    cutoff = len(dates) - REPLAY_DAYS
    start_date = dates[cutoff]
    rows = _inventory_origins(data_dir=data_dir, evaluation_start=start_date, series=series)
    max_protection = max(int(row['lead_time_days']) + REVIEW_EVERY for row in rows)
    if max_protection < 1:
        raise ValueError('Invalid protection horizon')

    # Important: never load the saved full-history production model. Fit afresh
    # on records BEFORE this evaluation window.
    static, schedule = _catalog(data_dir, series, dates, max_protection)
    x, y = build_training_rows(dates, sales, static, schedule, cutoff, config.max_training_days)
    model = fit_model(x, y, config)
    forecast_by_review: dict[int, np.ndarray] = {}
    for t in range(0, REPLAY_DAYS, REVIEW_EVERY):
        origin_after_day = cutoff + t + 1
        predicted_dates = pd.date_range(dates[origin_after_day-1] + timedelta(days=1),
                                        periods=max_protection, freq='D')
        planned = schedule[origin_after_day:origin_after_day + max_protection]
        forecast_by_review[t] = recursive_forecast(
            model, predicted_dates, sales[:origin_after_day], static, planned, max_protection)

    policies: dict[str, list[dict]] = {
        'simple_moving_average': [], 'buffered_moving_average': [], 'lightgbm_buffered': []}
    for i, (sku, warehouse) in enumerate(series):
        info = rows[i]
        params = dict(training_demand=sales[cutoff-28:cutoff, i].astype(int).tolist(),
                      replay_demand=sales[cutoff:, i].astype(int).tolist(),
                      opening_stock=int(info['opening_stock']),
                      lead_days=int(info['lead_time_days']),
                      minimum=int(info['min_order_qty']), pack=int(info['case_pack']),
                      unit_cost_usd=float(info['unit_cost_usd']))
        for name, buffered in [('simple_moving_average', False), ('buffered_moving_average', True)]:
            policies[name].append(dict(simulate_series(**params, buffered=buffered), days=REPLAY_DAYS))
        ml_forecasts = {t: forecast_by_review[t][:, i].tolist() for t in forecast_by_review}
        policies['lightgbm_buffered'].append(dict(simulate_ml_series(
            **params, forecast_by_review=ml_forecasts), days=REPLAY_DAYS))

    result = {
        'status': 'ready', 'fictional': True,
        'source': source['source'], 'dataset_version': source['dataset_version'],
        'dataset_sha256': source.get('source_sha256'),
        'evaluation_start': start_date.date().isoformat(),
        'evaluation_end': dates[-1].date().isoformat(),
        'model_fit_through': dates[cutoff-1].date().isoformat(),
        'trained_model': 'LightGBM historical-origin fit, not current production artifact',
        'model_training_config': {'max_training_days': config.max_training_days,
                                  'estimators': config.estimators, 'seed': config.seed},
        'policy_comparison': {name: aggregate(metrics) for name, metrics in policies.items()},
        'assumptions': {
            'replay_history_days': 28, 'evaluation_days': REPLAY_DAYS,
            'review_every_days': REVIEW_EVERY, 'carrying_rate_annual_pct': 20,
            'ml_ordering': 'Frozen pre-holdout trained model; fresh recursive forecasts after each weekly review using observed history to date',
            'ml_buffer': 'Same trailing-observed-sales volatility heuristic as buffered moving average',
            'lead_time': 'fixed supplier lead times, order placed after review day, arrival at start of day + lead',
            'starting_inventory': 'Same historical opening stock for all policies',
            'incoming_orders': 'Only newly simulated orders; historical POs/receipts excluded',
            'target': 'Observed sales rather than latent demand',
        },
        'limitations': [
            'Synthetic observed sales are stockout-censored: fill rates are NOT actual customer service levels.',
            'Replay period previously used for forecast evaluation; this is a retrospective diagnostic, NOT a fresh untouched benchmark.',
            'Model hyperparameters/model family were previously reviewed; do not claim blind independent policy validation.',
            'Promotions assumed announced before forecast origins; dataset has no announcement timestamps.',
            'Review-day model updates use historical observed sales even if a simulated stockout would have changed observed purchases: this is not a fully closed-loop counterfactual.', 
            'Historical receipts, initial on-order inventory, perishability, storage limits, supply uncertainty and expiry loss excluded.',
            'Holding cost uses illustrative 20% annual carrying rate; not measured business costs.',
            'All policies share the same beginning inventory, lead times, MOQ, pack-size and replay observed sales.',
        ],
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def report_status() -> dict:
    if not REPORT.exists():
        return {'status':'not_run', 'message':'Run python -m app.inventory.ml_simulation'}
    try:
        payload = json.loads(REPORT.read_text(encoding='utf-8'))
        if payload.get('status') != 'ready':
            return {'status':'invalid'}
        return payload
    except (OSError, ValueError):
        return {'status':'invalid', 'message':'Invalid report format'}


def main():
    parser = argparse.ArgumentParser(description='Historical-origin LightGBM policy comparison')
    parser.add_argument('--data-dir', type=Path, default=None, help='Explicit CSV test source; default is SQL')
    args = parser.parse_args()
    result = run_ml_replay(data_dir=args.data_dir)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
