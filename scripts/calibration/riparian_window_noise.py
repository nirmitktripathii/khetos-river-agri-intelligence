"""Noise floor of the year-on-year riparian vegetated share from matched 90-day Sentinel-2 windows.

For each reach and season, composite three consecutive years and difference neighbouring years, with a minimum
clear-look count of 1, 2 or 3 in both windows. The result sets RIPARIAN_THRESHOLD_PP and the Jan-Mar-only rule
in river.py. Needs network access; about 25 minutes.

    python scripts/calibration/riparian_window_noise.py
"""
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("rasterio").setLevel(logging.ERROR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import shape  # noqa: E402

from src import eo, river  # noqa: E402
from src.config import NAKATIYA_REACHES  # noqa: E402

T = time.time()
SEASONS = {"rabi (Jan-Mar)": (3, 31), "pre-monsoon (Apr-Jun)": (6, 30), "monsoon (Jul-Sep)": (9, 27),
           "post-monsoon (Oct-Dec)": (12, 31)}
rows = []
for reach in ("Urban reach · Dohra Rd–Bisalpur Rd", "Upper reach · Bhojipura side"):
    point, radius_km = NAKATIYA_REACHES[reach]
    corridor = river.river_corridor_geometry(100, point, radius_km)
    bbox = shape(corridor).bounds
    grid = eo.make_grid(bbox, res=10, max_px=1536)
    aoi = eo.geometry_pixels(grid, corridor)
    for season, (m, d) in SEASONS.items():
        comps = {}
        for year in (2024, 2025, 2026):
            end = datetime(year, m, d, tzinfo=timezone.utc)
            if end > datetime.now(timezone.utc):
                continue
            try:
                comps[year] = eo.s2_window_greenest(bbox, grid, aoi, end - timedelta(days=90), end)
            except Exception as exc:
                print("FAILED", reach, season, year, exc, flush=True)
        for y in sorted(comps):
            if y - 1 not in comps:
                continue
            now, ref = comps[y], comps[y - 1]
            out = {"reach": reach.split(" ·")[0], "season": season, "pair": f"{y - 1}->{y}",
                   "looks": f"{len(ref['dates'])}/{len(now['dates'])}",
                   "med_obs": f"{np.median(ref['n_obs'][aoi]):.0f}/{np.median(now['n_obs'][aoi]):.0f}"}
            for k in (1, 2, 3):
                both = aoi & (now["n_obs"] >= k) & (ref["n_obs"] >= k)
                n = int(both.sum())
                if not n:
                    out[f"d{k}"], out[f"cmp{k}"] = float("nan"), 0.0
                    continue
                g_now = 100 * float(((now["ndvi_max"] >= 0.5) & both).sum()) / n
                g_ref = 100 * float(((ref["ndvi_max"] >= 0.5) & both).sum()) / n
                out[f"d{k}"] = round(g_now - g_ref, 1)
                out[f"cmp{k}"] = round(100 * n / int(aoi.sum()), 1)
                if k == 1:
                    out["veg"] = f"{g_ref:.1f}->{g_now:.1f}"
            rows.append(out)
            print(out, f"(t={time.time() - T:.0f}s)", flush=True)

pd.set_option("display.width", 220)
df = pd.DataFrame(rows)
print(df.to_string())
for k in (1, 2, 3):
    x = df[f"d{k}"].abs()
    print(f"min looks {k}: |d| median {x.median():.1f}, p90 {x.quantile(0.9):.1f}, max {x.max():.1f}, "
          f"compared median {df[f'cmp{k}'].median():.0f}%")
print(f"DONE {time.time() - T:.0f}s", flush=True)
sys.stdout.flush()
os._exit(0)  # skip interpreter teardown of the GDAL worker threads
