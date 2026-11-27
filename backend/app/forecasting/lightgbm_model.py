"""Global recursive LightGBM forecasting for the fictional pet-retail data.

Offline training only. Features for training day t use observations up to t-1;
recursive inference substitutes *prior predictions* after the forecast origin.
All promotions used at inference come from the explicitly scheduled promotion
calendar, never from future recorded sales rows. The synthetic demo assumes
promotional events were scheduled in advance (the dataset lacks created_at).

Run from backend/:
    python -m app.forecasting.lightgbm_model
    python -m app.forecasting.lightgbm_model --data-dir ../data/synthetic

Does not ingest evaluation_only/latent_demand_truth.csv.gz.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sqlalchemy import text

from app.data_pipeline.generate import DEFAULT_OUT, ROOT
from app.forecasting.baselines import (
    REPORT_DIR, BASELINES, evaluate, load_observed_sales, predict_baseline, prepare_panel,
)
from app.db.connection import get_database_url, get_engine
from app.forecasting.uncertainty import origin_scale, fit_bands, apply_bands, evaluate_bands

ML_REPORT_FILE = REPORT_DIR / "forecasting_ml_v1.json"
ML_PREVIEW_FILE = REPORT_DIR / "forecasting_ml_preview_v1.csv"
MODEL_DIR = ROOT / "models"
MODEL_FILE = MODEL_DIR / "lightgbm_global_v1.joblib"
FEATURES = (
    "lag_1", "lag_7", "lag_14", "lag_28", "lag_56", "mean_7", "mean_28",
    "mean_56", "day_of_week", "month", "day_of_year_sin", "day_of_year_cos",
    "is_weekend", "promo_discount", "sku_code", "warehouse_code", "category_code",
    "repeat_purchase", "product_unit_price",
)
MAX_LAG = 56


@dataclass(frozen=True)
class TrainConfig:
    horizon: int = 28
    folds: int = 4
    max_training_days: int = 365
    estimators: int = 140
    learning_rate: float = 0.065
    leaves: int = 31
    seed: int = 42
    min_history_days: int = 120

    def validate(self):
        if self.horizon < 1 or self.folds < 2 or self.max_training_days < MAX_LAG + 14:
            raise ValueError("Invalid horizon, fold count or training window")
        if self.min_history_days < MAX_LAG + 14 or self.estimators < 1:
            raise ValueError("Need sufficient history and at least one estimator")


def _catalog(data_dir: Path | None, series: pd.MultiIndex, dates: pd.DatetimeIndex,
             horizon: int) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Read static catalog and scheduled promotions (not future sales/stock)."""
    if data_dir is None and get_database_url():
        with get_engine(get_database_url()).connect() as conn:
            products = pd.read_sql_query(text("SELECT sku_id, category, repeat_purchase, unit_price_usd FROM products"), conn)
            promos = pd.read_sql_query(text("SELECT sku_id, start_date, end_date, discount_pct FROM promotions"), conn)
    else:
        folder = Path(data_dir) if data_dir is not None else DEFAULT_OUT
        products = pd.read_csv(folder / "products.csv", usecols=["sku_id", "category", "repeat_purchase", "unit_price_usd"])
        promos = pd.read_csv(folder / "promotions.csv", usecols=["sku_id", "start_date", "end_date", "discount_pct"])
    products = products.set_index("sku_id")
    skus = series.get_level_values("sku_id").to_numpy()
    whs = series.get_level_values("warehouse_id").to_numpy()
    if not set(skus).issubset(set(products.index)):
        raise ValueError("Missing SKU catalog records")
    cat = products.loc[skus]
    category_map = {name: idx for idx, name in enumerate(sorted(products.category.unique()))}
    sku_map = {name: idx for idx, name in enumerate(sorted(products.index))}
    wh_map = {name: idx for idx, name in enumerate(sorted(set(whs)))}
    static = {
        "sku_code": np.array([sku_map[x] for x in skus], dtype=np.float32),
        "warehouse_code": np.array([wh_map[x] for x in whs], dtype=np.float32),
        "category_code": np.array([category_map[x] for x in cat.category], dtype=np.float32),
        "repeat_purchase": cat.repeat_purchase.astype(int).to_numpy(dtype=np.float32),
        "product_unit_price": cat.unit_price_usd.to_numpy(dtype=np.float32),
    }
    all_days = pd.date_range(dates[0], periods=len(dates) + horizon, freq="D")
    schedule = np.zeros((len(all_days), len(series)), dtype=np.float32)
    sku_columns = {sku: np.flatnonzero(skus == sku) for sku in set(skus)}
    # A fixed synthetic promotional plan is assumed known at each origin.
    for p in promos.itertuples(index=False):
        positions = sku_columns.get(p.sku_id)
        if positions is None:
            continue
        start = max(0, int((pd.Timestamp(p.start_date) - all_days[0]).days))
        end = min(len(all_days), int((pd.Timestamp(p.end_date) - all_days[0]).days) + 1)
        if start < end:
            schedule[start:end, positions] = np.maximum(schedule[start:end, positions], float(p.discount_pct))
    return static, schedule


