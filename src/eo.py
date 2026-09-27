"""Earth-observation readers for small areas of interest (AOIs).

Every product is read onto one UTM 44N grid (EPSG:32644, the native CRS of Sentinel-1, Sentinel-2 and
Landsat over Rohilkhand), so masks, bands and sensors line up pixel-for-pixel. Reads are windowed
requests against cloud-optimised GeoTIFFs; nothing is downloaded whole.
"""
import json
import logging
import math
import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache, partial

import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import Affine, from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform, reproject, transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds
from shapely.geometry import box, shape

from src.stac import search_items, search_landsat, search_s1, search_s2

log = logging.getLogger(__name__)

UTM_CRS = "EPSG:32644"  # UTM 44N spans 78–84°E, i.e. all of Rohilkhand
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    # Fail fast on stalled transfers (below 1 KB/s for 15 s) and retry, rather than hang for minutes.
    "GDAL_HTTP_CONNECTTIMEOUT": "10",
    "GDAL_HTTP_TIMEOUT": "30",
    "GDAL_HTTP_LOW_SPEED_TIME": "15",
    "GDAL_HTTP_LOW_SPEED_LIMIT": "1024",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
}

# Sentinel-2 L2A: from processing baseline 04.00 (25 Jan 2022) every band carries BOA_ADD_OFFSET = -1000.
# Planetary Computer serves the DNs unharmonised, so reflectance = (DN - 1000) / 10000 for those scenes.
S2_OFFSET_START = datetime(2022, 1, 25, tzinfo=timezone.utc)
S2_BAND_RES = {"B02": 10, "B03": 10, "B04": 10, "B08": 10, "B11": 20, "B12": 20, "SCL": 20}
SCL_VALID = (4, 5, 6, 7)  # vegetation, not-vegetated, water, unclassified; everything else is masked

# Landsat Collection 2 Level-2.
LANDSAT_QA_MASK = 0b111111  # QA_PIXEL bits 0-5: fill, dilated cloud, cirrus, cloud, cloud shadow, snow
SLC_OFF_START = datetime(2003, 5, 31, tzinfo=timezone.utc)  # Landsat 7 scan-line-corrector failure
LANDSAT_FIRST_YEAR = 1990  # no Collection-2 scene of any tier over the Nakatiya for the 1985-1989 rabi seasons
# Roy et al. (2016, Remote Sens. Environ. 185:57-70), OLS: OLI = intercept + slope * ETM+ surface reflectance.
ETM_TO_OLI = {"green": (0.8483, 0.0088), "red": (0.9047, 0.0061), "nir08": (0.8462, 0.0412),
              "swir16": (0.8937, 0.0254)}
WATER_MNDWI = 0.05
GREEN_NDVI = 0.50
NEVER_GREEN_NDVI = 0.40

# Impact Observatory 10 m annual land cover: io-lulc-annual-v02 on Planetary Computer for 2017-2023; later
# years from the same producer's Esri Living Atlas image service (pixel-identical for the overlapping years).
LULC_YEARS = (2017, 2025)
LULC_PC_LAST = 2023
ESRI_LULC_URL = ("https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer/"
                 "exportImage")
LULC_CLASSES = {1: "water", 2: "trees", 4: "flooded_vegetation", 5: "crops", 7: "built", 8: "bare", 11: "rangeland"}
LULC_CLOUD = 10

# DLR World Settlement Footprint Evolution v1: 30 m, value = first year a pixel is settled (1985 means 1985 or
# earlier), 0 = not settled by 2015. 2x2 degree cloud-optimised GeoTIFF tiles named by their south-west corner.
WSF_EVO_URL = "https://download.geoservice.dlr.de/WSF_EVO/files/WSFevolution_v1_{lon}_{lat}.tif"
WSF_YEARS = (1985, 2015)

# JRC Global Surface Water v1.3 (1984-2020) transition classes.
JRC_TRANSITIONS = {
    1: "permanent", 2: "new_permanent", 3: "lost_permanent", 4: "seasonal", 5: "new_seasonal",
    6: "lost_seasonal", 7: "seasonal_to_permanent", 8: "permanent_to_seasonal",
    9: "ephemeral_permanent", 10: "ephemeral_seasonal",
}


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _now():
    return datetime.now(timezone.utc)


def latest_rabi_year(today=None):
    """The latest complete rabi (winter-crop) season: it runs December to March and is labelled by the year it
    ends in, so from May onwards it is this year's."""
    today = today or _now().date()
    return today.year if today.month >= 5 else today.year - 1


# --------------------------------------------------------------------------------------------- grid

@dataclass(frozen=True)
class Grid:
    transform: Affine
    width: int
    height: int
    crs: str = UTM_CRS

    @property
    def shape(self):
        return self.height, self.width

    @property
    def res(self):
        return self.transform.a

    @property
    def bounds(self):
        left, top = self.transform.c, self.transform.f
        return left, top - self.height * self.res, left + self.width * self.res, top

    def lonlat_bounds(self):
        return transform_bounds(self.crs, "EPSG:4326", *self.bounds, densify_pts=21)


def make_grid(bbox, res=10.0, max_px=1024, origin=(0.0, 0.0)):
    """Snap a lon/lat bbox to a UTM grid. The pixel size grows in multiples of `res` to stay <= max_px a side.

    `origin` shifts the pixel lattice, e.g. (15, 15) matches Landsat's grid, (0, 0) matches Sentinel-2's.
    """
    minx, miny, maxx, maxy = transform_bounds("EPSG:4326", UTM_CRS, *bbox, densify_pts=21)
    step = res * max(1, math.ceil(max(maxx - minx, maxy - miny) / (res * max_px)))
    ox, oy = origin
    left = math.floor((minx - ox) / step) * step + ox
    right = math.ceil((maxx - ox) / step) * step + ox
    bottom = math.floor((miny - oy) / step) * step + oy
    top = math.ceil((maxy - oy) / step) * step + oy
    return Grid(from_origin(left, top, step, step), round((right - left) / step), round((top - bottom) / step))


