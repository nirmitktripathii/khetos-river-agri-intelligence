"""IMD gridded daily rain (0.25 degree, gauge-based, Pai et al. 2014) from 1901, at Baheri and over the watershed.

IMD Pune publishes one binary file per year for all of India (about 25 MB). Each file is downloaded with
`imdlib`, the cells needed here are kept and the file is deleted, so only a small CSV stays on disk:

    baheri_mm      the cell centred 28.75 N 79.5 E (contains Baheri; same cell as the ERA5 series)
    baheri_area_mm mean of the 3 x 3 cells around it (about 75 km across). Single IMD cells swing with which
                   gauges reported in a year (2020: 276 mm in one cell next to 800-1,100 mm in its neighbours),
                   so the block mean is the steadier "Baheri area" figure
    cell_<lat>_mm  the three cells along 79.5 E that the watershed falls in
    watershed_mm   their mean weighted by the share of the watershed polygon in each cell

Writes inputs/imd_rain_daily.csv.gz. Resumes where it left off. IMD final data lag about a year; the current
year is skipped if not yet published.

    uv run --no-project --with imdlib --with shapely --with pyproj python research/nakatiya_observatory/pull_imd.py
"""
import json
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import imdlib
import numpy as np
import pandas as pd
import requests
from pyproj import Geod
from shapely.geometry import box, shape

HERE = Path(__file__).resolve().parent
OUT = HERE / "inputs" / "imd_rain_daily.csv.gz"
WATERSHED = HERE.parents[1] / "data" / "nakatiya_watershed.geojson"
LON = 79.5
LATS = (28.25, 28.5, 28.75)
BAHERI_LAT = 28.75
FIRST = 1901
IMD_URL = "https://imdpune.gov.in/cmpg/Griddata/rainfall.php"  # the endpoint imdlib.get_data posts to
GEOD = Geod(ellps="WGS84")
WORKERS = 6  # IMD's server is slow per connection; a few parallel downloads keep it busy without hammering it


def cell_weights():
    feat = next(f for f in json.loads(WATERSHED.read_text())["features"] if f["properties"]["name"] == "whole_river")
    ws = shape(feat["geometry"])
    area = {lat: abs(GEOD.geometry_area_perimeter(ws.intersection(box(LON - .125, lat - .125, LON + .125,
                                                                      lat + .125)))[0]) for lat in LATS}
    total = abs(GEOD.geometry_area_perimeter(ws)[0])
    print("watershed share per cell:", {k: round(v / total, 3) for k, v in area.items()},
          f"(covered {sum(area.values()) / total:.3f})", flush=True)
    s = sum(area.values())
    return {k: v / s for k, v in area.items()}


def download(year, tmp, tries=6):
    """IMD's server often cuts transfers short, so download with retries and check the size: 135 x 129 cells x
    4 bytes per day."""
    (tmp / "rain").mkdir(exist_ok=True)
    f = tmp / "rain" / f"{year}.grd"
    want = 135 * 129 * 4 * (366 if pd.Timestamp(year, 12, 31).dayofyear == 366 else 365)
    for k in range(tries):
        try:
            with requests.post(IMD_URL, data={"rain": year}, stream=True, timeout=(30, 300)) as r:
                r.raise_for_status()
                with open(f, "wb") as out:
                    for chunk in r.iter_content(1 << 20):
                        out.write(chunk)
            if f.stat().st_size == want:
                return
            raise IOError(f"got {f.stat().st_size} bytes, expected {want}")
        except Exception as exc:
            if k == tries - 1:
                raise
            print(f"  {year} retry {k + 1}: {type(exc).__name__} {str(exc)[:80]}", flush=True)
            time.sleep(10 * (k + 1))


def one_year(year, tmp):
    download(year, tmp)
    data = imdlib.open_data("rain", year, year, fn_format="yearwise", file_dir=str(tmp))
    ds = data.get_xarray()
    var = list(ds.data_vars)[0]
    cols = {}
    for lat in LATS:
        s = ds[var].sel(lat=lat, lon=LON, method="nearest")
        assert abs(float(s.lat) - lat) < 0.01 and abs(float(s.lon) - LON) < 0.01, (float(s.lat), float(s.lon))
        v = s.to_series()
        v[v < 0] = np.nan  # -999 = no data
        cols[f"cell_{lat:.2f}_mm"] = v
    block = ds[var].sel(lat=slice(BAHERI_LAT - .26, BAHERI_LAT + .26), lon=slice(LON - .26, LON + .26))
    assert block.shape[1:] == (3, 3), block.shape
    cols["baheri_area_mm"] = block.where(block >= 0).mean(("lat", "lon")).to_series()
    return pd.DataFrame(cols)


def fetch(year, weights):
    tmp = Path(tempfile.mkdtemp(prefix=f"imd_{year}_"))
    try:
        d = one_year(year, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the 25 MB national file is not kept
    d["baheri_mm"] = d[f"cell_{BAHERI_LAT:.2f}_mm"]
    d["watershed_mm"] = sum(d[f"cell_{lat:.2f}_mm"] * w for lat, w in weights.items())
    return d


def save(parts):
    out = pd.concat(parts).sort_index()
    out.index.name = "date"
    out.to_csv(OUT, float_format="%.2f")
    return out


def main(last=None, workers=WORKERS):
    last = last or date.today().year
    weights = cell_weights()
    old = pd.read_csv(OUT, index_col=0, parse_dates=True) if OUT.exists() else None
    have = set(old.index.year) if old is not None else set()
    parts = [old] if old is not None else []
    todo = [y for y in range(FIRST, last + 1) if y not in have]
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(fetch, y, weights): y for y in todo}
        for k, fut in enumerate(as_completed(futures), 1):
            year = futures[fut]
            try:
                d = fut.result()
            except Exception as exc:
                print(f"{year}: not available ({type(exc).__name__}: {str(exc)[:120]})", flush=True)
                continue
            parts.append(d)
            print(year, f"Baheri {d['baheri_mm'].sum():.0f} mm, area {d['baheri_area_mm'].sum():.0f} mm, "
                        f"watershed {d['watershed_mm'].sum():.0f} mm", flush=True)
            if k % 2 == 0:  # save as we go; a rerun resumes from the saved years
                save(parts)
    out = save(parts)
    print("wrote", OUT, out.index.min().date(), "to", out.index.max().date(), "years:", out.index.year.nunique())


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:2]))