def _calendar_features(day: pd.Timestamp) -> dict[str, float]:
    day = pd.Timestamp(day)
    return {"day_of_week": float(day.dayofweek), "month": float(day.month),
            "day_of_year_sin": float(np.sin(2 * np.pi * day.dayofyear / 365.25)),
            "day_of_year_cos": float(np.cos(2 * np.pi * day.dayofyear / 365.25)),
            "is_weekend": float(day.dayofweek >= 5)}


def _features_at(history: np.ndarray, day: pd.Timestamp,
                 static: dict[str, np.ndarray], discount: np.ndarray) -> np.ndarray:
    """One feature row per series for the *next* date after history."""
    if len(history) < MAX_LAG or history.ndim != 2 or history.shape[1] != len(discount):
        raise ValueError("Insufficient historical rows or inconsistent series dimensions")
    if not np.isfinite(history).all():
        raise ValueError("History must contain only finite values")
    lag = {f"lag_{n}": history[-n, :] for n in (1, 7, 14, 28, 56)}
    mean = {f"mean_{n}": history[-n:, :].mean(axis=0) for n in (7, 28, 56)}
    cal = _calendar_features(day)
    nseries = history.shape[1]
    columns = {**lag, **mean, **{key: np.full(nseries, value) for key, value in cal.items()},
               "promo_discount": discount, **static}
    return np.column_stack([columns[name] for name in FEATURES]).astype(np.float32)


def build_training_rows(dates: pd.DatetimeIndex, sales: np.ndarray, static: dict[str, np.ndarray],
                        schedule: np.ndarray, cutoff: int, max_training_days: int) -> tuple[np.ndarray, np.ndarray]:
    """Each row uses only observations strictly earlier than its target date."""
    if cutoff > len(sales) or cutoff < MAX_LAG + 1:
        raise ValueError("Invalid cutoff")
    start = max(MAX_LAG, cutoff - max_training_days)
    xx, yy = [], []
    for t in range(start, cutoff):
        xx.append(_features_at(sales[t - MAX_LAG:t], dates[t], static, schedule[t]))
        yy.append(sales[t, :].astype(np.float32))
    return np.concatenate(xx, axis=0), np.concatenate(yy)


def fit_model(x: np.ndarray, y: np.ndarray, config: TrainConfig) -> lgb.LGBMRegressor:
    model = lgb.LGBMRegressor(
        objective="regression", n_estimators=config.estimators, learning_rate=config.learning_rate,
        num_leaves=config.leaves, max_depth=9, min_child_samples=30, max_bin=127,
        random_state=config.seed, n_jobs=2, verbosity=-1, deterministic=True,
        force_col_wise=True,
    )
    model.fit(pd.DataFrame(x, columns=FEATURES), y)
    return model


def recursive_forecast(model: lgb.LGBMRegressor, dates: pd.DatetimeIndex,
                       train: np.ndarray, static: dict[str, np.ndarray],
                       planned_discounts: np.ndarray, horizon: int) -> np.ndarray:
    """Past observations + earlier predicted values; never future observed sales."""
    if horizon < 1 or len(train) < MAX_LAG or len(planned_discounts) < horizon:
        raise ValueError("Insufficient history or promotion plan")
    context = train[-MAX_LAG:, :].copy()
    result = np.empty((horizon, train.shape[1]), dtype=np.float64)
    for step in range(horizon):
        features = _features_at(context, dates[step], static, planned_discounts[step])
        guess = np.asarray(model.booster_.predict(features, num_threads=2), dtype=np.float64)
        result[step, :] = np.maximum(0, guess)
        context = np.vstack((context[1:, :], result[step, :]))
    return result


