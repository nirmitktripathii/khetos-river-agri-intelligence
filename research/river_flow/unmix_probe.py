"""T08 test: can linear spectral unmixing of Sentinel-2 find Nakatiya water narrower than a 10 m pixel?

For each reach and chosen clear scene:
  - read 10 surface-reflectance bands on a 10 m UTM grid covering +-200 m of the OSM centreline;
  - endmembers from the same acquisition: water = the purest half of eroded open-water pixels (MNDWI > 0.2,
    NDWI > 0.1) on ~12 km of the Ramganga around the confluence. SCL is not used: it labels most of the turbid
    Ramganga "unclassified". When the Ramganga itself is too narrow for pure pixels (pre-monsoon), a library
    spectrum from two dates with a wide channel is used instead and flagged; vegetation = greenest 3 % of
    corridor pixels; bright and dark dry land = the brightest and darkest 20 % of clearly dry, low-NDVI
    corridor pixels;
  - fully constrained least squares (non-negative, sum-to-one) per pixel -> water fraction f;
  - profile of mean f against distance from the centreline (10 m bins, 0-200 m);
  - equivalent width = sum over pixels within 40 m of the line of (f - background f) * 100 m2 / reach length,
    background = pixels 100-200 m from the line. The sensor's blur spreads a narrow channel over neighbouring
    pixels and lowers the peak, but the sum across the channel survives it;
  - synthetic check: mix real Ramganga water pixels into real background pixels at known fractions and unmix.
Writes unmix_scenes.csv, unmix_profiles.csv, unmix_segments.csv, unmix_synthetic.csv to OUT (argv[1], default
research/river_flow/unmix, which holds the run of 30 September 2026 that the width table is built from).

It reads about 35 Sentinel-2 scenes over the network and takes 30-60 minutes; run one copy at a time. It needs
scipy, which the app itself does not import:

    python research/river_flow/unmix_probe.py [output folder]
"""
import os
import sys
import threading
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np
import pandas as pd
import shapely
from rasterio.enums import Resampling
from scipy import ndimage
from scipy.optimize import nnls
from shapely.geometry import Point, box, shape
from shapely.ops import nearest_points
from shapely.ops import transform as shp_transform

from src import eo, river
from src.config import NAKATIYA_REACHES
from src.stac import search_s2

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "unmix"
OUT.mkdir(parents=True, exist_ok=True)
_LOG = open(OUT / "progress.log", "a", encoding="utf-8", buffering=1)

# Slow ranges were being cut off by the 30 s total timeout and the truncated bytes cached, so a retry re-read
# the bad bytes (see the s2-read-failures note). For this run: a longer timeout and no region cache for https.
eo.GDAL_ENV.update({"GDAL_HTTP_TIMEOUT": "180", "CPL_VSIL_CURL_NON_CACHED": "/vsicurl/https"})

BANDS = ("B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12")
IX = {b: i for i, b in enumerate(BANDS)}
SHORT = {"Urban reach · Dohra Rd–Bisalpur Rd": "urban", "Upper reach · Bhojipura side": "upper",
         "Lower reach · Ramganga confluence": "lower"}
WATER_BOX = (79.40, 28.08, 79.52, 28.20)  # ~12 km of the Ramganga around the Nakatiya confluence
LIBRARY_DAYS = (date(2025, 2, 24), date(2018, 10, 19))  # wide, clear Ramganga channel on both
MIN_WATER_PX = 100
VALIDATION = {"lower": [date(2025, 2, 24)], "urban": [date(2024, 6, 9)],
              "upper": [date(2024, 4, 20), date(2022, 11, 27)]}  # clear S2 scenes near dated Esri basemap captures
YEARS = (2018, 2020, 2022, 2024, 2025)
HALF = 200
NEAR = 40
BG = (100, 200)
EM_NAMES = ("water", "vegetation", "bright_land", "dark_land")


def say(*a):
    msg = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(msg, flush=True)
    _LOG.write(msg + "\n")


def retry(fn, tries=4, wait=5):
    def run(*a):
        for k in range(tries):
            try:
                return fn(*a)
            except Exception as exc:
                if k == tries - 1:
                    raise
                say("  retry", getattr(fn, "__name__", "?"), a[0] if a else "", type(exc).__name__, str(exc)[:120])
                time.sleep(wait * (k + 1))
    run.__name__ = getattr(fn, "__name__", "run")
    return run


