"""Landsat index rules against Impact Observatory 10 m land cover on the same 30 m grid: per-class index
percentiles, and the precision / recall of candidate built-up rules.

Finding recorded in eo.composite_shares: "never green" (greenest NDVI < 0.40) is precise for built-up land
(0.83-0.95 on the urban reach) but misses tree-shaded settlement (recall 0.48-0.60 urban, 0.01-0.20 rural), so built-up change comes from WSF Evolution
and Impact Observatory instead of Landsat. Needs network access; a few minutes per corridor.

    python scripts/calibration/landsat_vs_landcover.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
from shapely.geometry import shape  # noqa: E402

from src import eo, river  # noqa: E402
from src.config import NAKATIYA_REACHES  # noqa: E402

CLASSES = {1: "water", 2: "trees", 5: "crops", 7: "built", 8: "bare", 11: "range"}


def run(name, point, radius_km, buffer_m, year=2023, lulc_year=2023):
    t = time.time()
    corridor = river.river_corridor_geometry(buffer_m, point, radius_km)
    bounds = shape(corridor).bounds
    grid = eo.make_grid(bounds, res=30, max_px=1024, origin=(15.0, 15.0))
    inside = eo.geometry_pixels(grid, corridor)
    items = sorted(eo.landsat_candidates(bounds, year), key=lambda i: i.datetime)
    print(f"\n== {name}, {buffer_m} m corridor, rabi {year}: {len(items)} candidate scenes")

    def one(item):
        clear = inside & eo.landsat_clear(eo.read_on_grid(item.assets["qa_pixel"].href, grid))
        if clear.sum() / inside.sum() < 0.3:
            return None
        platform = item.properties.get("platform")
        b = {a: eo.landsat_reflectance(eo.read_on_grid(item.assets[a].href, grid), platform, a)
             for a in ("green", "red", "nir08", "swir16")}
        idx = {"mndwi": eo.normalized_difference(b["green"], b["swir16"]),
               "ndvi": eo.normalized_difference(b["nir08"], b["red"]),
               "ndbi": eo.normalized_difference(b["swir16"], b["nir08"])}
        for v in idx.values():
            v[~clear] = np.nan
        return item.datetime.strftime("%Y-%m-%d"), idx

    scenes = [s for s in eo._pmap(eo._safe(one), items) if s]  # an unreadable scene is skipped, as in the app
    print("clear scenes:", ", ".join(s[0] for s in scenes))
    if not scenes:
        return
    stack = {k: np.stack([s[1][k] for s in scenes]) for k in ("mndwi", "ndvi", "ndbi")}
    ndvi_max, ndvi_med = eo._nanstat(np.nanmax, stack["ndvi"]), eo._nanstat(np.nanmedian, stack["ndvi"])
    ndbi_med, mndwi_med = eo._nanstat(np.nanmedian, stack["ndbi"]), eo._nanstat(np.nanmedian, stack["mndwi"])

    # Nearest-neighbour sample of the 10 m classes on the 30 m grid (one of nine pixels): adequate for shares.
    lulc = np.asarray(eo.lulc_on_grid(grid, lulc_year))
    ok = inside & np.isfinite(ndvi_max) & np.isfinite(lulc) & (lulc > 0)
    n = int(ok.sum())
    print(f"pixels {n}; IO {lulc_year} shares:",
          {v: round(100 * ((lulc == k) & ok).sum() / n, 1) for k, v in CLASSES.items()})
    for k, v in CLASSES.items():
        m = ok & (lulc == k)
        if m.sum() >= 10:
            q = lambda a: np.round(np.nanpercentile(a[m], [10, 50, 90]), 2).tolist()  # noqa: E731
            print(f"  {v:6s} n={m.sum():5d} greenest NDVI p10/50/90 {q(ndvi_max)}  median NDVI {q(ndvi_med)}  "
                  f"median NDBI {q(ndbi_med)}  median MNDWI {q(mndwi_med)}")

    built = ok & (lulc == 7)
    rules = {
        "NDBI > 0.1 & median NDVI < 0.35": (ndbi_med > 0.10) & (ndvi_med < 0.35),
        "NDBI > 0 & median NDVI < 0.3": (ndbi_med > 0.0) & (ndvi_med < 0.3),
        "greenest NDVI < 0.30": ndvi_max < 0.30,
        "greenest NDVI < 0.35": ndvi_max < 0.35,
        "greenest NDVI < 0.40": ndvi_max < 0.40,
        "greenest NDVI < 0.45": ndvi_max < 0.45,
        "greenest NDVI < 0.40 & MNDWI < 0": (ndvi_max < 0.40) & (mndwi_med < 0),
    }
    print(f"  IO built share {100 * built.sum() / n:.1f}%")
    for rule, r in rules.items():
        r = r & ok
        tp = (r & built).sum()
        print(f"  {rule:36s} share {100 * r.sum() / n:5.1f}%  precision {tp / max(r.sum(), 1):.2f}  "
              f"recall {tp / max(built.sum(), 1):.2f}")
    veg_io = ok & np.isin(lulc, (2, 5))
    for rule, r in {"median NDVI > 0.40": ndvi_med > 0.40, "greenest NDVI > 0.50": ndvi_max > 0.5,
                    "greenest NDVI > 0.45": ndvi_max > 0.45}.items():
        print(f"  vegetated if {rule:22s} share {100 * (r & ok).sum() / n:5.1f}% "
              f"vs IO trees + crops {100 * veg_io.sum() / n:.1f}%")
    print(f"  [{time.time() - t:.0f}s]", flush=True)


if __name__ == "__main__":
    urban = NAKATIYA_REACHES["Urban reach · Dohra Rd–Bisalpur Rd"][0]
    run("urban", urban, 4, 250)
    run("lower", NAKATIYA_REACHES["Lower reach · Ramganga confluence"][0], 6, 250)
    run("upper", NAKATIYA_REACHES["Upper reach · Bhojipura side"][0], 6, 250)
    run("urban", urban, 4, 250, year=2017, lulc_year=2017)
    sys.stdout.flush()
    os._exit(0)  # skip interpreter teardown of the GDAL worker threads
