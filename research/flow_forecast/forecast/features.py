"""Turn the daily table into a supervised problem: at forecast origin t, predict log(1 + flow) at t + h.

Every feature uses data up to and including day t only. Future rain is deliberately NOT a feature: it is not
known at the origin, and giving it to the model would make the skill look far better than any real forecast.
"""
import numpy as np
import pandas as pd

HORIZONS = (1, 3, 7, 14)
FLOW_LAGS = (0, 1, 2, 3, 7, 14, 30)
RAIN_WINDOWS = (1, 3, 7, 14, 30, 60, 90)
WETNESS_HALF_LIVES = (3, 10, 30, 90)  # days
GROUPS = ("base", "wetness", "soil", "upstream", "future_rain")


def log_flow(q):
    return np.log1p(q)


def build(df, horizon, groups=("base",), extra=None):
    """Features `X` and target `y` (log1p flow at t + horizon), both indexed by the origin date t.

    `groups` picks feature families from GROUPS:
      base        flow lags, rain totals, days since heavy rain, evaporation, day of year, season
      wetness     rain weighted by how recent it is (exponential decay, half-lives WETNESS_HALF_LIVES)
      soil        ERA5 soil moisture of three layers today, and its change over 7 days (needs `sm_*` columns)
      upstream    log flow at the upstream Nakatiya points today and yesterday (needs `up_*` columns)
      future_rain THE RAIN THAT ACTUALLY FELL between t+1 and t+horizon. Not knowable at t: use only to measure the
                  best case a perfect rain forecast could reach, and never report it as forecast skill.

    `extra` is an optional table of further daily drivers indexed by date; every column is used as it stands on
    day t, so it must already be lagged to what was knowable then."""
    unknown = set(groups) - set(GROUPS)
    if unknown:
        raise ValueError(f"unknown feature groups {unknown}")
    lq = log_flow(df["flow"])
    X = pd.DataFrame(index=df.index)
    if "base" in groups:
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
    X["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)  # always kept: the climatology baseline reads them
    X["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    if "base" in groups:
        m = df.index.month.to_numpy()
        X["season"] = np.select([m <= 2, m <= 5, m <= 9], [0, 1, 2], 3)  # 0 winter ... 3 post-monsoon
    if "wetness" in groups:
        for hl in WETNESS_HALF_LIVES:
            X[f"wet_hl{hl}"] = df["rain_mm"].ewm(halflife=hl, adjust=False).mean()
        X["wet_minus_et0_hl30"] = (df["rain_mm"] - df["et0_mm"]).ewm(halflife=30, adjust=False).mean()
    if "soil" in groups:
        for c in [c for c in df.columns if c.startswith("sm_")]:
            X[c] = df[c]
            X[f"{c}_chg7"] = df[c] - df[c].shift(7)
    if "upstream" in groups:
        for c in [c for c in df.columns if c.startswith("up_")]:
            X[f"log{c}"] = log_flow(df[c])
            X[f"log{c}_lag1"] = log_flow(df[c]).shift(1)
    if "future_rain" in groups:
        future = df["rain_mm"][::-1].rolling(horizon).sum()[::-1].shift(-1)  # rain on t+1 .. t+horizon
        X["FUTURE_rain_sum"] = future
        X["FUTURE_rain_last3"] = df["rain_mm"][::-1].rolling(min(3, horizon)).sum()[::-1].shift(-(horizon - min(3, horizon) + 1))
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
