"""Gradient-boosted trees, one model per quantile: the strong tabular baseline before any neural network."""
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

QUANTILES = (0.1, 0.5, 0.9)


def fit(X, y, seed=0, **kw):
    """Fit one HistGradientBoostingRegressor per quantile (in log1p-flow space). Returns {quantile: model}."""
    params = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0,
                  random_state=seed)
    params.update(kw)
    return {q: HistGradientBoostingRegressor(loss="quantile", quantile=q, **params).fit(X, y) for q in QUANTILES}


def predict(models, X):
    """(n, 3) array of the 10 %, 50 % and 90 % predictions, sorted so the band never crosses itself."""
    return np.sort(np.column_stack([models[q].predict(X) for q in QUANTILES]), axis=1)
