"""retrospective inventory-policy replay (observed sales, not latent demand).

This is NOT a historical LightGBM backtest: it compares two *causal* moving-
average ordering policies. Both use the identical starting inventory, SKU lead
and pack constraints, and sales records available at each historical cutoff.
Historical customer demand is unknown when a SKU was out of stock, so simulated
fill rates are relative to observed sales and likely optimistic.

Simplifications: no pre-existing on-order POs at origin; no historical receipts,
expiry wastage, space constraints, labor, order costs or lead-time variance.
Costs use an explicitly hypothetical 20% annual carrying rate.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from math import ceil, sqrt
from pathlib import Path
from statistics import pstdev
from typing import Iterable
import json

from sqlalchemy import text
from app.db.connection import get_database_url, get_engine
from app.inventory.policy import _ceil_pack

ROOT = Path(__file__).resolve().parents[3]
REPORT = ROOT / 'reports' / 'inventory_policy_replay_v1.json'


def simulate_series(*, training_demand: list[int], replay_demand: list[int],
                    opening_stock: int, lead_days: int, minimum: int, pack: int,
                    unit_cost_usd: float, buffered: bool,
                    holding_rate_per_year: float = 0.20, review_every: int = 7) -> dict:
    if not (len(training_demand) >= 28 and len(replay_demand) > 0):
        raise ValueError('At least 28 historical demand days and a future replay window required')
    if any(v < 0 for v in training_demand + replay_demand):
        raise ValueError('Demand cannot be negative')
    if min(opening_stock, lead_days, minimum, pack) < 0 or lead_days < 1 or minimum < 1 or pack < 1:
        raise ValueError('Invalid stock or supplier parameters')
    if not (unit_cost_usd >= 0 and 0 <= holding_rate_per_year <= 1):
        raise ValueError('Invalid cost or rate')
    stock = float(opening_stock)
    pending: dict[int, int] = defaultdict(int)
    seen = list(training_demand)
    result = dict(observed_demand_units=0, units_filled=0., unmet_observed_units=0.,
                  shortage_days=0, holding_cost_usd=0., inventory_days=0.,
                  orders_placed=0, units_ordered=0)
    for t, demand in enumerate(replay_demand):
        stock += pending.pop(t, 0)
        filled = min(stock, demand)
        stock -= filled
        result['observed_demand_units'] += demand
        result['units_filled'] += filled
        result['unmet_observed_units'] += demand - filled
        result['shortage_days'] += int(filled + 1e-9 < demand)
        result['inventory_days'] += stock
        result['holding_cost_usd'] += stock * unit_cost_usd * holding_rate_per_year / 365
        seen.append(demand)  # Today's observed sales become available only after this day.
        if t % review_every == 0:
            recent = seen[-28:]
            avg = sum(recent) / len(recent)
            protection_days = lead_days + review_every
            safety = (1.28 * pstdev(recent) * sqrt(protection_days)) if buffered else 0.0
            stock_position = stock + sum(pending.values())
            need = max(0, ceil(avg * protection_days + safety) - stock_position)
            order = _ceil_pack(need, minimum, pack)
            if order:
                pending[t + lead_days] += order
                result['orders_placed'] += 1
                result['units_ordered'] += order
    days = len(replay_demand)
    result['fill_rate_pct'] = round(100 * result['units_filled'] / result['observed_demand_units'], 2) if result['observed_demand_units'] else None
    result['stockout_day_pct'] = round(100 * result['shortage_days'] / days, 2)
    result['average_ending_stock_units'] = round(result['inventory_days'] / days, 2)
    result['holding_cost_usd'] = round(result['holding_cost_usd'], 2)
    for key in ('units_filled', 'unmet_observed_units', 'inventory_days'):
        result[key] = round(result[key], 2)
    return result


def aggregate(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    if not rows:
        raise ValueError('No series to evaluate')
    keys = ['observed_demand_units', 'units_filled', 'unmet_observed_units', 'shortage_days',
            'holding_cost_usd', 'inventory_days', 'orders_placed', 'units_ordered']
    totals = {k: round(sum(r[k] for r in rows), 2) for k in keys}
    total_days = sum(r['days'] for r in rows)
    totals['fill_rate_pct'] = round(100 * totals['units_filled'] / totals['observed_demand_units'], 2) if totals['observed_demand_units'] else None
    totals['stockout_day_pct'] = round(100 * totals['shortage_days'] / total_days, 2)
    totals['average_ending_stock_units_per_series'] = round(totals['inventory_days'] / total_days, 2)
    totals['series_count'] = len(rows)
    totals['replay_days_per_series'] = rows[0]['days']
    return totals


def run_replay() -> dict:
    url = get_database_url()
    if not url:
        raise RuntimeError('PostgreSQL must be configured for SQL-backed policy replay')
    with get_engine(url).connect() as conn:
        snapshot = conn.execute(text('''
           SELECT dataset_version, as_of_date, source_sha256 FROM dataset_imports WHERE id = 1
        ''')).mappings().one()
        end = snapshot['as_of_date']
        start = end - timedelta(days=27)
        history_start = start - timedelta(days=28)
        records = conn.execute(text('''
           SELECT d.date, d.sku_id, d.warehouse_id, d.units_sold,
                  d.opening_stock, d.stockout_observed,
                  s.lead_time_days, s.min_order_qty, s.case_pack, p.unit_cost_usd
             FROM daily_sales d
             JOIN products p ON p.sku_id=d.sku_id
             JOIN suppliers s ON s.supplier_id=p.supplier_id
            WHERE d.date BETWEEN :a AND :b
            ORDER BY d.sku_id, d.warehouse_id, d.date
        '''), {'a': history_start, 'b': end}).mappings().all()
    grouped = defaultdict(list)
    for record in records:
        grouped[(record['sku_id'], record['warehouse_id'])].append(record)
    policies = {'simple_moving_average': [], 'buffered_moving_average': []}
    for (sku, warehouse), recordset in grouped.items():
        if len(recordset) != 56 or recordset[0]['date'] != history_start or recordset[-1]['date'] != end:
            raise ValueError(f'Incomplete history for {sku}/{warehouse}; no partial simulations')
        if any(recordset[i]['date'] + timedelta(days=1) != recordset[i+1]['date'] for i in range(55)):
            raise ValueError('Missing/duplicate dates in simulation history')
        training = [r['units_sold'] for r in recordset[:28]]
        replay = [r['units_sold'] for r in recordset[28:]]
        sample = recordset[28]
        params = dict(training_demand=training, replay_demand=replay,
                      opening_stock=sample['opening_stock'], lead_days=sample['lead_time_days'],
                      minimum=sample['min_order_qty'], pack=sample['case_pack'],
                      unit_cost_usd=float(sample['unit_cost_usd']))
        for name, buffered in (('simple_moving_average', False), ('buffered_moving_average', True)):
            metrics = simulate_series(**params, buffered=buffered)
            policies[name].append(dict(metrics, days=28))
    if not grouped:
        raise ValueError('No historical SKU/warehouse series available')
    result = {
        'status': 'ready', 'fictional': True,
        'source': 'postgresql', 'dataset_version': snapshot['dataset_version'],
        'dataset_sha256': snapshot['source_sha256'],
        'evaluation_start': start.isoformat(), 'evaluation_end': end.isoformat(),
        'policy_comparison': {name: aggregate(values) for name, values in policies.items()},
        'assumptions': {
            'replay_history_days': 28, 'evaluation_days': 28, 'review_every_days': 7,
            'carrying_rate_annual_pct': 20, 'lead_time': 'fixed supplier lead time',
            'buffered_policy': '28-day observed-sales mean + 1.28 * trailing daily population std * sqrt(lead+review days)',
            'simple_policy': '28-day observed-sales mean with no buffer',
            'starting_inventory': 'historical opening stock on evaluation start; same for both policies',
            'incoming_orders': 'new simulated orders only; historical supplier receipts and prior POs excluded',
            'orders': 'placed at end of review day; arrivals at start of day after fixed lead',
            'simulation_target': 'observed sales (not latent customer demand)',
        },
        'limitations': [
            'Observed historical sales are stockout-censored: simulated fill rates are NOT true customer service levels.',
            'This is a retrospective moving-average inventory-policy replay, NOT a LightGBM-driven policy evaluation.',
            'No historical receipts, existing purchase orders at origin, perishability, or lead-time uncertainty are modeled.',
            'Holding costs assume an illustrative 20% annual rate; they are not measured operating costs.',
            'The same historical period has been examined during forecasting; this is a diagnostic replay, not a newly untouched holdout.',
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def report_status() -> dict:
    if not REPORT.exists():
        return {'status': 'not_run', 'message': 'Run python -m app.inventory.simulation'}
    try:
        saved = json.loads(REPORT.read_text(encoding='utf-8'))
        if saved.get('status') != 'ready':
            return {'status': 'invalid', 'message': 'Invalid report format'}
        return saved
    except (ValueError, OSError):
        return {'status': 'invalid', 'message': 'Error reading report'}


if __name__ == '__main__':
    print(json.dumps(run_replay(), indent=2))
