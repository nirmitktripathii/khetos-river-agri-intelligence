"""Inter-annual noise of the Landsat rabi-season shares: every season 1990-2026 on the urban and upper reach
(250 m corridor). The spread between neighbouring seasons sets VEGETATION_THRESHOLD_PP and the use of
3-season medians in river.py. Needs network access; about 12 minutes.

    python scripts/calibration/landsat_season_noise.py > scripts/calibration/results/landsat_season_noise.txt
"""
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("rasterio").setLevel(logging.ERROR)

import pandas as pd  # noqa: E402

from src import river  # noqa: E402
from src.config import NAKATIYA_REACHES  # noqa: E402

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 60)

T = time.time()
for reach in ("Urban reach · Dohra Rd–Bisalpur Rd", "Upper reach · Bhojipura side"):
    point, radius_km = NAKATIYA_REACHES[reach]
    tl = river.river_timeline(point, radius_km, 250, years=range(1990, 2027))
    df = tl["landsat"]
    cols = ["year", "water_pct", "vegetation_pct", "nongreen_pct", "scenes", "peak_scenes", "slc_off_scenes",
            "coverage_pct", "quality", "platforms", "dates"]
    print(f"\n=== {reach} 250 m (t={time.time() - T:.0f}s)")
    print(df[cols].round(1).to_string())
    print("missing:", tl["missing"])
    s = df.set_index("year")["vegetation_pct"]
    d = s.diff().dropna()
    d = d[[y - 1 in s.index for y in d.index]]
    print(f"year-on-year vegetation change: median |d| {d.abs().median():.1f}, p90 {d.abs().quantile(0.9):.1f}, "
          f"max {d.abs().max():.1f} pp over {len(d)} neighbouring pairs")
    good = df[df["quality"] == "good"].set_index("year")["vegetation_pct"]
    dg = good.diff().dropna()
    dg = dg[[y - 1 in good.index for y in dg.index]]
    if len(dg):
        print(f"good-only pairs: median |d| {dg.abs().median():.1f}, p90 {dg.abs().quantile(0.9):.1f}, "
              f"max {dg.abs().max():.1f} pp over {len(dg)} pairs")
    r3 = s.rolling(3, center=True, min_periods=2).median()
    d3 = r3.diff().dropna()
    print(f"3-season rolling median: median |d| {d3.abs().median():.1f}, p90 {d3.abs().quantile(0.9):.1f}, "
          f"max {d3.abs().max():.1f}")
    print(tl["builtup"].set_index("year")["built_pct"].to_dict())
print(f"DONE {time.time() - T:.0f}s", flush=True)
sys.stdout.flush()
os._exit(0)  # skip interpreter teardown of the GDAL worker threads
