"""Neural forecasters on sliding windows of daily drivers: an LSTM (Kratzert et al. 2019) and a small transformer
encoder. Both read the last `window` days of [log1p flow, rain, et0, doy sin, doy cos] and emit the 10/50/90 %
quantiles of log1p flow at `horizon` days ahead, trained with the pinball loss.

Needs PyTorch (see pyproject.toml, `uv sync --extra nn`). STATUS: written but not yet run on this machine; run
`python run_compare.py --nn` after installing PyTorch and treat the first results as a check, not a finding.
On one catchment with ~85 years of daily modelled flow these networks have few independent events to learn from,
so expect them to match, not beat, the gradient-boosted trees. Kratzert's results come from pooling hundreds of
basins."""
import numpy as np

QUANTILES = (0.1, 0.5, 0.9)
CHANNELS = ("logq", "rain", "et0", "doy_sin", "doy_cos")


def windows(df, horizon, window=90):
    """(array [n, window, 5], target [n], origin dates): inputs are scaled with train-independent fixed constants
    (log flow, rain/20, et0/5) so no statistic of the test years leaks in."""
    lq = np.log1p(df["flow"].to_numpy())
    doy = df.index.dayofyear.to_numpy()
    feats = np.column_stack([lq, df["rain_mm"].to_numpy() / 20, df["et0_mm"].to_numpy() / 5,
                             np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)]).astype("float32")
    n = len(df) - window - horizon + 1
    idx = np.arange(window)[None, :] + np.arange(n)[:, None]
    X = feats[idx]
    y = lq[window - 1 + horizon: window - 1 + horizon + n].astype("float32")
    return X, y, df.index[window - 1: window - 1 + n]


def _torch():
    import torch
    from torch import nn
    return torch, nn


def make_model(kind="lstm", hidden=64, layers=1, dropout=0.1, window=90):
    torch, nn = _torch()

    class LSTM(nn.Module):
        def __init__(self):
            super().__init__()
            self.rnn = nn.LSTM(len(CHANNELS), hidden, layers, batch_first=True, dropout=dropout if layers > 1 else 0)
            self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(hidden, len(QUANTILES)))

        def forward(self, x):
            out, _ = self.rnn(x)
            return self.head(out[:, -1])

    class Transformer(nn.Module):
        def __init__(self):
            super().__init__()
            self.inp = nn.Linear(len(CHANNELS), hidden)
            self.pos = nn.Parameter(torch.zeros(1, window, hidden))
            layer = nn.TransformerEncoderLayer(hidden, nhead=4, dim_feedforward=2 * hidden, dropout=dropout,
                                               batch_first=True, norm_first=True)
            self.enc = nn.TransformerEncoder(layer, num_layers=max(layers, 2))
            self.head = nn.Linear(hidden, len(QUANTILES))

        def forward(self, x):
            h = self.enc(self.inp(x) + self.pos)
            return self.head(h[:, -1])

    return {"lstm": LSTM, "transformer": Transformer}[kind]()


def pinball_loss(pred, y):
    torch, _ = _torch()
    q = torch.tensor(QUANTILES, dtype=pred.dtype)
    d = y[:, None] - pred
    return torch.maximum(q * d, (q - 1) * d).mean()


def fit(model, X, y, Xval, yval, epochs=40, batch=256, lr=2e-3, patience=6, seed=0):
    """Train with Adam, keep the epoch with the lowest validation pinball loss, stop after `patience` epochs
    without improvement. Returns the model in eval mode."""
    torch, _ = _torch()
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    Xt, yt = torch.from_numpy(X), torch.from_numpy(y)
    Xv, yv = torch.from_numpy(Xval), torch.from_numpy(yval)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    best, best_state, bad = np.inf, None, 0
    for _ in range(epochs):
        model.train()
        for b in np.array_split(rng.permutation(len(Xt)), max(1, len(Xt) // batch)):
            opt.zero_grad()
            loss = pinball_loss(model(Xt[b]), yt[b])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            v = float(pinball_loss(model(Xv), yv))
        if v < best - 1e-5:
            best, bad = v, 0
            best_state = {k: t.clone() for k, t in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    return model.eval()


def predict(model, X):
    """(n, 3) sorted quantile predictions (log1p flow)."""
    torch, _ = _torch()
    with torch.no_grad():
        return np.sort(model(torch.from_numpy(X)).numpy(), axis=1)
