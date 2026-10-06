"""Permanent vegetation in May, every year, inside the Nakatiya watershed (Landsat 5/7/8/9, 30 m).

May is the driest, hottest month before the monsoon. Wheat is harvested by then, so a pixel that is green in May
is either permanent vegetation (trees, groves, orchards, grass on wet ground) or a hot-season crop (mentha,
vegetables, young or ratoon sugarcane). To separate the two, a pixel counts as **permanent** only if it stands out
as green both in May and in the November before (10 Nov - 10 Dec: rice is cut and the new wheat has not come up).
Mentha and summer vegetables are absent in November; sugarcane, a 10-12 month crop, is green in both and so is
counted with the trees and groves.

"Stands out" is relative: NDVI at least 0.10 above the watershed's median NDVI in that window. A fixed threshold
(NDVI >= 0.40, kept as green_both_abs_pct) does not survive the change of sensor and haze: 0.7-2.1 % of the
watershed in the Landsat 5/7 years 2000-2011 against 2.1-24.8 % in the Landsat 8 years 2014-2021, because the
whole scene's NDVI moves (May median 0.22-0.25 with TM/ETM+, 0.27-0.34 with OLI, even after Roy et al. 2016
harmonisation). Haze and sensor shift the median with the trees, so the relative test moved far less (4.8-8.4 %
and 3.5-11.5 %) and matched WorldCover trees as well (precision 0.39-0.58). NDMI-based tests did no better.

Per year: the per-pixel median NDVI of the clear Landsat looks in each window (May; widened to 20 Apr - 10 Jun
when May has fewer than two usable looks; the November window widens to 1 Nov - 20 Dec likewise). Shares are
percentages of the watershed pixels seen clear in the window(s) concerned.

Independent check: tree cover in ESA WorldCover (10 m, Sentinel-1/2) for 2020 and 2021 (two map versions with
different algorithms: 7.7 % and 13.6 % trees here, so the difference is the map, not the land). About half of the
"permanent" pixels are WorldCover trees: 30 m Landsat pixels mix scattered trees with fields, and sugarcane is
counted here, not there. The Impact Observatory map KhetOS uses elsewhere puts trees at about 1 % here, so it is
not used as the check. A further test (also green in every clear Jan-Mar look) was dropped: one hazy February look
turns real trees "bare".

Writes data/nakatiya_may_vegetation.csv. Takes about an hour (40 years of Landsat reads).

    .venv/Scripts/python.exe -u research/nakatiya_observatory/may_vegetation.py [first_year] [last_year]
"""
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src import eo  # noqa: E402
from src.stac import search_items, search_landsat  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402

WATERSHED = ROOT / "data" / "nakatiya_watershed.geojson"
OUT = ROOT / "data" / "nakatiya_may_vegetation.csv"
GREEN = 0.40  # absolute test, kept for reference only
STANDS_OUT = 0.10  # NDVI above the watershed median in the same window
MIN_CLEAR = 0.3  # a look must see at least 30 % of the watershed clear
FIRST, LAST = 1985, datetime.now().year
WORLDCOVER_YEARS = (2020, 2021)  # ESA WorldCover 10 m, class 10 = tree cover


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


WINDOWS = {  # name: (narrow window, widened window), as (month, day) pairs; November belongs to the year before
    "may": (((5, 1), (6, 1)), ((4, 20), (6, 11))),
    "nov": (((11, 10), (12, 11)), ((11, 1), (12, 21))),
}


def window_ndvi(grid, inside, year, which):
    """(median NDVI array, list of (date, platform)) for one window; widened if the narrow one gives < 2 looks."""
    n = int(inside.sum())
    y = year - 1 if which == "nov" else year
    looks, items_seen = [], set()
    for (m0, d0), (m1, d1) in WINDOWS[which]:
        items = eo._one_per_day(search_landsat(grid.lonlat_bounds(), _utc(y, m0, d0), _utc(y, m1, d1),
                                               max_cloud=80, max_items=60), grid.lonlat_bounds())
        items = [i for i in items if i.id not in items_seen]
        items_seen |= {i.id for i in items}

        def screen(item):
            clear = inside & eo.landsat_clear(eo.read_on_grid(item.assets["qa_pixel"].href, grid))
            return (item, clear) if clear.sum() / n >= MIN_CLEAR else None

        usable = [u for u in eo._pmap(eo._safe(screen), items) if u]

        def ndvi(pair):
            item, clear = pair
            platform = item.properties.get("platform")
            red, nir = eo._pmap(lambda a: eo.landsat_reflectance(eo.read_on_grid(item.assets[a].href, grid),
                                                                 platform, a), ["red", "nir08"])
            v = eo.normalized_difference(nir, red)
            v[~clear] = np.nan
            return item, v

        looks += [r for r in eo._pmap(eo._safe(ndvi), usable, io=False) if r]
        if len(looks) >= 2:
            break
    if not looks:
        return None, []
    med = eo._nanstat(np.nanmedian, np.stack([v for _, v in looks]))
    return med, sorted((i.datetime.date().isoformat(), i.properties.get("platform", "?")) for i, _ in looks)