# rasterio 1.5 rasterize, run concurrently, occasionally reports its in-memory dataset as un-georeferenced
# (NotGeoreferencedWarning) although the mask comes out right; serialising the milliseconds-long call avoids it.
_RASTERIZE_LOCK = threading.Lock()


def geometry_pixels(grid, geometry=None):
    """Mask of grid pixels whose centres fall inside a lon/lat geometry (every pixel when geometry is None)."""
    if geometry is None:
        return np.ones(grid.shape, dtype=bool)
    if hasattr(geometry, "__geo_interface__"):
        geometry = geometry.__geo_interface__
    g = transform_geom("EPSG:4326", grid.crs, geometry)
    with _RASTERIZE_LOCK:
        return geometry_mask([g], out_shape=grid.shape, transform=grid.transform, invert=True)


# --------------------------------------------------------------------------------------------- I/O

def _read_window(src, grid, resampling):
    """Windowed read for a source already in the grid CRS; uses COG overviews when downsampling."""
    out = np.full(grid.shape, np.nan, dtype="float32")
    gl, gb, gr, gt = grid.bounds
    sl, sb, sr, st = src.bounds
    left, bottom, right, top = max(sl, gl), max(sb, gb), min(sr, gr), min(st, gt)
    if right <= left or top <= bottom:
        return out
    # Grid pixels lying wholly inside the source footprint.
    c0 = math.ceil((left - gl) / grid.res - 1e-6)
    c1 = math.floor((right - gl) / grid.res + 1e-6)
    r0 = math.ceil((gt - top) / grid.res - 1e-6)
    r1 = math.floor((gt - bottom) / grid.res + 1e-6)
    if c1 <= c0 or r1 <= r0:
        return out
    win = from_bounds(gl + c0 * grid.res, gt - r1 * grid.res, gl + c1 * grid.res, gt - r0 * grid.res,
                      transform=src.transform)
    col, row = max(0.0, win.col_off), max(0.0, win.row_off)
    win = Window(col, row, min(win.width, src.width - col), min(win.height, src.height - row))
    data = src.read(1, window=win, out_shape=(r1 - r0, c1 - c0), resampling=resampling).astype("float32")
    if src.nodata is not None:
        data[data == src.nodata] = np.nan
    out[r0:r1, c0:c1] = data
    return out


class _DaemonPool:
    """A fixed set of long-lived daemon worker threads.

    GDAL keeps HTTP connections per thread, so reads on long-lived threads reuse warm connections.
    It also matters on Windows: when a thread that holds GDAL network state exits, its cleanup runs under
    the OS loader lock and can deadlock every later thread start. Raster reads therefore only ever run on
    these threads, which never exit (daemon threads are not joined at interpreter shutdown).
    """

    def __init__(self, name, workers):
        self.name = name
        self._queue = queue.SimpleQueue()
        for i in range(workers):
            threading.Thread(target=self._work, name=f"{name}-{i}", daemon=True).start()

    def _work(self):
        while True:
            future, fn, args = self._queue.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args))
            except BaseException as exc:
                future.set_exception(exc)

    def submit(self, fn, *args):
        future = Future()
        self._queue.put((future, fn, args))
        return future

    def owns_current_thread(self):
        return threading.current_thread().name.startswith(self.name + "-")


# Four levels so a worker only ever waits on a lower level (no pool can starve itself):
# whole analyses -> jobs (e.g. one river analysis per year) -> per-scene tasks -> raster reads.
_ANALYSIS_POOL = _DaemonPool("khetos-analysis", 4)
JOB_POOL = _DaemonPool("khetos-job", 6)
_TASK_POOL = _DaemonPool("khetos-task", 8)
_IO_POOL = _DaemonPool("khetos-io", 16)


def run_analysis(fn, *args, **kwargs):
    """Run a whole analysis on a long-lived analysis thread and wait for it.

    For callers on short-lived threads: Streamlit runs every script rerun on a new thread that then exits, so
    anything that creates GDAL or PROJ state (grids, masks, reprojection) runs here instead.
    """
    if any(p.owns_current_thread() for p in (_ANALYSIS_POOL, JOB_POOL, _TASK_POOL, _IO_POOL)):
        return fn(*args, **kwargs)
    return _ANALYSIS_POOL.submit(partial(fn, *args, **kwargs)).result()


def run_jobs(*calls):
    """Run (fn, *args) tuples concurrently on the job pool; returns futures in the same order."""
    return [JOB_POOL.submit(c[0], *c[1:]) for c in calls]


def _on_io(fn, *args):
    """Run a raster read on the I/O threads (see _DaemonPool)."""
    if _IO_POOL.owns_current_thread():
        return fn(*args)
    return _IO_POOL.submit(fn, *args).result()


def read_on_grid(href, grid, resampling=Resampling.nearest):
    """Read band 1 of a raster onto `grid` as float32, NaN where the source has no data."""
    return _on_io(_read_on_grid, href, grid, resampling)


def _read_on_grid(href, grid, resampling):
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
        return _dataset_on_grid(src, grid, resampling)


def _dataset_on_grid(src, grid, resampling):
    if src.crs == CRS.from_string(grid.crs):
        return _read_window(src, grid, resampling)
    with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                   resampling=resampling, src_nodata=src.nodata, nodata=src.nodata) as vrt:
        out = vrt.read(1).astype("float32")
    if src.nodata is not None:
        out[out == src.nodata] = np.nan
    return out


def to_web_mercator(array, grid, resampling=Resampling.nearest):
    """Reproject a grid array to Web Mercator so an image overlay lines up exactly on a web map.

    Returns (array, ((south, west), (north, east))). Runs on the I/O threads, like every GDAL call the app
    makes (see _DaemonPool).
    """
    return _on_io(_to_web_mercator, array, grid, resampling)


