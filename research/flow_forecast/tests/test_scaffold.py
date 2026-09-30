import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecast import features, metrics, splits  # noqa: E402


def _frame(n=1500, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2000-01-01", periods=n, freq="D")
    rain = rng.gamma(0.3, 8, n)
    return pd.DataFrame({"flow": np.convolve(rain, np.ones(10) / 10, "same") + 0.5, "rain_mm": rain,
                         "et0_mm": 4 + np.sin(np.arange(n) / 58)}, index=idx)


def test_metrics_known_values():
    obs = np.array([1.0, 2.0, 3.0, 4.0])
    assert metrics.nse(obs, obs) == 1 and metrics.kge(obs, obs) == pytest.approx(1)
    assert metrics.nse(obs, np.full(4, obs.mean())) == pytest.approx(0)
    assert metrics.pbias(obs, obs * 1.1) == pytest.approx(10)
    assert metrics.pinball([1.0], [0.0], 0.9) == pytest.approx(0.9)
    assert metrics.coverage([1, 5], [0, 0], [2, 2]) == 0.5


def test_splits_do_not_overlap_and_keep_a_gap():
    idx = pd.date_range("1990-01-01", "2025-12-31", freq="D")
    train, val, test = splits.blocked(idx, range(2011, 2016), range(2016, 2026), gap_days=90)
    assert not (train & val).any() and not (train & test).any() and not (val & test).any()
    assert idx[train].max() < pd.Timestamp("2011-01-01") - pd.Timedelta(days=89)
    assert idx[val].min() >= pd.Timestamp("2011-01-01") and idx[val].max() < pd.Timestamp("2015-12-31") - pd.Timedelta(days=89)
    assert idx[test].min() == pd.Timestamp("2016-01-01")


def test_rolling_origin_only_trains_on_the_past():
    for train, test in splits.rolling_origin(range(1960, 2026), 1990, 10):
        assert max(train) < min(test)


def test_features_use_only_the_past():
    df = _frame()
    X, y = features.build(df, horizon=7)
    t = X.index[700]
    later = df.copy()
    later.loc[later.index > t, ["flow", "rain_mm", "et0_mm"]] *= 50  # change everything after the origin
    X2, y2 = features.build(later, horizon=7)
    pd.testing.assert_series_equal(X.loc[t], X2.loc[t])  # features at t cannot see it
    assert y.loc[t] != y2.loc[t]  # the target, 7 days on, does
    assert y.loc[t] == pytest.approx(np.log1p(df["flow"].shift(-7).loc[t]))


def test_future_rain_is_the_rain_after_the_origin():
    df = _frame()
    X, _ = features.build(df, horizon=7, groups=("base", "future_rain"))
    t = X.index[500]
    after = df["rain_mm"].loc[t + pd.Timedelta(days=1): t + pd.Timedelta(days=7)]
    assert X.loc[t, "FUTURE_rain_sum"] == pytest.approx(after.sum())
    assert X.loc[t, "FUTURE_rain_last3"] == pytest.approx(after.iloc[-3:].sum())


def test_honest_groups_use_only_the_past():
    df = _frame()
    df["sm_0_7"] = 0.2 + df["rain_mm"].rolling(5, min_periods=1).mean() / 100
    df["up_above"] = df["flow"] * 0.4
    groups = ("base", "wetness", "soil", "upstream")
    X, _ = features.build(df, horizon=7, groups=groups)
    t = X.index[700]
    later = df.copy()
    later.loc[later.index > t] *= 50
    X2, _ = features.build(later, horizon=7, groups=groups)
    pd.testing.assert_series_equal(X.loc[t], X2.loc[t])


def test_forecast_rain_uses_lead_k_for_day_t_plus_k():
    import run_rain_forecast as rrf
    idx = pd.date_range("2024-03-01", periods=20, freq="D")
    fc = pd.DataFrame({f"lead{k}_mm": np.arange(20) * 10 + k for k in range(1, 8)}, index=idx, dtype=float)
    fut = rrf.forecast_future_rain(fc, 3)
    t = idx[5]
    # day t+1 from lead 1, t+2 from lead 2, t+3 from lead 3
    assert fut.loc[t, "FUTURE_rain_sum"] == (60 + 1) + (70 + 2) + (80 + 3)
    assert fut.loc[t, "FUTURE_rain_last3"] == fut.loc[t, "FUTURE_rain_sum"]
