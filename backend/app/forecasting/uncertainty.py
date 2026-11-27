"""Empirical scaled-residual intervals for pet-retail recursive forecasts.

Calibration uses earlier held-out rolling forecasts only; it never reads the
final 28-day test outcomes or the future target when determining quantiles.

These are approximate *marginal* 90% intervals across SKU-warehouse series,
not a guarantee of conditional coverage, independent residuals, or true
latent demand coverage when stockouts censor observed sales.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class IntervalBands:
    level: float
    daily_quantiles: tuple[float, ...]
    weekly_quantiles: tuple[float, ...]
    calibration_folds: int
    observations_per_horizon: int


def origin_scale(past: np.ndarray) -> np.ndarray:
    """Past-only demand scale; a one-unit floor avoids division by zero."""
    if past.ndim != 2 or past.shape[0] < 28 or not np.isfinite(past).all() or (past < 0).any():
        raise ValueError("Need at least 28 days of finite nonnegative history")
    return np.maximum(1.0, past[-28:].mean(axis=0))


def week_slices(horizon: int) -> list[slice]:
    if horizon < 1:
        raise ValueError("Horizon must be positive")
    return [slice(start, min(horizon, start + 7)) for start in range(0, horizon, 7)]


def _quantile(scores: np.ndarray, coverage: float) -> float:
    """Finite-sample upper order statistic (conformal-style split quantile)."""
    values = np.asarray(scores, dtype=float).ravel()
    if not 0 < coverage < 1 or not len(values) or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Invalid calibration scores or coverage")
    rank = min(len(values), int(np.ceil((len(values) + 1) * coverage))) - 1
    return float(np.partition(values, rank)[rank])


def fit_bands(folds: list[tuple[np.ndarray, np.ndarray, np.ndarray]], level: float = 0.90) -> IntervalBands:
    """Fit separate day-position and week-position scaled absolute residuals.

    Each input tuple is (actual, forecast, past-only daily scale). The entire
    last rolling fold should be withheld by the caller for validation.
    """
    if not folds or not 0 < level < 1:
        raise ValueError("Calibration requires prior folds and coverage in (0,1)")
    h, n = folds[0][0].shape
    if h < 1 or n < 1:
        raise ValueError("Empty calibration fold")
    daily = [[] for _ in range(h)]
    weeks = week_slices(h)
    weekly = [[] for _ in weeks]
    for actual, forecast, scale in folds:
        if actual.shape != (h, n) or forecast.shape != (h, n) or scale.shape != (n,):
            raise ValueError("Mismatched calibration dimensions")
        if not all(np.isfinite(x).all() for x in (actual, forecast, scale)) or np.any(scale < 1):
            raise ValueError("Invalid calibration values")
        errors = np.abs(actual - forecast)
        for step in range(h):
            daily[step].extend((errors[step] / scale).tolist())
        for idx, chunk in enumerate(weeks):
            weekly[idx].extend((np.abs(actual[chunk].sum(axis=0) - forecast[chunk].sum(axis=0)) /
                                (scale * (chunk.stop - chunk.start))).tolist())
    return IntervalBands(level, tuple(_quantile(np.array(scores), level) for scores in daily),
                         tuple(_quantile(np.array(scores), level) for scores in weekly),
                         len(folds), len(daily[0]))


def apply_bands(forecast: np.ndarray, scale: np.ndarray, bands: IntervalBands) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Nonnegative lower/upper intervals on daily and weekly total demand."""
    if forecast.ndim != 2 or scale.shape != (forecast.shape[1],):
        raise ValueError("Inconsistent forecast and scale shapes")
    if len(bands.daily_quantiles) != len(forecast) or len(bands.weekly_quantiles) != len(week_slices(len(forecast))):
        raise ValueError("Forecast horizon doesn't match calibrated horizon")
    if not np.isfinite(forecast).all() or (forecast < 0).any() or not np.isfinite(scale).all() or (scale < 1).any():
        raise ValueError("Invalid forecast or demand scale")
    daily_radius = np.asarray(bands.daily_quantiles)[:, None] * scale[None, :]
    lower = np.maximum(0, forecast - daily_radius)
    upper = forecast + daily_radius
    weekly_lower, weekly_upper = [], []
    for idx, chunk in enumerate(week_slices(len(forecast))):
        totals = forecast[chunk].sum(axis=0)
        radius = bands.weekly_quantiles[idx] * scale * (chunk.stop - chunk.start)
        weekly_lower.append(np.maximum(0, totals - radius))
        weekly_upper.append(totals + radius)
    return lower, upper, np.stack(weekly_lower), np.stack(weekly_upper)


def coverage_report(actual: np.ndarray, lower: np.ndarray, upper: np.ndarray,
                    flags: np.ndarray | None = None, *, weekly: bool = False) -> dict:
    """Coverage/width by individual daily or aggregate weekly intervals."""
    if actual.shape != lower.shape or actual.shape != upper.shape or actual.ndim != 2:
        raise ValueError("Shape mismatch in interval coverage")
    if not np.isfinite(actual).all() or not np.isfinite(lower).all() or not np.isfinite(upper).all():
        raise ValueError("Nonfinite interval coverage inputs")
    if not ((lower <= upper) & (lower >= 0)).all():
        raise ValueError("Invalid interval endpoints")
    if flags is not None and flags.shape != actual.shape:
        raise ValueError("Stockout flag shape mismatch")
    contains = (actual >= lower) & (actual <= upper)
    result = {
        "observations": int(actual.size),
        "coverage_pct": round(100 * float(contains.mean()), 2),
        "mean_width_units": round(float((upper - lower).mean()), 3),
    }
    if flags is not None:
        valid = ~flags.astype(bool)
        result["nonstockout_observations"] = int(valid.sum())
        result["nonstockout_coverage_pct"] = round(100 * float(contains[valid].mean()), 2) if valid.any() else None
    result["unit"] = "weekly total" if weekly else "daily units"
    return result


def evaluate_bands(actual: np.ndarray, flags: np.ndarray, lower: np.ndarray, upper: np.ndarray,
                   week_lower: np.ndarray, week_upper: np.ndarray) -> dict:
    windows = week_slices(len(actual))
    weekly_actual = np.stack([actual[chunk].sum(axis=0) for chunk in windows])
    weekly_flags = np.stack([flags[chunk].any(axis=0) for chunk in windows])
    return {
        "daily": coverage_report(actual, lower, upper, flags),
        "weekly": coverage_report(weekly_actual, week_lower, week_upper, weekly_flags, weekly=True),
    }
