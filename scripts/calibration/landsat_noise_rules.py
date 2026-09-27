"""Noise of the Landsat season vegetation shares under different comparison rules (single season, quality
filters, 3-season medians), parsed from the output of landsat_season_noise.py. Offline.

    python scripts/calibration/landsat_noise_rules.py [results/landsat_season_noise.txt]
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LOG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "results" / "landsat_season_noise.txt"
text = LOG.read_text(encoding="utf-8")
blocks = re.split(r"=== (.+?) 250 m", text)[1:]
row = re.compile(r"^\s*\d+\s+(\d{4})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+(\w+)",
                 re.M)


def pairs(s, gap):
    """|s[y + gap] - s[y]| for every y where both exist."""
    return pd.Series({y: abs(s[y + gap] - s[y]) for y in s.index if y + gap in s.index}, dtype=float).dropna()


def med3(s, y):
    v = [s[k] for k in (y - 1, y, y + 1) if k in s.index and np.isfinite(s[k])]
    return np.median(v) if len(v) >= 2 else np.nan


for name, body in zip(blocks[::2], blocks[1::2]):
    df = pd.DataFrame([m.groups() for m in row.finditer(body)],
                      columns="year water veg nongreen scenes peak slc cov quality".split())
    df = df.astype({"year": int, "veg": float, "scenes": int, "peak": int, "slc": int}).set_index("year")
    print(f"\n=== {name}: {len(df)} seasons")
    rules = {
        "single, all": df["veg"],
        "single, not poor": df["veg"].where(df["quality"] != "poor"),
        "single, >=2 peak": df["veg"].where(df["peak"] >= 2),
        "single, good": df["veg"].where(df["quality"] == "good"),
    }
    for label, s in rules.items():
        s = s.dropna()
        for gap in (1, 3):
            d = pairs(s, gap)
            print(f"{label:22s} gap {gap}: n={len(d):2d} median {d.median():5.1f}  p90 {d.quantile(.9):5.1f}  "
                  f"max {d.max():5.1f}")
    for label, base in (("med3, not poor", df["veg"].where(df["quality"] != "poor")),
                        ("med3, >=2 peak", df["veg"].where(df["peak"] >= 2))):
        m3 = pd.Series({y: med3(base, y) for y in df.index}).dropna()
        for gap in (3, 5):
            d = pairs(m3, gap)
            print(f"{label:22s} gap {gap}: n={len(d):2d} median {d.median():5.1f}  p90 {d.quantile(.9):5.1f}  "
                  f"max {d.max():5.1f}")
    print("seasons with <2 peak scenes:", ", ".join(f"{y}({v:.0f}%,{q})" for y, v, q, p in
                                                   zip(df.index, df.veg, df.quality, df.peak) if p < 2))
