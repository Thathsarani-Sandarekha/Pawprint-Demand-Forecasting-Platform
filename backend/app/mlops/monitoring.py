"""Evaluate saved forecasts against explicitly supplied subsequent observed sales.
No automatic retraining, network calls or invented actuals. Daily matched-row metrics only.
"""
from __future__ import annotations
import argparse
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
from app.mlops.registry import ArtifactPaths, verify

KEYS = ['date', 'sku_id', 'warehouse_id']


def monitor(actuals: Path, paths: ArtifactPaths = ArtifactPaths(), *, as_of: date | None = None) -> dict:
    status = verify(paths)
    if status['status'] != 'ready':
        raise ValueError('Registered forecast bundle required')
    as_of = as_of or date.today()
    cutoff = date.fromisoformat(status['trained_through'])
    if as_of < cutoff:
        raise ValueError('As-of date predates trained-through date')
    predictions = pd.read_csv(paths.preview, dtype={'sku_id': str, 'warehouse_id': str})
    actual = pd.read_csv(actuals, dtype={'sku_id': str, 'warehouse_id': str})
    for frame, required in ((predictions, {'predicted_units','lower_units','upper_units'}), (actual, {'units_sold','stockout_observed'})):
        if not set(KEYS).union(required).issubset(frame.columns):
            raise ValueError('Missing required forecast or observed-sales columns')
        frame['date'] = pd.to_datetime(frame['date'], errors='raise').dt.normalize()
        if frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any():
            raise ValueError('Missing or duplicate SKU–warehouse–date keys')
        for col in required:
            frame[col] = pd.to_numeric(frame[col], errors='raise')
            if not np.isfinite(frame[col]).all() or (frame[col] < 0).any():
                raise ValueError('Invalid numeric forecast or actual')
    if not actual.stockout_observed.isin([0, 1]).all():
        raise ValueError('Invalid stockout flag')
    if not ((predictions.lower_units <= predictions.predicted_units) & (predictions.predicted_units <= predictions.upper_units)).all():
        raise ValueError('Invalid prediction bounds')
    eligible = predictions[(predictions.date > pd.Timestamp(cutoff)) & (predictions.date <= pd.Timestamp(as_of))]
    matched = eligible.merge(actual[KEYS + ['units_sold', 'stockout_observed']], on=KEYS, validate='one_to_one')
    def metrics(rows):
        if rows.empty:
            return None
        error = rows.predicted_units - rows.units_sold
        total = rows.units_sold.sum()
        return {'observations': len(rows), 'wape_pct': round(float(error.abs().sum()/total*100),4) if total else None,
                'bias_pct': round(float(error.sum()/total*100),4) if total else None,
                'daily_coverage_pct': round(float(rows.units_sold.between(rows.lower_units, rows.upper_units).mean()*100),4)}
    return {'status': 'evaluated' if len(matched) else 'no_matured_actuals', 'run_id': status['run_id'],
            'as_of': as_of.isoformat(), 'trained_through': cutoff.isoformat(), 'days_since_training_cutoff': (as_of-cutoff).days,
            'eligible_forecast_rows': len(eligible), 'matched_rows': len(matched), 'missing_actual_rows': len(eligible)-len(matched),
            'observed_sales': metrics(matched), 'nonstockout_observed_sales': metrics(matched[matched.stockout_observed == 0]),
            'note': 'Matched observed sales only; censored by stockouts. This is an offline check, not continuous production monitoring.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--actuals', type=Path, required=True)
    p.add_argument('--as-of', type=date.fromisoformat)
    p.add_argument('--out', type=Path)
    args = p.parse_args()
    result = json.dumps(monitor(args.actuals, as_of=args.as_of), indent=2, allow_nan=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result+'\n', encoding='utf-8')
    print(result)


if __name__ == '__main__':
    main()
