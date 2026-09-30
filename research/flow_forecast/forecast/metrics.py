"""Hydrology scores. All take flows in m³/s (not logs) unless the name says otherwise."""
import numpy as np


def nse(obs, sim):
    """Nash–Sutcliffe efficiency: 1 is perfect, 0 is no better than always guessing the mean, below 0 is worse.
    Dominated by the biggest floods."""
    obs, sim = np.asarray(obs, float), np.asarray(sim, float)
    return 1 - np.sum((obs - sim) ** 2) / np.sum((obs - obs.mean()) ** 2)


def log_nse(obs, sim, eps=0.01):
    """NSE on log flows: weights the dry season as much as the floods (eps avoids log of zero)."""
    return nse(np.log(np.asarray(obs, float) + eps), np.log(np.asarray(sim, float) + eps))


def kge(obs, sim):
    """Kling–Gupta efficiency (2009): combines correlation, variability ratio and bias ratio; 1 is perfect."""
    obs, sim = np.asarray(obs, float), np.asarray(sim, float)
    r = np.corrcoef(obs, sim)[0, 1]
    alpha = sim.std() / obs.std()
    beta = sim.mean() / obs.mean()
    return 1 - np.sqrt((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2)


def pbias(obs, sim):
    """Percent bias: positive means the simulation has too much water."""
    obs, sim = np.asarray(obs, float), np.asarray(sim, float)
    return 100 * np.sum(sim - obs) / np.sum(obs)


def pinball(obs, pred, q):
    """Pinball (quantile) loss of quantile `q`; lower is better."""
    d = np.asarray(obs, float) - np.asarray(pred, float)
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def coverage(obs, low, high):
    """Share of observations inside [low, high]. For a 10–90 % band it should be near 0.80."""
    obs = np.asarray(obs, float)
    return float(np.mean((obs >= np.asarray(low)) & (obs <= np.asarray(high))))


def report(obs, sim):
    """{"NSE", "logNSE", "KGE", "PBIAS_%"} for one simulation."""
    return {"NSE": nse(obs, sim), "logNSE": log_nse(obs, sim), "KGE": kge(obs, sim), "PBIAS_%": pbias(obs, sim)}
