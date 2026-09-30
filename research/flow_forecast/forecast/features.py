"""Turn the daily table into a supervised problem: at forecast origin t, predict log(1 + flow) at t + h.

Every feature uses data up to and including day t only. Future rain is deliberately NOT a feature: it is not
known at the origin, and giving it to the model would make the skill look far better than any real forecast.
"""
import numpy as np
import pandas as pd

HORIZONS = (1, 3, 7, 14)
FLOW_LAGS = (0, 1, 2, 3, 7, 14, 30)
RAIN_WINDOWS = (1, 3, 7, 14, 30, 60, 90)


def log_flow(q):
    return np.log1p(q)


def build(df, horizon, extra=None):
    """Features `X` and target `y` (log1p flow at t + horizon), both indexed by the origin date t.

    `extra` is an optional table of further daily drivers (soil moisture, groundwater level, built-up fraction,
    ...) indexed by date; every column is used as it stands on day t, so it must already be lagged to what was
    knowable then."""
    lq = log_flow(df["flow"])
    X = pd.DataFrame(index=df.index)
    for lag in FLOW_LAGS:
        X[f"logq_lag{lag}"] = lq.shift(lag)
    X["logq_max7"] = lq.rolling(7).max()
    X["logq_min30"] = lq.rolling(30).min()
    X["logq_trend3"] = lq - lq.shift(3)
    for w in RAIN_WINDOWS:
        X[f"rain_sum{w}"] = df["rain_mm"].rolling(w).sum()
    X["days_since_rain10"] = _days_since(df["rain_mm"] >= 10)
    X["et0_sum30"] = df["et0_mm"].rolling(30).sum()
    doy = df.index.dayofyear.to_numpy()
    X["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    X["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    # Indian Meteorological Department season as a number, 0 winter ... 3 post-monsoon
    m = df.index.month.to_numpy()
    X["season"] = np.select([m <= 2, m <= 5, m <= 9], [0, 1, 2], 3)
    if extra is not None:
        X = X.join(extra.reindex(df.index))
    y = lq.shift(-horizon).rename("y")
    keep = X.notna().all(axis=1) & y.notna()
    return X[keep], y[keep]


def _days_since(flag):
    """Days since `flag` was last true (0 on such a day); the series starts at its own length if never true."""
    idx = np.arange(len(flag))
    last = pd.Series(np.where(flag.to_numpy(), idx, np.nan), index=flag.index).ffill()
    return (pd.Series(idx, index=flag.index) - last).fillna(len(flag)).clip(upper=365)
