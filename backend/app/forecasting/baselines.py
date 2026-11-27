"""Leakage-safe, deterministic demand baselines with rolling-origin backtests.

Evaluation uses OBSERVED SALES (not latent demand), and labels zero-ending-stock
rows as potentially censored. No future operational features are loaded.

Usage, from backend/: python -m app.forecasting.baselines
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.data_pipeline.generate import DEFAULT_OUT, ROOT
from app.data_pipeline.validate import validate_dataset
from app.db.connection import get_database_url, get_engine

REPORT_DIR = ROOT / "reports"
REPORT_FILE = REPORT_DIR / "forecasting_baselines_v1.json"
PREVIEW_FILE = REPORT_DIR / "forecasting_baseline_preview_v1.csv"
SALES_COLUMNS = ["date", "sku_id", "warehouse_id", "units_sold", "stockout_observed"]
BASELINES = ("seasonal_naive_7", "moving_average_28")


def load_observed_sales(data_dir: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Prefer PostgreSQL when configured; explicit --data-dir opts into CSV.

    A broken configured database is an error, not an implicit CSV fallback.
    Synthetic latent-demand evaluation truth is never loaded.
    """
    if data_dir is not None:
        data_dir = Path(data_dir)
        validate_dataset(data_dir, report_path=None)
        frame = pd.read_csv(data_dir / "daily_sales.csv.gz", usecols=SALES_COLUMNS, parse_dates=["date"])
        meta = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
        return frame, {"source": "synthetic_csv", "dataset_version": meta["dataset_version"]}

    url = get_database_url()
    if url:
        with get_engine(url).connect() as conn:
            imported = conn.execute(text("SELECT dataset_version, source_sha256 FROM dataset_imports WHERE id = 1")).mappings().one()
            frame = pd.read_sql_query(
                text("SELECT date, sku_id, warehouse_id, units_sold, stockout_observed FROM daily_sales ORDER BY date, sku_id, warehouse_id"),
                conn, parse_dates=["date"],
            )
        return frame, {"source": "postgresql", "dataset_version": imported["dataset_version"], "source_sha256": imported["source_sha256"]}

    return load_observed_sales(DEFAULT_OUT)


