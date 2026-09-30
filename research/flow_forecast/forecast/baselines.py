"""Baselines every model must beat before it is worth any attention. All work in log1p(flow) space, like the
features, and take the feature table `X` built by `features.build`."""
import numpy as np
import pandas as pd


def persistence(X, horizon=None):
    """Tomorrow (and every later day) equals today."""
    return X["logq_lag0"].to_numpy()


def _doy(X):
    return (np.round(np.arctan2(X["doy_sin"], X["doy_cos"]) / (2 * np.pi) * 365.25) % 365).astype(int).to_numpy()


def climatology(train_X, train_y, X):
    """Median target of the training days that fall in the same day-of-year window (+-7 days)."""
    d, y = _doy(train_X), train_y.to_numpy()
    med = np.array([np.median(y[np.isin(d, [(k + j) % 365 for j in range(-7, 8)])]) for k in range(365)])
    return med[_doy(X)]


def recession(X, horizon, k_per_day=0.97):
    """Dry-weather recession: flow decays by a constant factor each day from today's value. Only valid when no
    rain arrives, so it is a floor on the skill a dry-season forecast must show, not a fair all-weather baseline."""
    q = np.expm1(X["logq_lag0"].to_numpy())
    return np.log1p(q * k_per_day ** horizon)