def _to_web_mercator(array, grid, resampling):
    left, bottom, right, top = grid.bounds
    dst, w, h = calculate_default_transform(grid.crs, "EPSG:3857", grid.width, grid.height,
                                            left=left, bottom=bottom, right=right, top=top)
    out = np.full((h, w), np.nan, dtype="float32")
    reproject(np.asarray(array, dtype="float32"), out, src_transform=grid.transform, src_crs=grid.crs,
              dst_transform=dst, dst_crs="EPSG:3857", src_nodata=np.nan, dst_nodata=np.nan, resampling=resampling)
    west, south, east, north = transform_bounds("EPSG:3857", "EPSG:4326", dst.c, dst.f + h * dst.e,
                                                dst.c + w * dst.a, dst.f)
    return out, ((south, west), (north, east))


def _pmap(fn, items, io=True):
    """Ordered concurrent map. io=True: `fn` only reads rasters (runs on the I/O threads);
    io=False: `fn` orchestrates reads of its own (runs on the task threads)."""
    items = list(items)
    pool = _IO_POOL if io else _TASK_POOL
    if len(items) <= 1 or pool.owns_current_thread():
        return [fn(x) for x in items]
    futures = [pool.submit(fn, x) for x in items]
    return [f.result() for f in futures]


def _safe(fn):
    """Wrap a per-scene reader so one unreadable scene does not sink a multi-scene analysis."""
    def run(x):
        try:
            return fn(x)
        except Exception as exc:
            item = x[0] if isinstance(x, tuple) else x  # (item, clear mask) pairs: name the item, not the mask
            log.warning("Skipping %s: %s", getattr(item, "id", item), exc)
            return None
    return run


