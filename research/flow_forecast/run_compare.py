"""Score the baselines and the gradient-boosted model on blocked test years, one row per horizon.

    python run_compare.py            # baselines + gradient boosting (scikit-learn only)
    python run_compare.py --nn       # also LSTM and transformer (needs PyTorch)

THE TARGET IS GEOGLOWS, NOT THE RIVER. Every score below says how well the model reproduces another model's
flows from their shared ERA5 inputs. It is a test of the pipeline and a bound on what GEOGLOWS-trained models can
show; it is not evidence about how well the real Nakatiya can be forecast.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forecast import baselines, data, features, gbm, metrics, splits  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
VAL_YEARS = range(2011, 2016)
TEST_YEARS = range(2016, 2026)  # 2026 is unfinished


def evaluate(segment="mouth", horizons=features.HORIZONS, use_nn=False):
    df = data.load(segment)
    rows = []
    for h in horizons:
        X, y = features.build(df, h)
        train, val, test = splits.blocked(X.index, VAL_YEARS, TEST_YEARS, gap_days=90)
        obs = np.expm1(y[test].to_numpy())
        sims = {
            "persistence": baselines.persistence(X[test]),
            "climatology": baselines.climatology(X[train], y[train], X[test]),
            "recession": baselines.recession(X[test], h),
        }
        models = gbm.fit(X[train], y[train])
        band = gbm.predict(models, X[test])
        sims["gradient boosting"] = band[:, 1]
        if use_nn:
            sims.update(_neural(df, h, X.index, train, val, test))
        for name, sim in sims.items():
            rows.append({"horizon_days": h, "model": name, **metrics.report(obs, np.expm1(sim))})
        rows.append({"horizon_days": h, "model": "gradient boosting 10-90% band",
                     "coverage_10_90": metrics.coverage(y[test], band[:, 0], band[:, 2])})
    return pd.DataFrame(rows)


def _neural(df, h, index, train, val, test):
    from forecast import nn
    Xw, yw, dates = nn.windows(df, h)
    pos = pd.Series(np.arange(len(dates)), index=dates)
    pick = lambda mask: pos.reindex(index[mask]).dropna().astype(int).to_numpy()  # noqa: E731
    tr, va, te = pick(train), pick(val), pick(test)
    out = {}
    for kind in ("lstm", "transformer"):
        model = nn.fit(nn.make_model(kind), Xw[tr], yw[tr], Xw[va], yw[va])
        out[kind] = _align(nn.predict(model, Xw[te])[:, 1], pos.index[te], index[test])
    return out


def _align(pred, dates, wanted):
    return pd.Series(pred, index=dates).reindex(wanted).ffill().bfill().to_numpy()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment", default="mouth", choices=list(data.SEGMENT_IDS))
    ap.add_argument("--nn", action="store_true")
    args = ap.parse_args()
    table = evaluate(args.segment, use_nn=args.nn)
    OUT.mkdir(exist_ok=True)
    table.to_csv(OUT / f"compare_{args.segment}.csv", index=False)
    with pd.option_context("display.width", 140, "display.float_format", "{:.3f}".format):
        print(table.to_string(index=False))