def run_ml(frame: pd.DataFrame, source: dict, *, data_dir: Path | None = None,
           config: TrainConfig = TrainConfig()) -> tuple[dict, pd.DataFrame, lgb.LGBMRegressor]:
    """Fit rolling folds, final holdout and full-history production candidate.

    ML-vs-baseline selection uses only rolling fold scores. The final 28-day
    holdout is scored *after* selection; no tuning on its performance.
    """
    config.validate()
    dates, series, sales, flags = prepare_panel(frame)
    horizon, folds = config.horizon, config.folds
    first_origin = len(dates) - (folds + 1) * horizon
    if first_origin < max(config.min_history_days, MAX_LAG + 1):
        raise ValueError("Insufficient history for rolling folds and final holdout")
    static, schedule = _catalog(data_dir, series, dates, horizon)
    fold_results = []
    fold_predictions = []  # Out-of-fold observed outcomes/predictions and past-only scales.
    aggregate = {name: {"error": 0.0, "demand": 0.0, "signed": 0.0} for name in (*BASELINES, "lightgbm_global")}
    for fold in range(folds):
        cutoff = first_origin + fold * horizon
        x, y = build_training_rows(dates, sales, static, schedule, cutoff, config.max_training_days)
        model = fit_model(x, y, config)
        calendar = dates[cutoff:cutoff + horizon]
        ml = recursive_forecast(model, calendar, sales[:cutoff], static, schedule[cutoff:cutoff+horizon], horizon)
        actual = sales[cutoff:cutoff+horizon]
        metrics = {}
        predictions_for_fold = {}
        for name in (*BASELINES, "lightgbm_global"):
            pred = ml if name == "lightgbm_global" else predict_baseline(sales[:cutoff], horizon, name)
            predictions_for_fold[name] = pred
            row = evaluate(actual, pred, sales[:cutoff])
            row["wape_nonstockout_pct"] = evaluate(actual, pred, sales[:cutoff], valid=~flags[cutoff:cutoff+horizon])["wape_pct"]
            metrics[name] = row
            agg = aggregate[name]
            agg["error"] += float(np.abs(actual - pred).sum())
            agg["demand"] += float(actual.sum())
            agg["signed"] += float((pred - actual).sum())
        fold_predictions.append((actual, predictions_for_fold, origin_scale(sales[:cutoff]), flags[cutoff:cutoff+horizon]))
        fold_results.append({"fold": fold + 1, "train_through": str(dates[cutoff-1].date()),
                             "validation_start": str(calendar[0].date()),
                             "validation_end": str(calendar[-1].date()), "metrics": metrics})
    compare = {name: {"wape_pct": round(100 * v["error"] / v["demand"], 4) if v["demand"] > 0 else None,
                      "bias_pct": round(100 * v["signed"] / v["demand"], 4) if v["demand"] > 0 else None}
               for name, v in aggregate.items()}
    valid = [name for name in compare if compare[name]["wape_pct"] is not None]
    if not valid:
        raise ValueError("No validation demand for candidate selection")
    selected = min(valid, key=lambda n: compare[n]["wape_pct"])
    holdout_cutoff = len(dates) - horizon
    x, y = build_training_rows(dates, sales, static, schedule, holdout_cutoff, config.max_training_days)
    holdout_model = fit_model(x, y, config)
    holdout_ml = recursive_forecast(holdout_model, dates[holdout_cutoff:], sales[:holdout_cutoff],
                                   static, schedule[holdout_cutoff:], horizon)
    holdout_scores = {}
    holdout_predictions = {}
    for name in (*BASELINES, "lightgbm_global"):
        pred = holdout_ml if name == "lightgbm_global" else predict_baseline(sales[:holdout_cutoff], horizon, name)
        holdout_predictions[name] = pred
        score = evaluate(sales[holdout_cutoff:], pred, sales[:holdout_cutoff])
        score["wape_nonstockout_pct"] = evaluate(sales[holdout_cutoff:], pred, sales[:holdout_cutoff],
                                                 valid=~flags[holdout_cutoff:])["wape_pct"]
        holdout_scores[name] = score
    # Calibration excludes the final rolling fold (diagnostic) and the 28-day
    # untouched holdout. Only the selected model's earlier fold residuals enter.
    calibrator = fit_bands([(item[0], item[1][selected], item[2]) for item in fold_predictions[:-1]], level=0.90)
    val_actual, val_predictions, val_scale, val_flags = fold_predictions[-1]
    val_bounds = apply_bands(val_predictions[selected], val_scale, calibrator)
    validation_coverage = evaluate_bands(val_actual, val_flags, *val_bounds)
    holdout_bounds = apply_bands(holdout_predictions[selected], origin_scale(sales[:holdout_cutoff]), calibrator)
    holdout_coverage = evaluate_bands(sales[holdout_cutoff:], flags[holdout_cutoff:], *holdout_bounds)
    x, y = build_training_rows(dates, sales, static, schedule, len(dates), config.max_training_days)
    production_model = fit_model(x, y, config)
    next_dates = pd.date_range(dates[-1] + timedelta(days=1), periods=horizon, freq="D")
    ml_future = recursive_forecast(production_model, next_dates, sales, static, schedule[len(dates):], horizon)
    future = ml_future if selected == "lightgbm_global" else predict_baseline(sales, horizon, selected)
    prod_scale = origin_scale(sales)
    future_daily_lower, future_daily_upper, _, _ = apply_bands(future, prod_scale, calibrator)
    pred_frame = pd.DataFrame(future, index=next_dates, columns=series)
    pred_frame.index.name = "date"
    pred_frame.columns.names = ["sku_id", "warehouse_id"]
    preview = pred_frame.stack(["sku_id", "warehouse_id"], future_stack=True).rename("predicted_units").reset_index()
    preview["date"] = preview["date"].dt.date.astype(str)
    preview["predicted_units"] = preview["predicted_units"].round(4)
    for label, bounds in (("lower_units", future_daily_lower), ("upper_units", future_daily_upper)):
        bframe = pd.DataFrame(bounds, index=next_dates, columns=series)
        bframe.index.name = "date"
        bframe.columns.names = ["sku_id", "warehouse_id"]
        long_bounds = bframe.stack(["sku_id", "warehouse_id"], future_stack=True).rename(label).reset_index()
        long_bounds["date"] = long_bounds["date"].dt.date.astype(str)
        preview = preview.merge(long_bounds, on=["date", "sku_id", "warehouse_id"], validate="one_to_one")
    scale_by_series = {key: float(value) for key, value in zip(series, prod_scale)}
    preview["uncertainty_scale"] = [scale_by_series[(row.sku_id, row.warehouse_id)] for row in preview.itertuples(index=False)]
    preview[["lower_units", "upper_units", "uncertainty_scale"]] = preview[["lower_units", "upper_units", "uncertainty_scale"]].round(4)
    report = {
        "status": "ready", "synthetic_data": True,
        "model_type": "trained_ml_comparison", "dataset_version": source["dataset_version"],
        "source": source["source"], "source_sha256": source.get("source_sha256"),
        "forecast_granularity": "SKU-warehouse-day", "horizon_days": horizon, "rolling_fold_count": folds,
        "sku_warehouse_series": len(series), "history_start": str(dates[0].date()),
        "history_end": str(dates[-1].date()), "selected_model": selected,
        "selected_by": "Lowest aggregate WAPE across rolling validation folds; never the final holdout",
        "model_version": "lightgbm-global-v1", "config": asdict(config), "features": list(FEATURES),
        "feature_importance": dict(zip(FEATURES, map(int, production_model.feature_importances_))),
        "rolling_folds": fold_results, "backtest_summary": compare,
        "prediction_intervals": {
            "nominal_coverage_pct": 90,
            "method": "Past-only scale-normalized absolute rolling-origin residual quantiles, separated by day and 7-day week",
            "calibration_folds": list(range(1, folds)),
            "validation_fold": folds,
            "observations_per_horizon": calibrator.observations_per_horizon,
            "daily_quantiles": [round(q, 8) for q in calibrator.daily_quantiles],
            "weekly_quantiles": [round(q, 8) for q in calibrator.weekly_quantiles],
            "validation_coverage": validation_coverage,
            "untouched_test_coverage": holdout_coverage,
            "notes": "Nominal marginal intervals for observed sales, not guaranteed SKU-specific coverage or latent demand coverage during stockouts. Model selection used all rolling folds; calibration estimates may be selection-dependent. Final holdout was not used for quantile fitting.",
        },
        "untouched_test": {"train_through": str(dates[holdout_cutoff-1].date()),
                           "test_start": str(dates[holdout_cutoff].date()),
                           "test_end": str(dates[-1].date()), "metrics": holdout_scores},
        "future": {"cutoff": str(dates[-1].date()), "forecast_start": str(next_dates[0].date()),
                   "forecast_end": str(next_dates[-1].date())},
        "methodology": {
            "target": "Observed units sold; zero-ending-stock days can censor actual demand",
            "training": "One global LightGBM regressor over SKU-warehouse series with past-only lagged demand",
            "prediction": "Recursive multi-step: future lags are previous predictions, never actual future sales",
            "promotions": "Synthetic promotions table treated as an advance-published plan at every cutoff; this data lacks announcement timestamps. Historical outcomes are not used as future features.",
            "cutoffs": "Four expanding-origin 28-day rolling backtests plus separately scored final holdout",
            "selection": "Candidate chosen using rolling folds alone; final holdout is descriptive, not used for selection",
            "metadata": "Only product catalog, SKU, warehouse, known date and planned promotions; no latent ground truth, future stock, future sales, or test labels in features",
            "intervals": "90% nominal scaled residual bands calibrated using folds 1..N-1; last rolling fold and untouched holdout scored separately. Weekly totals calibrated directly (not summed daily bounds).",
            "scope": "Synthetic portfolio benchmark, not a claim of production retailer forecast accuracy",
        },
    }
    return report, preview, production_model