def nd(a, b):
    return eo.normalized_difference(a, b)


def read_scene(item, grid):
    off = eo.s2_boa_offset(item)

    def one(b):
        rs = Resampling.nearest if b == "SCL" else Resampling.bilinear
        a = eo.read_on_grid(item.assets[b].href, grid, rs)
        return b, (a if b == "SCL" else eo.s2_reflectance(a, off))

    one.__name__ = "band"
    got = dict(eo._pmap(retry(one), BANDS + ("SCL",)))
    X = np.stack([got[b] for b in BANDS], axis=-1)
    valid = eo.scl_clear(got["SCL"]) & np.isfinite(X).all(axis=-1)
    return X, got["SCL"], valid


def find_item(bbox, day, scene_id=None):
    items = search_s2(bbox, eo._utc(day.year, day.month, day.day),
                      eo._utc(day.year, day.month, day.day) + timedelta(days=1), max_tile_cloud=100, max_items=20)
    if scene_id:
        items = [i for i in items if i.id == scene_id] or items
    inside = [i for i in items if shape(i.geometry).contains(box(*bbox))]
    return (inside or items or [None])[0]


_WATER = {}
_WATER_LOCK = threading.Lock()
_LIBRARY = {}


def _same_day_water(day):
    item = find_item(WATER_BOX, day)
    grid = eo.make_grid(WATER_BOX, res=10, max_px=2048)
    X, scl, valid = read_scene(item, grid)
    g, s, n = X[..., IX["B03"]], X[..., IX["B11"]], X[..., IX["B08"]]
    mndwi = nd(g, s)
    w = valid & (mndwi > 0.2) & (nd(g, n) > 0.1)
    w = ndimage.binary_erosion(w)  # drop bank-edge (mixed) pixels
    if w.sum():
        w &= mndwi >= np.median(mndwi[w])  # purest half
    return X[w], item.id


def water_pixels(day):
    """Pure open-water spectra from the Ramganga on the same day, else the two-date library."""
    with _WATER_LOCK:
        if day not in _WATER:
            px, sid = _same_day_water(day)
            src = "same day"
            if len(px) < MIN_WATER_PX:
                say("  water endmember", day, "only", len(px), "pure px -> library")
                px, sid, src = _LIBRARY["px"], _LIBRARY["id"], "library"
            _WATER[day] = (px, sid, src)
            say("  water endmember", day, sid, src, "px", len(px))
        return _WATER[day]


def build_library():
    parts, ids = [], []
    for d in LIBRARY_DAYS:
        px, sid = _same_day_water(d)
        say("  library", d, sid, "pure px", len(px))
        _WATER[d] = (px, sid, "same day")
        parts.append(px[np.random.default_rng(1).integers(0, len(px), min(len(px), 3000))])
        ids.append(sid)
    _LIBRARY.update(px=np.concatenate(parts), id="library:" + "+".join(ids))


def fcls(X, E, delta=10.0):
    """Fully constrained least squares: fractions >= 0 that sum to 1 (sum-to-one row weighted by delta)."""
    A = np.vstack([E.T, np.full(E.shape[0], delta)])
    F = np.empty((len(X), len(E)), "float32")
    R = np.empty(len(X), "float32")
    for i, x in enumerate(X):
        F[i], R[i] = nnls(A, np.append(x, delta))
    return F, R


def endmembers(X, valid, water):
    b = lambda k: X[..., IX[k]]
    ndvi, mndwi, ndwi = nd(b("B08"), b("B04")), nd(b("B03"), b("B11")), nd(b("B03"), b("B08"))
    bright = X.mean(axis=-1)
    veg = valid & (ndvi >= max(0.45, np.nanpercentile(ndvi[valid], 97)))
    land = valid & (ndvi < 0.25) & (mndwi < -0.15) & (ndwi < -0.1)
    if land.sum() < 50:  # lush post-monsoon scenes: few bare or built pixels, so relax the NDVI cut
        land = valid & (ndvi < 0.35) & (mndwi < -0.15) & (ndwi < -0.1)
    lb = bright[land]
    hi = land & (bright >= np.percentile(lb, 80))
    lo = land & (bright <= np.percentile(lb, 20))
    E = np.stack([np.median(water, axis=0), np.median(X[veg], axis=0), np.median(X[hi], axis=0),
                  np.median(X[lo], axis=0)])
    return E, {"n_veg_px": int(veg.sum()), "n_land_px": int(land.sum())}