def prepare_panel(frame: pd.DataFrame) -> tuple[pd.DatetimeIndex, pd.MultiIndex, np.ndarray, np.ndarray]:
    """Reject incomplete or malformed history; never silently convert missing to zero."""
    missing = set(SALES_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Sales data missing required columns: {sorted(missing)}")
    data = frame[SALES_COLUMNS].copy()
    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.normalize()
    if data["date"].isna().any():
        raise ValueError("Dates must be valid")
    if data[["date", "sku_id", "warehouse_id"]].duplicated().any():
        raise ValueError("Duplicate daily SKU-warehouse rows")
    if data["units_sold"].isna().any() or (data["units_sold"] < 0).any():
        raise ValueError("Units sold must be nonnegative and nonmissing")
    if data["stockout_observed"].isna().any() or not data["stockout_observed"].isin([0, 1]).all():
        raise ValueError("Stockout flag must be 0 or 1")
    sales = data.pivot(index="date", columns=["sku_id", "warehouse_id"], values="units_sold")
    flags = data.pivot(index="date", columns=["sku_id", "warehouse_id"], values="stockout_observed")
    expected = pd.date_range(sales.index.min(), sales.index.max(), freq="D")
    if len(expected) != len(sales.index) or not sales.index.equals(expected) or sales.isna().any().any():
        raise ValueError("Incomplete daily SKU-warehouse history; missing is not zero")
    return expected, sales.columns, sales.to_numpy(dtype=float), flags.reindex(columns=sales.columns).to_numpy(dtype=bool)


def predict_baseline(train: np.ndarray, horizon: int, method: str) -> np.ndarray:
    """Only train[:cutoff] may be passed. No future sales or stock levels."""
    if train.ndim != 2 or not np.isfinite(train).all() or (train < 0).any() or horizon <= 0:
        raise ValueError("Training array must be 2D nonnegative finite values; horizon > 0")
    if method == "seasonal_naive_7":
        if len(train) < 7:
            raise ValueError("At least seven training days are required")
        return np.tile(train[-7:, :], (int(np.ceil(horizon / 7)), 1))[:horizon, :].copy()
    if method == "moving_average_28":
        if len(train) < 28:
            raise ValueError("At least 28 training days are required")
        return np.repeat(train[-28:, :].mean(axis=0, keepdims=True), horizon, axis=0)
    raise ValueError(f"Unknown baseline: {method}")


def seasonal_scale(train: np.ndarray, season: int = 7) -> np.ndarray:
    if len(train) <= season:
        raise ValueError("Insufficient training history for MASE")
    scale = np.abs(train[season:] - train[:-season]).mean(axis=0)
    return np.where(scale > 1e-12, scale, np.nan)


def evaluate(actual: np.ndarray, predicted: np.ndarray, train: np.ndarray, valid: np.ndarray | None = None) -> dict:
    """WAPE/bias percentages; MASE = macro mean of per-series scaled MAE.

    Undefined denominator -> None, never a fabricated zero.
    """
    if actual.shape != predicted.shape or actual.ndim != 2 or actual.shape[1] != train.shape[1]:
        raise ValueError("Actual, predicted and training series dimensions must match")
    mask = np.ones_like(actual, dtype=bool) if valid is None else np.asarray(valid, dtype=bool)
    if mask.shape != actual.shape:
        raise ValueError("Evaluation mask has incorrect shape")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all() or (predicted < 0).any():
        raise ValueError("Evaluation arrays must be finite and predictions nonnegative")
    ae = np.abs(actual - predicted)
    denom = float(actual[mask].sum())
    n = int(mask.sum())
    scale = seasonal_scale(train)
    per_series_count = mask.sum(axis=0)
    per_series_mae = np.divide((ae * mask).sum(axis=0), per_series_count, out=np.full(len(scale), np.nan), where=per_series_count > 0)
    mase_values = per_series_mae / scale
    mase_values = mase_values[np.isfinite(mase_values)]
    return {
        "observations": n,
        "wape_pct": round(float(ae[mask].sum()) / denom * 100, 4) if denom > 0 else None,
        "bias_pct": round(float((predicted - actual)[mask].sum()) / denom * 100, 4) if denom > 0 else None,
        "mae_units": round(float(ae[mask].mean()), 4) if n else None,
        "mase": round(float(mase_values.mean()), 4) if len(mase_values) else None,
        "mase_valid_series": int(len(mase_values)),
    }


def _backtest_arrays(dates, series, sales, flags, horizon, folds, min_history_days) -> tuple[dict, np.ndarray, dict]:
    n = len(dates)
    first_origin = n - (folds + 1) * horizon
    if first_origin < max(28, min_history_days):
        raise ValueError("Insufficient history for rolling folds plus untouched final holdout")
    fold_results = []
    totals = {name: {"absolute": 0.0, "demand": 0.0, "errors": 0.0, "count": 0} for name in BASELINES}
    # Folds select the baseline. The last horizon is withheld until selection.
    for fold in range(folds):
        cutoff = first_origin + fold * horizon
        actual = sales[cutoff:cutoff + horizon]
        fold_row = {"fold": fold + 1, "train_through": dates[cutoff - 1].date().isoformat(),
                    "validation_start": dates[cutoff].date().isoformat(),
                    "validation_end": dates[cutoff + horizon - 1].date().isoformat(), "metrics": {}}
        for name in BASELINES:
            pred = predict_baseline(sales[:cutoff], horizon, name)
            result = evaluate(actual, pred, sales[:cutoff])
            result["wape_nonstockout_pct"] = evaluate(actual, pred, sales[:cutoff], valid=~flags[cutoff:cutoff + horizon])["wape_pct"]
            fold_row["metrics"][name] = result
            totals[name]["absolute"] += float(np.abs(actual - pred).sum())
            totals[name]["demand"] += float(actual.sum())
            totals[name]["errors"] += float((pred - actual).sum())
            totals[name]["count"] += int(actual.size)
        fold_results.append(fold_row)
    aggregate = {}
    for name, stats in totals.items():
        aggregate[name] = {
            "wape_pct": round(100 * stats["absolute"] / stats["demand"], 4) if stats["demand"] else None,
            "bias_pct": round(100 * stats["errors"] / stats["demand"], 4) if stats["demand"] else None,
            "observations": stats["count"],
        }
    valid_models = [m for m in BASELINES if aggregate[m]["wape_pct"] is not None]
    if not valid_models:
        raise ValueError("No observed validation demand; WAPE undefined for model selection")
    selected = min(valid_models, key=lambda name: (aggregate[name]["wape_pct"], BASELINES.index(name)))

    cutoff = n - horizon
    holdout = {"train_through": dates[cutoff - 1].date().isoformat(),
               "test_start": dates[cutoff].date().isoformat(),
               "test_end": dates[-1].date().isoformat(),
               "metrics": {}}
    for name in BASELINES:
        pred = predict_baseline(sales[:cutoff], horizon, name)
        row = evaluate(sales[cutoff:], pred, sales[:cutoff])
        row["wape_nonstockout_pct"] = evaluate(sales[cutoff:], pred, sales[:cutoff], valid=~flags[cutoff:])["wape_pct"]
        holdout["metrics"][name] = row

    holdout_pred = predict_baseline(sales[:cutoff], horizon, selected)
    warehouses = sorted(set(series.get_level_values("warehouse_id")))
    by_warehouse = {}
    for warehouse in warehouses:
        indexes = np.where(series.get_level_values("warehouse_id") == warehouse)[0]
        by_warehouse[warehouse] = evaluate(sales[cutoff:, indexes], holdout_pred[:, indexes], sales[:cutoff, indexes])
    holdout["selected_by_warehouse"] = by_warehouse

    future_pred = predict_baseline(sales, horizon, selected)
    report = {"rolling_folds": fold_results, "backtest_summary": aggregate, "selected_baseline": selected, "untouched_test": holdout}
    return report, future_pred, {"cutoff": dates[-1].date().isoformat(), "forecast_start": (dates[-1] + timedelta(days=1)).date().isoformat(),
                                 "forecast_end": (dates[-1] + timedelta(days=horizon)).date().isoformat()}


def run_baselines(frame: pd.DataFrame, source: dict, horizon: int = 28, folds: int = 4,
                  min_history_days: int = 84) -> tuple[dict, pd.DataFrame]:
    if horizon <= 0 or folds < 2:
        raise ValueError("Horizon must be positive and at least two rolling folds are required")
    dates, series, sales, flags = prepare_panel(frame)
    result, future, future_meta = _backtest_arrays(dates, series, sales, flags, horizon, folds, min_history_days)
    dates_future = pd.date_range(dates[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
    future_df = pd.DataFrame(future, index=dates_future, columns=series)
    future_df.index.name = "date"
    future_df.columns.names = ["sku_id", "warehouse_id"]
    preview = future_df.stack(["sku_id", "warehouse_id"], future_stack=True).rename("predicted_units").reset_index()
    preview["date"] = preview["date"].dt.date.astype(str)
    preview["predicted_units"] = preview["predicted_units"].round(4)
    report = {
        "status": "ready", "synthetic_data": True, "model_type": "baseline_only",
        "source": source["source"], "dataset_version": source["dataset_version"],
        "source_sha256": source.get("source_sha256"),
        "forecast_granularity": "SKU-warehouse-day", "horizon_days": horizon, "rolling_fold_count": folds,
        "sku_warehouse_series": len(series), "history_start": dates[0].date().isoformat(),
        "history_end": dates[-1].date().isoformat(), "future": future_meta,
        "methodology": {
            "forecast_target": "Observed unit sales; may be censored by stockouts, not latent customer demand",
            "split": "Chronological, expanding-origin folds to select baseline, then one untouched final horizon for test",
            "feature_availability": "Baseline uses only sales before each forecast cutoff; no future observed sales, stock, or latent-demand columns",
            "seasonal_naive_7": "Repeat the last 7 observed sales days for each future week",
            "moving_average_28": "Repeat the average of the last 28 observed sales days",
            "wape_pct": "100*sum(abs(actual-predicted))/sum(actual); undefined if demand sum is zero",
            "mase": "Unweighted mean per-series MAE divided by training-only seasonal naive MAE (7-day); zero-scale series excluded",
            "bias_pct": "100*sum(predicted-actual)/sum(actual), positive indicates overforecast",
            "nonstockout_metric": "Extra WAPE excludes days with zero ending stock, which are potential demand-censoring days",
            "prediction_intervals": "Not implemented yet: no interval coverage claims",
            "interpretation": "Measured on fictional synthetic observed sales, not real pet-retail business performance",
        },
        **result,
    }
    return report, preview


def write_artifacts(report: dict, preview: pd.DataFrame, output_dir: Path = REPORT_DIR) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file, preview_file = output_dir / REPORT_FILE.name, output_dir / PREVIEW_FILE.name
    # Avoid a half-written report from declaring an incomplete run ready.
    preview_temp = preview_file.with_suffix(".csv.tmp")
    report_temp = report_file.with_suffix(".json.tmp")
    preview.to_csv(preview_temp, index=False)
    report_temp.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    preview_temp.replace(preview_file)
    report_temp.replace(report_file)
    return report_file, preview_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Seasonal naive and moving average forecasting; synthetic data only")
    parser.add_argument("--data-dir", type=Path, default=None, help="Use validated synthetic CSV instead of configured PostgreSQL")
    parser.add_argument("--horizon", type=int, default=28)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--out", type=Path, default=REPORT_DIR)
    args = parser.parse_args()
    frame, source = load_observed_sales(args.data_dir)
    report, preview = run_baselines(frame, source, horizon=args.horizon, folds=args.folds)
    report_file, preview_file = write_artifacts(report, preview, args.out)
    print(json.dumps({"status": "ready", "dataset_version": report["dataset_version"],
                      "source": report["source"], "selected_baseline": report["selected_baseline"],
                      "backtest_summary": report["backtest_summary"],
                      "untouched_test": report["untouched_test"]["metrics"],
                      "report_path": str(report_file), "preview_path": str(preview_file)}, indent=2))


if __name__ == "__main__":
    main()