def write_ml_artifacts(report: dict, preview: pd.DataFrame, model: lgb.LGBMRegressor,
                       out: Path = REPORT_DIR, model_dir: Path = MODEL_DIR) -> tuple[Path, Path, Path]:
    out, model_dir = Path(out), Path(model_dir)
    out.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    # A model artifact is produced for offline use. Never deserialize it from an API request.
    target = model_dir / MODEL_FILE.name
    model_tmp = target.with_suffix(".joblib.tmp")
    joblib.dump(model, model_tmp)
    model_tmp.replace(target)
    report = dict(report)
    report["model_artifact_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    report["model_artifact_path"] = str(target)
    out_csv = out / ML_PREVIEW_FILE.name
    out_report = out / ML_REPORT_FILE.name
    csv_tmp = out_csv.with_suffix(".csv.tmp")
    report_tmp = out_report.with_suffix(".json.tmp")
    preview.to_csv(csv_tmp, index=False)
    csv_tmp.replace(out_csv)
    report_tmp.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    report_tmp.replace(out_report)
    return out_report, out_csv, target


def main():
    parser = argparse.ArgumentParser(description="Train and evaluate global LightGBM; synthetic pet retail")
    parser.add_argument("--data-dir", type=Path, default=None, help="Use explicit CSV dataset rather than PostgreSQL")
    parser.add_argument("--horizon", type=int, default=28)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--estimators", type=int, default=140)
    parser.add_argument("--max-training-days", type=int, default=365)
    parser.add_argument("--out", type=Path, default=REPORT_DIR)
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    args = parser.parse_args()
    config = TrainConfig(horizon=args.horizon, folds=args.folds, estimators=args.estimators,
                         max_training_days=args.max_training_days)
    frame, info = load_observed_sales(args.data_dir)
    report, preview, model = run_ml(frame, info, data_dir=args.data_dir, config=config)
    report_file, csv_file, model_file = write_ml_artifacts(report, preview, model, args.out, args.model_dir)
    print(json.dumps({"status": report["status"], "source": report["source"],
                      "selected_model": report["selected_model"], "backtest_summary": report["backtest_summary"],
                      "untouched_test": report["untouched_test"]["metrics"],
                      "interval_coverage": report["prediction_intervals"]["untouched_test_coverage"],
                      "report_path": str(report_file), "preview_path": str(csv_file),
                      "model_path": str(model_file)}, indent=2))


if __name__ == "__main__":
    main()