def reach_geometry(pt, r):
    g = shp_transform(river._TO_UTM, shape(river.nakatiya_geometry()))
    c = shp_transform(river._TO_UTM, Point(pt))
    reach = g.intersection(c.buffer(r * 1000))
    if reach.is_empty:
        c = nearest_points(c, g)[1]
        reach = g.intersection(c.buffer(r * 1000))
    return g, reach


def pixel_centres(grid):
    left, bottom, right, top = grid.bounds
    cols = left + (np.arange(grid.width) + 0.5) * grid.res
    rows = top - (np.arange(grid.height) + 0.5) * grid.res
    return np.meshgrid(cols, rows)


def run_reach(name, pt, r, scenes):
    tag = SHORT[name]
    line, reach = reach_geometry(pt, r)
    L = reach.length
    corridor = river.river_corridor_geometry(HALF, pt, r)
    bounds = shape(corridor).bounds
    grid = eo.make_grid(bounds, res=10, max_px=2048)
    inside = eo.geometry_pixels(grid, corridor)
    xs, ys = pixel_centres(grid)
    d = np.full(grid.shape, np.inf, "float32")
    d[inside] = shapely.distance(line, shapely.points(xs[inside], ys[inside]))
    seg = (np.arange(grid.height)[:, None] * grid.res // 1000).repeat(grid.width, axis=1)  # ~1 km row blocks
    say(tag, "grid", grid.shape, "reach km", round(L / 1000, 2), "corridor px", int(inside.sum()))
    rows, prof_rows, seg_rows, syn_rows = [], [], [], []
    for day, scene_id in scenes:
        try:
            item = find_item(bounds, day, scene_id)
            X, scl, valid = read_scene(item, grid)
            valid &= inside
            water, water_id, water_src = water_pixels(day)
            E, em_info = endmembers(X, valid, water)
            F, R = fcls(X[valid], E)
            f = np.full(grid.shape, np.nan, "float32")
            f[valid] = F[:, 0]
            res = np.full(grid.shape, np.nan, "float32")
            res[valid] = R
            bgm = valid & (d >= BG[0]) & (d < BG[1])
            near = inside & (d < NEAR)
            nearv = valid & (d < NEAR)
            f_bg, sd_bg = float(np.nanmean(f[bgm])), float(np.nanstd(f[bgm]))
            share = nearv.sum() / max(near.sum(), 1)
            weq = float(np.nansum(f[nearv] - f_bg) * 100 / (L * share))
            b = lambda k: X[..., IX[k]]
            mndwi, ndwi, ndvi = nd(b("B03"), b("B11")), nd(b("B03"), b("B08")), nd(b("B08"), b("B04"))
            conf_w = float(((mndwi > 0) & (ndwi > 0) & nearv).sum() * 100 / (L * share))
            thr = float(np.nanpercentile(f[bgm], 99))
            excess = int((nearv & (f > thr)).sum() - 0.01 * nearv.sum())
            rows.append({"reach": tag, "date": day.isoformat(), "scene": item.id, "water_scene": water_id,
                         "water_src": water_src, "n_water_px": len(water), **em_info, "valid_near_share": round(float(share), 3),
                         "f_bg_mean": round(f_bg, 4), "f_bg_sd": round(sd_bg, 4), "f_bg_p99": round(thr, 4),
                         "f_near_mean": round(float(np.nanmean(f[nearv])), 4),
                         "weq_m": round(weq, 2), "confirmed_index_width_m": round(conf_w, 2),
                         "excess_px_above_bg_p99": excess, "rmse_median": round(float(np.nanmedian(res[valid])), 4),
                         **{f"em_{n}_{bd}": round(float(E[i, IX[bd]]), 4) for i, n in enumerate(EM_NAMES)
                            for bd in ("B03", "B04", "B08", "B11")}})
            for lo in range(0, HALF, 10):
                m = valid & (d >= lo) & (d < lo + 10)
                prof_rows.append({"reach": tag, "date": day.isoformat(), "d_lo": lo, "n": int(m.sum()),
                                  "f_water": float(np.nanmean(f[m])), "mndwi": float(np.nanmean(mndwi[m])),
                                  "ndwi": float(np.nanmean(ndwi[m])), "ndvi": float(np.nanmean(ndvi[m])),
                                  "b11": float(np.nanmean(b("B11")[m]))})
            for s in np.unique(seg[inside]):
                ms_near, ms_bg = nearv & (seg == s), bgm & (seg == s)
                if ms_near.sum() < 20 or ms_bg.sum() < 20:
                    continue
                len_s = reach.intersection(box(grid.bounds[0], grid.bounds[3] - (s + 1) * 1000,
                                               grid.bounds[2], grid.bounds[3] - s * 1000)).length
                if len_s < 300:
                    continue
                share_s = ms_near.sum() / max((near & (seg == s)).sum(), 1)
                seg_rows.append({"reach": tag, "date": day.isoformat(), "seg_km": int(s),
                                 "weq_m": float(np.nansum(f[ms_near] - np.nanmean(f[ms_bg])) * 100 / (len_s * share_s))})
            rng = np.random.default_rng(0)
            bgX = X[bgm]
            pick_bg = bgX[rng.integers(0, len(bgX), 1500)]
            pick_w = water[rng.integers(0, len(water), 1500)]
            for fr in (0.0, 0.05, 0.1, 0.2, 0.3, 0.5):
                est = fcls(fr * pick_w + (1 - fr) * pick_bg, E)[0][:, 0]
                syn_rows.append({"reach": tag, "date": day.isoformat(), "true_f": fr, "mean": float(est.mean()),
                                 "p05": float(np.percentile(est, 5)), "p50": float(np.median(est)),
                                 "p95": float(np.percentile(est, 95))})
            say(tag, day, "weq", round(weq, 2), "m | confirmed-index", round(conf_w, 2), "m | f_bg",
                round(f_bg, 3), "+-", round(sd_bg, 3), "| excess px", excess)
        except Exception:
            say(tag, day, "FAILED", traceback.format_exc()[-600:])
    return rows, prof_rows, seg_rows, syn_rows


def pick_scenes():
    d = pd.read_csv(HERE / "inputs" / "s2_clear_scenes.csv", parse_dates=["date"])
    d["m"] = d.date.dt.month
    out = {}
    for reach, g in d.groupby("reach"):
        chosen = []
        for y in YEARS:
            gy = g[g.date.dt.year == y]
            for months in ((10, 11), (5, 4)):
                c = gy[gy.m.isin(months)].copy()
                if c.empty:
                    continue
                c["rank"] = c.m.map({m: i for i, m in enumerate(months)})
                best = c.sort_values(["rank", "clear_pct"], ascending=[True, False]).iloc[0]
                chosen.append((best.date.date(), best.scene))
        for v in VALIDATION.get(reach, []):
            if v not in [c[0] for c in chosen]:
                chosen.append((v, None))
        out[reach] = sorted(chosen)
    return out


def main():
    scenes = pick_scenes()
    say("scenes", {k: len(v) for k, v in scenes.items()})
    build_library()
    futures = eo.run_jobs(*[(run_reach, n, pt, r, scenes[SHORT[n]]) for n, (pt, r) in NAKATIYA_REACHES.items()])
    acc = [[], [], [], []]
    for fut in futures:
        try:
            for a, part in zip(acc, fut.result()):
                a += part
        except Exception:
            say("reach failed", traceback.format_exc())
    for a, fname in zip(acc, ("unmix_scenes.csv", "unmix_profiles.csv", "unmix_segments.csv", "unmix_synthetic.csv")):
        pd.DataFrame(a).to_csv(OUT / fname, index=False)
    say("ALL DONE")


if __name__ == "__main__":
    try:
        eo.run_analysis(main)
    except Exception:
        say("main failed", traceback.format_exc())
    finally:
        _LOG.flush()
        os._exit(0)
