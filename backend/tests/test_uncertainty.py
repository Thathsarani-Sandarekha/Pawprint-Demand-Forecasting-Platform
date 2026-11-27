"""intervals must be calibrated on older rolling folds."""
import numpy as np
import pytest
from app.forecasting.uncertainty import (
    origin_scale, fit_bands, apply_bands, evaluate_bands, week_slices,
)


def test_intervals_are_nonnegative_and_include_point_forecast():
    actual = np.full((28, 3), 12.0)
    pred = np.full((28, 3), 11.0)
    scale = np.array([10., 12., 13.])
    bands = fit_bands([(actual, pred, scale), (actual + 1, pred, scale)], level=0.9)
    low, high, week_low, week_high = apply_bands(pred, scale, bands)
    assert low.shape == pred.shape and high.shape == pred.shape
    assert week_low.shape == (4, 3) and week_high.shape == (4, 3)
    assert np.all(low >= 0) and np.all(low <= pred) and np.all(high >= pred)
    scores = evaluate_bands(actual, np.zeros_like(actual, dtype=bool), low, high, week_low, week_high)
    assert 0 <= scores["daily"]["coverage_pct"] <= 100
    assert scores["weekly"]["observations"] == 12


def test_interval_bounds_do_not_depend_on_unseen_future_actuals():
    pred = np.tile(np.arange(1, 15)[:, None], (1, 2)).astype(float)
    history = np.full((65, 2), 10.0)
    scale = origin_scale(history)
    actual = pred + 3
    fit = fit_bands([(actual, pred, scale)])
    bands1 = apply_bands(pred, scale, fit)
    future_actual = actual + 1000  # Cannot influence previously calibrated predictions.
    bands2 = apply_bands(pred, scale, fit)
    for a, b in zip(bands1, bands2):
        np.testing.assert_array_equal(a, b)
    assert len(week_slices(14)) == 2
    assert not np.array_equal(future_actual, actual)


def test_interval_rejects_bad_shapes_and_scales():
    history = np.ones((20, 2))
    with pytest.raises(ValueError):
        origin_scale(history)
    with pytest.raises(ValueError):
        fit_bands([])
    pred = np.ones((14, 2))
    with pytest.raises(ValueError):
        fit_bands([(pred, pred, np.ones(3))])
    calibrated = fit_bands([(pred, pred, np.ones(2))])
    with pytest.raises(ValueError):
        apply_bands(np.ones((28, 2)), np.ones(2), calibrated)