def normalized_difference(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        nd = ((a - b) / (a + b)).astype("float32")
    nd[~np.isfinite(nd)] = np.nan  # 0/0 where both reflectances clip to zero
    return nd


def _median(values):
    values = values[np.isfinite(values)]
    return float(np.median(values)) if values.size else float("nan")


def _nanstat(fn, stack):
    """np.nanmax / np.nanmedian over the first axis, NaN where every look is NaN. Filling those pixels first
    avoids numpy's all-NaN RuntimeWarning, which cannot be silenced reliably from worker threads
    (warnings.catch_warnings is not thread-safe)."""
    stack = np.asarray(stack, dtype="float32")
    seen = np.isfinite(stack).any(axis=0)
    out = fn(np.where(seen, stack, 0), axis=0).astype("float32")
    out[~seen] = np.nan
    return out


# ------------------------------------------------------------------------------------- Sentinel-2

def s2_boa_offset(item):
    """BOA_ADD_OFFSET for a Sentinel-2 L2A item: -1000 from processing baseline 04.00, else 0."""
    try:
        return -1000 if float(item.properties.get("s2:processing_baseline")) >= 4.0 else 0
    except (TypeError, ValueError):
        return -1000 if item.datetime >= S2_OFFSET_START else 0


def s2_reflectance(dn, offset):
    """DN -> surface reflectance. DN 0 is nodata; reflectance below zero is clipped, as Planetary
    Computer's harmonisation does."""
    x = np.asarray(dn, dtype="float32")
    invalid = ~np.isfinite(x) | (x == 0)
    if offset:
        x = np.maximum(x, -offset) + offset
    x = x / 10000.0
    x[invalid] = np.nan
    return x


def scl_clear(scl):
    return np.isin(np.nan_to_num(scl, nan=0).astype(np.uint8), SCL_VALID)


@dataclass
class S2Scene:
    id: str
    datetime: datetime
    tile_cloud: float
    grid: Grid
    aoi: np.ndarray
    clear: np.ndarray  # AOI pixels with a valid scene classification
    coverage: float  # share of AOI pixels with data
    clear_fraction: float  # share of AOI pixels that are clear
    bands: dict = field(default_factory=dict)

    @property
    def date(self):
        return self.datetime.date()

    @property
    def ndvi(self):
        return normalized_difference(self.bands["B08"], self.bands["B04"])

    @property
    def ndmi(self):
        return normalized_difference(self.bands["B08"], self.bands["B11"])


def _screen_s2(item, grid, aoi):
    scl = read_on_grid(item.assets["SCL"].href, grid, Resampling.nearest)
    n = max(int(aoi.sum()), 1)
    clear = aoi & scl_clear(scl)
    scene = S2Scene(item.id, item.datetime, float(item.properties.get("eo:cloud_cover", np.nan)), grid, aoi,
                    clear, float((aoi & (np.nan_to_num(scl) > 0)).sum() / n), float(clear.sum() / n))
    return scene, item


def _read_s2_bands(item, grid, bands=("B04", "B08", "B11")):
    offset = s2_boa_offset(item)

    def one(b):
        rs = Resampling.average if grid.res > S2_BAND_RES[b] else Resampling.bilinear
        return b, s2_reflectance(read_on_grid(item.assets[b].href, grid, rs), offset)

    return dict(_pmap(one, bands))


def _one_per_day(items, bbox):
    """One item per acquisition day: tiles whose footprint contains the AOI first, then least cloudy."""
    aoi = box(*bbox)
    best = {}
    for it in items:
        rank = (not shape(it.geometry).contains(aoi), it.properties.get("eo:cloud_cover", 100))
        day = it.datetime.date()
        if day not in best or rank < best[day][0]:
            best[day] = (rank, it)
    return sorted((v[1] for v in best.values()), key=lambda i: i.datetime, reverse=True)


def find_clear_s2(bbox, grid, aoi, start, end, max_cloud_pct=35, want=1, max_checks=24, min_coverage=0.9):
    """Newest scenes whose AOI (not tile) cloud share is within `max_cloud_pct`, screened with the SCL band."""
    items = search_s2(bbox, start, end, max_tile_cloud=min(95, max_cloud_pct + 50), max_items=150)
    items = _one_per_day(items, bbox)[:max_checks]
    min_clear = 1 - max_cloud_pct / 100
    found = []
    for i in range(0, len(items), 6):
        for res in _pmap(_safe(lambda it: _screen_s2(it, grid, aoi)), items[i:i + 6]):
            if res and res[0].coverage >= min_coverage and res[0].clear_fraction >= min_clear:
                found.append(res)
        if len(found) >= want:
            break
    return found[:want]


def latest_clear_s2(bbox, geometry=None, months=6, max_cloud_pct=35, res=10, max_px=1024):
    """Most recent Sentinel-2 scene that is clear over the AOI, with B04/B08/B11 read on a UTM grid."""
    grid = make_grid(bbox, res, max_px)
    aoi = geometry_pixels(grid, geometry)
    if not aoi.any():
        raise ValueError("The area is smaller than one pixel; draw a larger field.")
    end = _now()
    found = find_clear_s2(bbox, grid, aoi, end - timedelta(days=round(30.44 * months)), end, max_cloud_pct)
    if not found:
        raise RuntimeError(f"No Sentinel-2 scene in the last {months} months has at least "
                           f"{100 - max_cloud_pct}% clear pixels over this area. Widen the history window "
                           "or raise the cloud tolerance.")
    scene, item = found[0]
    scene.bands = _read_s2_bands(item, grid)
    return scene


def previous_clear_s2(scene, bbox, max_cloud_pct=35, lookback_days=75, min_gap_days=4):
    """The clear scene before `scene` on the same grid and AOI, or None."""
    end = scene.datetime - timedelta(days=min_gap_days)
    found = find_clear_s2(bbox, scene.grid, scene.aoi, end - timedelta(days=lookback_days), end, max_cloud_pct)
    if not found:
        return None
    prev, item = found[0]
    prev.bands = _read_s2_bands(item, scene.grid)
    return prev


def scene_metrics(scene, crop_mask=None, min_crop_pixels=50):
    """AOI medians of NDVI/NDMI over clear pixels, restricted to cropland when enough cropland is clear."""
    sel, used = scene.clear, "all clear pixels"
    if crop_mask is not None and int((sel & crop_mask).sum()) >= min_crop_pixels:
        sel, used = sel & crop_mask, "clear cropland pixels"
    ndvi, ndmi = scene.ndvi, scene.ndmi
    ok = sel & np.isfinite(ndvi) & np.isfinite(ndmi)
    return {"date": scene.date.isoformat(), "scene": scene.id,
            "ndvi": _median(ndvi[ok]), "ndmi": _median(ndmi[ok]),
            "clear_pct": round(100 * scene.clear_fraction, 1), "pixels": int(ok.sum()), "pixels_used": used,
            "tile_cloud_pct": round(scene.tile_cloud, 1)}


def _thin(items, n):
    """At most n items spread evenly through time, taking the least cloudy tile in each time bin."""
    if len(items) <= n:
        return items
    items = sorted(items, key=lambda i: i.datetime)
    bins = np.array_split(np.arange(len(items)), n)
    return [min((items[j] for j in b), key=lambda i: i.properties.get("eo:cloud_cover", 100)) for b in bins if len(b)]


def trend_series(bbox, months=6, max_cloud_pct=35, max_scenes=24):
    """NDVI/NDMI time series over the AOI's cropland from SCL-screened Sentinel-2 scenes (20 m grid)."""
    grid = make_grid(bbox, res=20, max_px=512)
    aoi = geometry_pixels(grid)
    try:
        crop = cropland_mask(grid)
    except Exception as exc:
        log.warning("Cropland mask unavailable: %s", exc)
        crop = None
    end = _now()
    items = search_s2(bbox, end - timedelta(days=round(30.44 * months)), end,
                      max_tile_cloud=min(95, max_cloud_pct + 50), max_items=200)
    items = _thin(_one_per_day(items, bbox), max_scenes)
    min_clear = 1 - max_cloud_pct / 100

    def row(item):
        scene, _ = _screen_s2(item, grid, aoi)
        if scene.coverage < 0.9 or scene.clear_fraction < min_clear:
            return None
        scene.bands = _read_s2_bands(item, grid)
        return scene_metrics(scene, crop)

    rows = [r for r in _pmap(_safe(row), items, io=False) if r]
    cols = ["date", "ndvi", "ndmi", "clear_pct", "pixels", "pixels_used", "scene", "tile_cloud_pct"]
    if not rows:
        return pd.DataFrame(columns=cols + ["ndvi_change", "ndmi_change", "days_since_prev"])
    df = pd.DataFrame(rows)[cols]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df["ndvi_change"] = df["ndvi"].diff()
    df["ndmi_change"] = df["ndmi"].diff()
    df["days_since_prev"] = df["date"].diff().dt.days
    return df


def s2_window_greenest(bbox, grid, aoi, start, end, max_scenes=8, min_clear=0.05):
    """Per-pixel greenest NDVI, median SWIR-1 reflectance and clear-look count over a date window.

    Each scene is masked pixel by pixel with its scene classification, so partly cloudy monsoon scenes still
    contribute their clear pixels. The greenest look makes the result insensitive to harvest dates inside the
    window.
    """
    n = max(int(aoi.sum()), 1)
    items = _thin(_one_per_day(search_s2(bbox, start, end, max_tile_cloud=90, max_items=150), bbox), 3 * max_scenes)

    def screen(item):
        return item, aoi & scl_clear(read_on_grid(item.assets["SCL"].href, grid, Resampling.nearest))

    usable = sorted((r for r in _pmap(_safe(screen), items) if r and r[1].sum() >= min_clear * n),
                    key=lambda r: r[0].datetime)
    if len(usable) > max_scenes:
        bins = np.array_split(np.arange(len(usable)), max_scenes)
        usable = [max((usable[j] for j in b), key=lambda r: int(r[1].sum())) for b in bins if len(b)]

    def read(pair):
        item, clear = pair
        b = _read_s2_bands(item, grid, ("B04", "B08", "B11"))
        ndvi = normalized_difference(b["B08"], b["B04"])
        swir = b["B11"]
        ndvi[~clear] = np.nan
        swir[~clear] = np.nan
        return item, ndvi, swir

    looks = [r for r in _pmap(_safe(read), usable, io=False) if r]
    if not looks:
        raise RuntimeError(f"No clear Sentinel-2 look between {start:%d %b %Y} and {end:%d %b %Y}")
    ndvi = np.stack([r[1] for r in looks])
    return {"ndvi_max": _nanstat(np.nanmax, ndvi),  # pixels never seen clear stay NaN
            "swir_median": _nanstat(np.nanmedian, np.stack([r[2] for r in looks])),
            "n_obs": np.isfinite(ndvi).sum(axis=0),
            "dates": [r[0].datetime.date().isoformat() for r in looks]}


# ------------------------------------------------------------------------------------- land cover

def _lulc_pc(grid, year):
    items = [i for i in search_items("io-lulc-annual-v02", grid.lonlat_bounds(), _utc(year, 1, 1),
                                     _utc(year, 12, 31), max_items=10)
             if str(i.properties.get("start_datetime", ""))[:4] == str(year)]
    if not items:
        raise RuntimeError(f"No Impact Observatory land-cover map for {year} on Planetary Computer")
    out = np.full(grid.shape, np.nan, dtype="float32")
    for it in items:
        arr = read_on_grid(it.assets["data"].href, grid, Resampling.nearest)
        gap = np.isnan(out)
        out[gap] = arr[gap]
    return out


ESRI_MAX_PX = 4000  # the image service's export size limit


def _lulc_esri(grid, year):
    """The same Impact Observatory map exported from Esri's image service on `grid`, in blocks of at most
    ESRI_MAX_PX a side. Raw class values: no rendering, nearest-neighbour."""
    epsg = CRS.from_string(grid.crs).to_epsg()
    window = f"{int(_utc(year, 1, 1).timestamp() * 1000)},{int(_utc(year, 12, 31).timestamp() * 1000)}"
    left, _, _, top = grid.bounds
    out = np.full(grid.shape, np.nan, dtype="float32")
    for r0 in range(0, grid.height, ESRI_MAX_PX):
        for c0 in range(0, grid.width, ESRI_MAX_PX):
            h, w = min(ESRI_MAX_PX, grid.height - r0), min(ESRI_MAX_PX, grid.width - c0)
            x0, y1 = left + c0 * grid.res, top - r0 * grid.res
            params = {"bbox": f"{x0},{y1 - h * grid.res},{x0 + w * grid.res},{y1}", "bboxSR": epsg,
                      "imageSR": epsg, "size": f"{w},{h}", "format": "tiff", "pixelType": "U8",
                      "interpolation": "RSP_NearestNeighbor", "renderingRule": json.dumps({"rasterFunction": "None"}),
                      "time": window, "f": "image"}
            resp = requests.get(ESRI_LULC_URL, params=params, timeout=90)
            resp.raise_for_status()
            if resp.content[:1] == b"{":  # errors come back as JSON with HTTP 200
                raise RuntimeError(f"Esri land-cover service: {resp.text[:200]}")
            with rasterio.MemoryFile(resp.content) as mf, mf.open() as src:
                block = src.read(1).astype("float32")
            if block.shape != (h, w):
                raise RuntimeError(f"Esri land-cover service returned {block.shape}, expected {(h, w)}")
            block[block == 0] = np.nan
            out[r0:r0 + h, c0:c0 + w] = block
    return out


@lru_cache(maxsize=16)
def lulc_on_grid(grid, year=LULC_YEARS[1]):
    """Impact Observatory annual land-cover classes on `grid` (NaN = no data). Read-only array.

    Planetary Computer serves 2017-2023. Later years, and any year Planetary Computer cannot serve, come
    from Esri's copy of the same maps (checked pixel-identical for 2017 and 2023).
    """
    year = int(year)
    if not LULC_YEARS[0] <= year <= LULC_YEARS[1]:
        raise ValueError(f"Impact Observatory land cover spans {LULC_YEARS[0]}-{LULC_YEARS[1]}, not {year}")
    out = None
    if year <= LULC_PC_LAST:
        try:
            out = _lulc_pc(grid, year)
        except Exception as exc:
            log.warning("Planetary Computer land cover %s unavailable (%s); trying Esri", year, exc)
    if out is None:
        out = _on_io(_lulc_esri, grid, year)
    out.flags.writeable = False
    return out


def latest_cropland(grid):
    """(cropland mask, year) from the latest land-cover map, or from the latest Planetary Computer year when
    the later maps cannot be read."""
    try:
        return lulc_on_grid(grid, LULC_YEARS[1]) == 5, LULC_YEARS[1]
    except Exception as exc:
        log.warning("Land cover %s unavailable (%s); using %s", LULC_YEARS[1], exc, LULC_PC_LAST)
        return lulc_on_grid(grid, LULC_PC_LAST) == 5, LULC_PC_LAST


def cropland_mask(grid, year=None):
    """Cropland (class 5) in the land-cover map of `year`, by default the latest (see latest_cropland)."""
    return latest_cropland(grid)[0] if year is None else lulc_on_grid(grid, year) == 5


def lulc_fractions(geometry, year, grid=None):
    """Percentage of each land-cover class inside a lon/lat geometry, or None outside LULC_YEARS.

    Pass the `grid` of an enclosing geometry to summarise nested areas from one (cached) read.
    """
    year = int(year)
    if not LULC_YEARS[0] <= year <= LULC_YEARS[1]:
        return None
    grid = grid or make_grid(shape(geometry).bounds, res=10, max_px=2048)
    inside = geometry_pixels(grid, geometry)
    lulc = lulc_on_grid(grid, year)
    valid = inside & np.isfinite(lulc) & (lulc > 0) & (lulc != LULC_CLOUD)
    n = int(valid.sum())
    if not n:
        return None
    out = {"year": year}
    out.update({f"{name}_pct": round(100 * float(((lulc == code) & valid).sum()) / n, 2)
                for code, name in LULC_CLASSES.items()})
    out["pixels"] = n
    return out


# ------------------------------------------------------------------------------------- settlement

def wsf_tiles(lonlat_bounds):
    """URLs of the 2x2 degree WSF Evolution tiles that cover a lon/lat bbox."""
    w, s, e, n = lonlat_bounds
    lons = range(math.floor(w / 2) * 2, math.floor(e / 2) * 2 + 1, 2)
    lats = range(math.floor(s / 2) * 2, math.floor(n / 2) * 2 + 1, 2)
    return [WSF_EVO_URL.format(lon=x, lat=y) for x in lons for y in lats]


@lru_cache(maxsize=16)
def wsf_evolution_on_grid(grid):
    """First year each grid pixel counts as settled (1985-2015; 1985 means 1985 or earlier), 0 where it was
    not settled by 2015. Read-only array. Neighbouring tiles overlap slightly and agree, so they are mosaicked
    by maximum; a tile that cannot be read raises rather than showing up as 'not settled'."""
    out = np.zeros(grid.shape, dtype="float32")
    for arr in _pmap(lambda url: read_on_grid(url, grid, Resampling.nearest), wsf_tiles(grid.lonlat_bounds())):
        out = np.fmax(out, arr)
    out = np.nan_to_num(out, nan=0.0)
    out.flags.writeable = False
    return out


def settlement_shares(geometry, years, grid=None):
    """Settled share (%) of a lon/lat geometry by the end of each year in `years` that WSF Evolution covers.

    Returns {year: pct}, empty when no year is covered or the geometry holds no pixel.
    """
    years = [int(y) for y in years if WSF_YEARS[0] <= int(y) <= WSF_YEARS[1]]
    if not years:
        return {}
    grid = grid or make_grid(shape(geometry).bounds, res=10, max_px=2048)
    inside = geometry_pixels(grid, geometry)
    n = int(inside.sum())
    if not n:
        return {}
    first = wsf_evolution_on_grid(grid)[inside]
    return {y: round(100 * float(((first > 0) & (first <= y)).sum()) / n, 2) for y in years}


def built_extent_on_grid(grid, year):
    """Built-up / settled pixels for one year, for a time-slider map, from validated products only.

    Returns (mask, label). 1985-2015: DLR WSF Evolution (settled by that year). 2017 onwards: Impact
    Observatory built area (the latest map for later years). Before 1985 the 1985 extent is shown (it means
    "1985 or earlier"); 2016 has no validated map, so the 2015 extent is shown.
    """
    year = int(year)
    if year <= WSF_YEARS[1] + 1:
        y = min(max(year, WSF_YEARS[0]), WSF_YEARS[1])
        first = wsf_evolution_on_grid(grid)
        label = f"Settled by {y} (DLR WSF Evolution)" + ("" if y == year else f", shown for {year}")
        return (first > 0) & (first <= y), label
    y = min(year, LULC_YEARS[1])
    label = f"Built area {y} (Impact Observatory)" + ("" if y == year else f", latest map, shown for {year}")
    return lulc_on_grid(grid, y) == 7, label


def jrc_water_summary(geometry):
    """Long-term surface-water history (JRC GSW v1.3, 1984-2020) inside a lon/lat geometry."""
    bounds = shape(geometry).bounds
    items = search_items("jrc-gsw", bounds, _utc(1980, 1, 1), _utc(2030, 1, 1), max_items=10)
    if not items:
        raise RuntimeError("JRC Global Surface Water is unavailable for this area")
    grid = make_grid(bounds, res=30, max_px=1024)
    inside = geometry_pixels(grid, geometry)
    occ = np.zeros(grid.shape, dtype="float32")
    trans = np.zeros(grid.shape, dtype="float32")
    for it in items:  # 10x10 degree tiles without a nodata value: mosaic by maximum
        occ = np.fmax(occ, read_on_grid(it.assets["occurrence"].href, grid, Resampling.nearest))
        trans = np.fmax(trans, read_on_grid(it.assets["transitions"].href, grid, Resampling.nearest))
    occ[occ > 100] = np.nan
    trans[trans > 10] = np.nan
    n = int(inside.sum())
    if not n:
        return None
    pct = lambda m: round(100 * float((m & inside).sum()) / n, 2)
    ever = pct(occ > 0)
    lost = pct(np.isin(trans, (3, 6)))
    out = {"period": "1984-2020", "ever_water_pct": ever, "frequent_water_pct": pct(occ >= 50),
           "lost_water_pct": lost, "new_water_pct": pct(np.isin(trans, (2, 5))),
           "lost_share_of_historic_water_pct": round(100 * lost / ever, 1) if ever else 0.0}
    out.update({f"{name}_pct": pct(trans == code) for code, name in JRC_TRANSITIONS.items()})
    return out


# ------------------------------------------------------------------------------------- Sentinel-1

def s1_db(linear):
    x = np.asarray(linear, dtype="float32")
    with np.errstate(divide="ignore", invalid="ignore"):
        db = (10 * np.log10(x)).astype("float32")
    db[~(x > 0)] = np.nan
    return db


def latest_s1_pair(bbox, lookback_days=45):
    """Latest Sentinel-1 RTC scene covering the AOI and the previous pass on the same relative orbit.

    Backscatter depends on viewing geometry, so change is only compared within one relative orbit.
    """
    end = _now()
    aoi = box(*bbox)
    covers = lambda i: shape(i.geometry).intersection(aoi).area >= 0.9 * aoi.area
    items = [i for i in search_s1(bbox, end - timedelta(days=lookback_days), end, max_items=40) if covers(i)]
    if not items:
        raise RuntimeError(f"No Sentinel-1 RTC scene covers the AOI in the last {lookback_days} days")
    latest = items[0]
    orbit = latest.properties.get("sat:relative_orbit")
    if orbit is None:
        return latest, None
    older = search_s1(bbox, latest.datetime - timedelta(days=60), latest.datetime - timedelta(days=5),
                      relative_orbit=orbit, max_items=6)
    return latest, next((i for i in older if covers(i)), None)


def s1_scene_metrics(item, bbox, crop=True, geometry=None):
    """Median VV/VH backscatter (dB), VH/VV ratio and dual-pol RVI over the AOI on a 30 m grid.

    Averaging 10 m pixels to 30 m suppresses speckle before the per-pixel open-water test
    (VV below -18 dB). `geometry` (lon/lat) restricts the statistics to e.g. a river corridor.
    """
    grid = make_grid(bbox, res=30, max_px=512)
    vv, vh = _pmap(lambda a: read_on_grid(item.assets[a].href, grid, Resampling.average), ["vv", "vh"])
    valid = (vv > 0) & (vh > 0) & geometry_pixels(grid, geometry)
    sel, used = valid, "all pixels"
    if crop:
        try:
            cm = cropland_mask(grid)
            if int((valid & cm).sum()) >= 30:
                sel, used = valid & cm, "cropland pixels"
        except Exception as exc:
            log.warning("Cropland mask unavailable: %s", exc)
    vv_m, vh_m = _median(vv[sel]), _median(vh[sel])
    vv_db = s1_db(vv)
    return {"date": item.datetime.date().isoformat(), "scene": item.id,
            "platform": item.properties.get("platform"),
            "relative_orbit": item.properties.get("sat:relative_orbit"),
            "orbit_state": item.properties.get("sat:orbit_state"),
            "vv_db": 10 * math.log10(vv_m) if vv_m > 0 else float("nan"),
            "vh_db": 10 * math.log10(vh_m) if vh_m > 0 else float("nan"),
            "vh_vv_db": 10 * math.log10(vh_m / vv_m) if vv_m > 0 and vh_m > 0 else float("nan"),
            "rvi": 4 * vh_m / (vv_m + vh_m) if vv_m + vh_m > 0 else float("nan"),
            "water_like_pct": round(100 * float((vv_db[valid] < -18).mean()), 1) if valid.any() else float("nan"),
            "pixels": int(sel.sum()), "pixels_used": used}


# ------------------------------------------------------------------------------------- Landsat

def landsat_clear(qa):
    return (np.nan_to_num(qa, nan=1).astype(np.uint16) & LANDSAT_QA_MASK) == 0


def landsat_reflectance(dn, platform=None, band=None):
    """Collection 2 Level-2 DN -> surface reflectance. TM and ETM+ bands are mapped onto OLI's with the
    Roy et al. (2016) OLS coefficients, so indices stay comparable across 1990-2026."""
    x = np.asarray(dn, dtype="float32") * 2.75e-05 - 0.2
    if platform in ("landsat-4", "landsat-5", "landsat-7") and band in ETM_TO_OLI:
        slope, intercept = ETM_TO_OLI[band]
        x = intercept + slope * x
    return x


def _slc_off(item):
    return item.properties.get("platform") == "landsat-7" and item.datetime > SLC_OFF_START


def _in_peak(dt):
    """Mid-January to March: rabi crops (wheat, mustard) are at or near full canopy."""
    return dt.month in (2, 3) or (dt.month == 1 and dt.day >= 15)


def landsat_candidates(bbox, year):
    """Tier-1 scenes from November of `year - 1` to April of `year`, one per acquisition day."""
    items = search_landsat(bbox, _utc(year - 1, 11, 1), _utc(year, 5, 1), max_cloud=70, max_items=80)
    return _one_per_day(items, bbox)


def choose_landsat_scenes(usable, max_scenes=8):
    """Pick scenes for one season from (item, clear_mask) pairs.

    Dec-Mar scenes come first. Landsat 7 SLC-off scenes (striped gaps), then November/April scenes, are added
    only until there are three scenes including one in the mid-Jan-to-March peak. The pick is then thinned
    evenly through time, keeping the clearest scene in each time bin.
    """
    core = [u for u in usable if u[0].datetime.month in (12, 1, 2, 3)]
    edge = [u for u in usable if u[0].datetime.month in (11, 4)]
    chosen = []
    for tier in ([u for u in core if not _slc_off(u[0])], [u for u in core if _slc_off(u[0])],
                 [u for u in edge if not _slc_off(u[0])], [u for u in edge if _slc_off(u[0])]):
        if len(chosen) >= 3 and any(_in_peak(u[0].datetime) for u in chosen):
            break
        chosen += tier
    chosen.sort(key=lambda u: u[0].datetime)
    if len(chosen) > max_scenes:
        bins = np.array_split(np.arange(len(chosen)), max_scenes)
        chosen = [max((chosen[j] for j in b), key=lambda u: int(u[1].sum())) for b in bins if len(b)]
    return chosen


@dataclass
class LandsatComposite:
    """Per-pixel season statistics of cloud-masked Landsat scenes on a 30 m grid: the greenest NDVI
    (`ndvi_max`) and the median MNDWI (`mndwi_median`)."""
    year: int
    season: str
    grid: Grid
    indices: dict  # name -> 2-D array, NaN outside the geometry or where no clear observation exists
    n_obs: np.ndarray
    dates: list
    platforms: str
    peak_scenes: int
    slc_off_scenes: int
    scene_ids: list

    @property
    def scenes(self):
        return len(self.dates)


def landsat_season_composite(geometry, year, max_scenes=8, min_clear=0.3):
    """Season composite for the rabi season ending in `year`, inside a lon/lat geometry.

    Nested sub-areas (e.g. narrower buffers) can then be summarised with `composite_shares` without
    reading the imagery again.
    """
    year = int(year)
    if year < LANDSAT_FIRST_YEAR:
        raise ValueError(f"The Tier-1 Landsat archive over Rohilkhand starts with the {LANDSAT_FIRST_YEAR} "
                         "rabi season")
    bounds = shape(geometry).bounds
    grid = make_grid(bounds, res=30, max_px=1024, origin=(15.0, 15.0))
    inside = geometry_pixels(grid, geometry)
    n = int(inside.sum())
    if not n:
        raise ValueError("The corridor is smaller than one Landsat pixel")
    items = landsat_candidates(bounds, year)
    if not items:
        raise RuntimeError(f"No Landsat Tier-1 scene covers this corridor between Nov {year - 1} and Apr {year}")

    def screen(item):
        return item, inside & landsat_clear(read_on_grid(item.assets["qa_pixel"].href, grid))

    usable = [r for r in _pmap(_safe(screen), items[:30]) if r and r[1].sum() / n >= min_clear]
    chosen = choose_landsat_scenes(usable, max_scenes)
    if not chosen:
        raise RuntimeError(f"Every Landsat scene between Nov {year - 1} and Apr {year} is cloudy over this corridor")

    def indices(pair):
        item, clear = pair
        platform = item.properties.get("platform")
        b = dict(_pmap(lambda a: (a, landsat_reflectance(read_on_grid(item.assets[a].href, grid), platform, a)),
                       ["green", "red", "nir08", "swir16"]))
        idx = {"mndwi": normalized_difference(b["green"], b["swir16"]),
               "ndvi": normalized_difference(b["nir08"], b["red"])}
        for v in idx.values():
            v[~clear] = np.nan
        return item, idx

    scenes = [r for r in _pmap(_safe(indices), chosen, io=False) if r]
    if not scenes:
        raise RuntimeError(f"Landsat scenes for {year} could not be read")
    ndvi = np.stack([s[1]["ndvi"] for s in scenes])
    stats = {"ndvi_max": _nanstat(np.nanmax, ndvi),  # pixels never seen clear stay NaN
             "mndwi_median": _nanstat(np.nanmedian, np.stack([s[1]["mndwi"] for s in scenes]))}
    months = {s[0].datetime.month for s in scenes}
    return LandsatComposite(
        year=year, season="rabi Dec-Mar" if months <= {12, 1, 2, 3} else "rabi Nov-Apr (widened)",
        grid=grid, indices=stats, n_obs=np.isfinite(ndvi).sum(axis=0),
        dates=[s[0].datetime.date().isoformat() for s in scenes],
        platforms=", ".join(sorted({s[0].properties.get("platform", "?") for s in scenes})),
        peak_scenes=sum(_in_peak(s[0].datetime) for s in scenes),
        slc_off_scenes=sum(_slc_off(s[0]) for s in scenes),
        scene_ids=[s[0].id for s in scenes])


def composite_quality(m):
    """('good' | 'fair' | 'poor', reasons) for a season summary from `composite_shares`.

    Poor: no clear scene in the mid-January-to-March peak (vegetation is then under-counted by tens of points)
    or under 60% of the area observed. Fair: SLC-off scenes only, fewer than three scenes, a widened season or
    under 80% observed. Over the Nakatiya the Dec-Mar archive for 2004-2008 and 2012-2013 holds Landsat 7
    SLC-off scenes only, so those seasons come out fair at best.
    """
    poor, fair = [], []
    if not m["peak_scenes"]:
        poor.append("no clear scene mid-Jan to March")
    if m["coverage_pct"] < 60:
        poor.append(f"{m['coverage_pct']:.0f}% of the area observed")
    elif m["coverage_pct"] < 80:
        fair.append(f"{m['coverage_pct']:.0f}% of the area observed")
    if m["slc_off_scenes"] and m["slc_off_scenes"] == m["scenes"]:
        fair.append("Landsat 7 SLC-off scenes only")
    if m["scenes"] < 3:
        fair.append(f"{m['scenes']} scene(s)")
    if m["season"] != "rabi Dec-Mar":
        fair.append("season widened to Nov-Apr")
    return ("poor" if poor else "fair" if fair else "good"), "; ".join(poor + fair)


def composite_shares(comp, geometry):
    """Water / vegetated / non-green shares (%) of a season composite inside a lon/lat geometry.

    Water: median MNDWI > 0.05 (open water in at least half the clear looks). Vegetated: greenest NDVI >= 0.50
    at some point in the season. Non-green: never green (greenest NDVI < 0.40) and not water, i.e. buildings,
    roads, brick kilns, sand and fallow land alike. Against the 10 m Impact Observatory map (urban reach 2017 and 2023,
    rerun 2026-09-27) it is precise for urban built-up land (0.83-0.95) but misses tree-shaded settlement (recall
    0.48-0.60, and 0.01-0.20 on the rural reaches), and
    it swings 8-10 points between neighbouring years with crop calendars, so it is indicative only; built-up
    change comes from the validated settlement and land-cover products instead.
    """
    inside = geometry_pixels(comp.grid, geometry)
    n = int(inside.sum())
    ndvi_max, mndwi = comp.indices["ndvi_max"], comp.indices["mndwi_median"]
    valid = inside & np.isfinite(ndvi_max) & np.isfinite(mndwi)
    if not valid.any():
        raise RuntimeError(f"No clear Landsat observation inside the corridor for {comp.year}")
    water = mndwi > WATER_MNDWI
    share = lambda m: float(100 * (m & valid).sum() / valid.sum())
    out = {"year": comp.year, "season": comp.season,
           "water_pct": share(water),
           "vegetation_pct": share(~water & (ndvi_max >= GREEN_NDVI)),
           "nongreen_pct": share(~water & (ndvi_max < NEVER_GREEN_NDVI)),
           "scenes": comp.scenes, "peak_scenes": comp.peak_scenes, "slc_off_scenes": comp.slc_off_scenes,
           "dates": ", ".join(comp.dates), "platforms": comp.platforms,
           "median_clear_obs": float(np.median(comp.n_obs[valid])),
           "coverage_pct": round(100 * float(valid.sum()) / max(n, 1), 1),
           "pixels": int(valid.sum()), "scene_ids": ", ".join(comp.scene_ids)}
    out["quality"], out["quality_notes"] = composite_quality(out)
    return out


def landsat_season_composite_metrics(geometry, year, max_scenes=8, min_clear=0.3):
    """Water / vegetated / non-green shares inside a lon/lat geometry for one season."""
    return composite_shares(landsat_season_composite(geometry, year, max_scenes, min_clear), geometry)