def one_year(year, geom, grid, inside):
    row = {"year": year}
    may, may_looks = window_ndvi(grid, inside, year, "may")
    nov, nov_looks = window_ndvi(grid, inside, year, "nov")
    n = int(inside.sum())
    row.update({"may_looks": len(may_looks), "may_dates": ", ".join(d for d, _ in may_looks),
                "nov_looks": len(nov_looks), "nov_dates": ", ".join(d for d, _ in nov_looks),
                "platforms": ", ".join(sorted({p for _, p in may_looks + nov_looks}))})
    if may is not None:
        seen = inside & np.isfinite(may)
        row["may_seen_pct"] = round(100 * seen.sum() / n, 1)
        row["green_in_may_pct"] = round(100 * (seen & (may >= GREEN)).sum() / seen.sum(), 2)
        if nov is not None:
            both = seen & np.isfinite(nov)
            row["both_seen_pct"] = round(100 * both.sum() / n, 1)
            may_med, nov_med = float(np.median(may[both])), float(np.median(nov[both]))
            perm = both & (may >= may_med + STANDS_OUT) & (nov >= nov_med + STANDS_OUT)
            row.update({"may_median_ndvi": round(may_med, 3), "nov_median_ndvi": round(nov_med, 3)})
            row["permanent_pct"] = round(100 * perm.sum() / both.sum(), 2)
            row["permanent_km2"] = round(row["permanent_pct"] / 100 * AREA_KM2, 1)
            row["green_both_abs_pct"] = round(100 * (both & (may >= GREEN) & (nov >= GREEN)).sum() / both.sum(), 2)
    if year in WORLDCOVER_YEARS:
        try:
            wc = worldcover(grid, year)
            ok = inside & np.isfinite(wc)
            row["worldcover_trees_pct"] = round(100 * (ok & (wc == 10)).sum() / ok.sum(), 2)
            if may is not None and nov is not None:
                flagged = perm & ok
                row["worldcover_precision"] = round(float((flagged & (wc == 10)).sum() / max(flagged.sum(), 1)), 2)
        except Exception as exc:
            print(f"  {year}: WorldCover check failed: {exc}", flush=True)
    return row


def worldcover(grid, year):
    items = search_items("esa-worldcover", grid.lonlat_bounds(), _utc(year, 1, 1), _utc(year, 12, 31), max_items=10)
    items = [i for i in items if str(i.properties.get("start_datetime", i.datetime))[:4] == str(year)]
    out = np.full(grid.shape, np.nan, "float32")
    for it in items:
        a = eo.read_on_grid(it.assets["map"].href, grid, Resampling.mode)
        gap = np.isnan(out)
        out[gap] = a[gap]
    return out


def main(first=FIRST, last=LAST):
    global AREA_KM2
    feat = next(f for f in json.loads(WATERSHED.read_text())["features"] if f["properties"]["name"] == "whole_river")
    AREA_KM2 = feat["properties"]["area_km2"]
    geom = shape(feat["geometry"])
    grid = eo.make_grid(geom.bounds, res=30, max_px=2400, origin=(15.0, 15.0))
    inside = eo.geometry_pixels(grid, feat["geometry"])
    done = pd.read_csv(OUT) if OUT.exists() else pd.DataFrame(columns=["year"])
    rows = [r for r in done.to_dict("records") if not first <= r["year"] <= last]
    for year in range(first, last + 1):
        row = one_year(year, geom, grid, inside)
        rows.append(row)
        keys = ("may_looks", "nov_looks", "green_in_may_pct", "permanent_pct", "green_both_abs_pct",
                "worldcover_trees_pct", "worldcover_precision", "may_seen_pct")
        print(year, {k: row.get(k) for k in keys}, flush=True)
        pd.DataFrame(rows).sort_values("year").to_csv(OUT, index=False, lineterminator="\n")  # save as we go


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]]
    try:
        eo.run_analysis(main, *args)
    except Exception:
        traceback.print_exc()
    finally:
        sys.stdout.flush()
        os._exit(0)  # see the GDAL-on-Windows note in src/eo.py
