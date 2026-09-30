"""Blocked time splits. Random splits leak: neighbouring days are nearly identical, so a model scored on days
beside its training days looks far better than it is. Whole years go to one side only, with a gap at each edge."""
import numpy as np
import pandas as pd


def _near(flag, gap_days):
    """True on days within `gap_days` (either side) of a day where `flag` is true."""
    f = pd.Series(np.asarray(flag, float))
    width = 2 * gap_days + 1
    return (f.rolling(width, center=True, min_periods=1).max() > 0).to_numpy()


def blocked(index, val_years, test_years, gap_days=30):
    """Boolean masks (train, val, test) over `index` (a daily, gap-free DatetimeIndex).

    Test is whole test years. Validation is whole validation years, minus `gap_days` beside the test years.
    Train is everything else, minus `gap_days` beside the validation and test years, so that no training target or
    rolling window touches a held-out day."""
    year = pd.DatetimeIndex(index).year.to_numpy()
    test = np.isin(year, list(test_years))
    val = np.isin(year, list(val_years)) & ~_near(test, gap_days)
    train = ~np.isin(year, list(val_years) + list(test_years)) & ~_near(test | val, gap_days)
    return train, val, test


def rolling_origin(years, first_test, step=5):
    """Yield (train_years, test_years) pairs that walk forward in time: train on everything before a block of
    `step` years, test on that block, then move on. Use for the final report: one split is one lucky draw."""
    years = sorted(years)
    for start in range(first_test, years[-1] + 1, step):
        test = [y for y in years if start <= y < start + step]
        train = [y for y in years if y < start]
        if test and train:
            yield train, test
