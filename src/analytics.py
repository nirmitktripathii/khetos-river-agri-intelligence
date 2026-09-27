"""Signals derived from EO metrics: change scores, the scouting queue, water stress and sensor agreement.

Every output is an inspection signal that ranks where to look, not a diagnosis.
"""
import logging
import math
import warnings

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import transform as shp_transform
from sklearn.ensemble import IsolationForest

from src.eo import (latest_clear_s2, latest_cropland, latest_s1_pair, previous_clear_s2, s1_scene_metrics,
                    scene_metrics)
from src.river import river_corridor_geometry, river_distance_m

log = logging.getLogger(__name__)

PRIORITY_ORDER = ["HIGH", "WATCH", "LOW", "INSUFFICIENT DATA", "NOT CROPLAND"]


def field_scan(bbox, geometry=None, months=6, max_cloud_pct=35):
    """Latest clear Sentinel-2 scene over an area (or a drawn field inside it) and the clear scene before it.

    Returns the metrics of both scenes over clear cropland (see eo.scene_metrics) and, for the map, NDVI, NDMI and
    NDVI change on the scene grid with every pixel that is not clear set to NaN.
    """
    scene = latest_clear_s2(bbox, geometry=geometry, months=months, max_cloud_pct=max_cloud_pct)
    prev = previous_clear_s2(scene, bbox, max_cloud_pct)
    try:
        crop, crop_year = latest_cropland(scene.grid)
    except Exception as exc:
        log.warning("Cropland mask unavailable: %s", exc)
        crop, crop_year = None, None
    masked = lambda a, keep: np.where(keep, a, np.nan).astype("float32")
    return {"latest": scene_metrics(scene, crop),
            "previous": scene_metrics(prev, crop) if prev is not None else None,
            "ndvi": masked(scene.ndvi, scene.clear), "ndmi": masked(scene.ndmi, scene.clear),
            "ndvi_change": masked(scene.ndvi - prev.ndvi, scene.clear & prev.clear) if prev is not None else None,
            "grid": scene.grid, "cropland_year": crop_year}


def radar_pair(bbox):
    """Sentinel-1 metrics for the latest pass over the area and the previous pass on the same relative orbit
    (None when there is none)."""
    latest, prev = latest_s1_pair(bbox)
    return s1_scene_metrics(latest, bbox), (s1_scene_metrics(prev, bbox) if prev is not None else None)


def _clamp(x, lo=0.0, hi=1.0):
    return float(max(lo, min(hi, x)))


def _finite(x):
    return x is not None and np.isfinite(x)


def _min_finite(*values):
    vals = [v for v in values if _finite(v)]
    return min(vals) if vals else float("nan")


def robust_z(value, reference, min_n=5):
    """(value - median) / (1.4826 * MAD) against a reference sample; NaN when the sample is too small or flat."""
    ref = np.asarray(reference, dtype=float)
    ref = ref[np.isfinite(ref)]
    if ref.size < min_n or not _finite(value):
        return float("nan")
    med = np.median(ref)
    mad = 1.4826 * np.median(np.abs(ref - med))
    return float((value - med) / mad) if mad > 1e-9 else float("nan")


def anomaly_score(features):
    """Magnitude heuristic (0-100, 50 = no change) used when there is too little history for a z-score."""
    ndvi = features.get("ndvi_change", 0.0) or 0.0
    ndmi = features.get("ndmi_change", 0.0) or 0.0
    raw = (-ndvi * 100) * 0.55 + (-ndmi * 100) * 0.45
    return _clamp(50.0 + 12.0 * raw, 0.0, 100.0)


def change_signal(series):
    """Latest scene-to-scene NDVI/NDMI move, scored against this AOI's own earlier moves."""
    s = series.dropna(subset=["ndvi", "ndmi"])
    if len(s) < 2:
        return None
    latest, prev = s.iloc[-1], s.iloc[-2]
    d_ndvi, d_ndmi = float(latest.ndvi - prev.ndvi), float(latest.ndmi - prev.ndmi)
    z_ndvi = robust_z(d_ndvi, s["ndvi"].diff().iloc[1:-1])
    z_ndmi = robust_z(d_ndmi, s["ndmi"].diff().iloc[1:-1])
    if _finite(z_ndvi) and _finite(z_ndmi):
        score = _clamp(50 - 15 * (0.55 * z_ndvi + 0.45 * z_ndmi), 0, 100)
        method = f"robust z-score against {len(s) - 2} earlier scene-to-scene changes in this AOI"
    else:
        score = anomaly_score({"ndvi_change": d_ndvi, "ndmi_change": d_ndmi})
        method = "magnitude heuristic (fewer than 7 clear scenes, so no z-score)"
    if d_ndvi <= -0.05 or d_ndmi <= -0.05:
        label = "UNUSUAL DECLINE" if _min_finite(z_ndvi, z_ndmi) <= -2 else "DECLINE"
    elif d_ndvi >= 0.05:
        label = "GREENING"
    else:
        label = "STABLE"
    return {"label": label, "score": score, "method": method,
            "latest_date": pd.Timestamp(latest.date).date().isoformat(),
            "previous_date": pd.Timestamp(prev.date).date().isoformat(),
            "ndvi": float(latest.ndvi), "ndmi": float(latest.ndmi),
            "ndvi_change": d_ndvi, "ndmi_change": d_ndmi, "z_ndvi": z_ndvi, "z_ndmi": z_ndmi}


# --------------------------------------------------------------------------------- scouting queue

def _median(a):
    a = a[np.isfinite(a)]
    return float(np.median(a)) if a.size else float("nan")


def _fmt(v, spec="+.2f"):
    return format(v, spec) if _finite(v) else "n/a"


def make_scouting_grid(bbox, months=6, max_cloud_pct=35, cell_m=400, max_cells_side=20,
                       min_crop=0.3, min_clear=0.5, geometry=None):
    """Rank ~400 m cells of the AOI for field scouting.

    Features per cell (cropland pixels only): NDVI and NDMI of the latest clear Sentinel-2 scene and their
    change since the previous clear scene. Cells are scored with robust z-scores across the AOI and,
    with 12+ eligible cells, an Isolation Forest. HIGH needs both an outlier and a downward (stress) move.
    `geometry` (lon/lat) limits the analysis to e.g. a river corridor inside `bbox`.
    """
    scene = latest_clear_s2(bbox, geometry=geometry, months=months, max_cloud_pct=max_cloud_pct, res=10)
    prev = previous_clear_s2(scene, bbox, max_cloud_pct)
    grid, aoi = scene.grid, scene.aoi
    try:
        crop, crop_year = latest_cropland(grid)
    except Exception:
        crop, crop_year = None, None
    span_m = max(grid.width, grid.height) * grid.res
    cell = max(cell_m, math.ceil(span_m / max_cells_side / 100) * 100)
    k = max(1, round(cell / grid.res))
    ndvi, ndmi = scene.ndvi, scene.ndmi
    if prev is not None:
        d_ndvi, d_ndmi = ndvi - prev.ndvi, ndmi - prev.ndmi
    to_ll = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform
    left, _, _, top = grid.bounds
    clip = shape(geometry) if geometry is not None else None

    rows, polys = [], []
    for i, r0 in enumerate(range(0, grid.height, k)):
        for j, c0 in enumerate(range(0, grid.width, k)):
            sl = (slice(r0, r0 + k), slice(c0, c0 + k))
            n_aoi = int(aoi[sl].sum())
            if not n_aoi:
                continue
            h, w = aoi[sl].shape
            cell_poly = shp_transform(to_ll, box(left + c0 * grid.res, top - (r0 + h) * grid.res,
                                                 left + (c0 + w) * grid.res, top - r0 * grid.res))
            if clip is not None:
                cell_poly = cell_poly.intersection(clip)
            ok = scene.clear[sl] & np.isfinite(ndvi[sl]) & np.isfinite(ndmi[sl])
            crop_frac = float((crop[sl] & aoi[sl]).sum() / n_aoi) if crop is not None else float("nan")
            if crop is not None and int((ok & crop[sl]).sum()) >= 10:
                ok = ok & crop[sl]
            row = {"cell": f"R{i + 1:02d}-C{j + 1:02d}", "lat": cell_poly.centroid.y, "lon": cell_poly.centroid.x,
                   "cropland_pct": round(100 * crop_frac, 1) if _finite(crop_frac) else float("nan"),
                   "clear_pct": round(100 * float(scene.clear[sl].sum() / n_aoi), 1),
                   "ndvi": _median(ndvi[sl][ok]), "ndmi": _median(ndmi[sl][ok])}
            if prev is not None:
                both = ok & prev.clear[sl]
                row["prev_clear_pct"] = round(100 * float(prev.clear[sl].sum() / n_aoi), 1)
                row["d_ndvi"] = _median(d_ndvi[sl][both])
                row["d_ndmi"] = _median(d_ndmi[sl][both])
            rows.append(row)
            polys.append(cell_poly)

    df = pd.DataFrame(rows)
    eligible = (df["cropland_pct"] >= 100 * min_crop) if crop is not None else pd.Series(True, index=df.index)
    enough = df["clear_pct"] >= 100 * min_clear
    if prev is not None:
        enough &= df["prev_clear_pct"] >= 100 * min_clear
    features = ["ndvi", "ndmi"] + (["d_ndvi", "d_ndmi"] if prev is not None else [])
    enough &= df[features].notna().all(axis=1)
    df["priority"] = np.where(~eligible, "NOT CROPLAND", np.where(~enough, "INSUFFICIENT DATA", "LOW"))
    cand = df.index[eligible & enough]

    for f in features:
        df[f"z_{f}"] = np.nan
        df.loc[cand, f"z_{f}"] = [robust_z(v, df.loc[cand, f]) for v in df.loc[cand, f]]
    df["outlier"] = False
    df["anomaly_strength"] = np.nan
    model = "rule-based robust z-scores (fewer than 12 eligible cells)"
    if len(cand) >= 12:
        X = df.loc[cand, [f"z_{f}" for f in features]].fillna(0.0).to_numpy()
        iso = IsolationForest(n_estimators=200, contamination="auto", random_state=42).fit(X)
        df.loc[cand, "outlier"] = iso.predict(X) == -1
        df.loc[cand, "anomaly_strength"] = -iso.decision_function(X)
        model = f"Isolation Forest (200 trees) on robust z-scores of {', '.join(features)}"

    stress_cols = ["z_d_ndvi", "z_d_ndmi"] if prev is not None else ["z_ndvi", "z_ndmi"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        df["stress_z"] = df[stress_cols].min(axis=1, skipna=True)
    stress, strong = df["stress_z"] <= -1.5, df["stress_z"] <= -2.5
    in_cand = df.index.isin(cand)
    if len(cand) >= 12:
        high, watch = df["outlier"] & stress, df["outlier"] | stress
    else:
        high, watch = strong, stress
    df.loc[in_cand & watch, "priority"] = "WATCH"
    df.loc[in_cand & high, "priority"] = "HIGH"

    since = f" since {prev.date.isoformat()}" if prev is not None else ""

    def reason(r):
        if r.priority == "NOT CROPLAND":
            return f"only {_fmt(r.cropland_pct, '.0f')}% cropland (IO land cover {crop_year})"
        if r.priority == "INSUFFICIENT DATA":
            return "too few clear pixels in one of the scenes"
        parts = [f"NDVI {_fmt(r.ndvi, '.2f')} (z {_fmt(r.z_ndvi, '+.1f')})",
                 f"NDMI {_fmt(r.ndmi, '.2f')} (z {_fmt(r.z_ndmi, '+.1f')})"]
        if prev is not None:
            parts += [f"ΔNDVI {_fmt(r.d_ndvi)}{since} (z {_fmt(r.z_d_ndvi, '+.1f')})",
                      f"ΔNDMI {_fmt(r.d_ndmi)} (z {_fmt(r.z_d_ndmi, '+.1f')})"]
        if r.outlier:
            parts.append("Isolation-Forest outlier")
        return "; ".join(parts)

    df["reason"] = df.apply(reason, axis=1)
    df["_order"] = df["priority"].map({p: i for i, p in enumerate(PRIORITY_ORDER)})
    df = df.sort_values(["_order", "stress_z", "anomaly_strength"], ascending=[True, True, False],
                        na_position="last")
    order = df.index.to_list()
    df = df.drop(columns="_order").reset_index(drop=True)
    features_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(polys[idx]),
         "properties": {"cell": rows[idx]["cell"], "priority": df.loc[n, "priority"], "reason": df.loc[n, "reason"]}}
        for n, idx in enumerate(order)]}
    return {"table": df, "geojson": features_fc, "scene": scene.id, "date": scene.date.isoformat(),
            "previous_scene": prev.id if prev is not None else None,
            "previous_date": prev.date.isoformat() if prev is not None else None,
            "cell_m": cell, "model": model, "cropland_source": f"Impact Observatory 10 m land cover {crop_year}"
            if crop is not None else "unavailable (all cells treated as eligible)"}


def fields_near_river(distance_m=500, point=None, radius_km=None, months=6, max_cloud_pct=35, river=None):
    """Scouting cells within `distance_m` of the river centreline (the whole mapped river, or the reach
    within `radius_km` of `point`), with HIGH / WATCH cells as the answer to "which fields near the river
    show unusual change"."""
    corridor = river_corridor_geometry(distance_m, point, radius_km, river)
    res = make_scouting_grid(shape(corridor).bounds, months, max_cloud_pct, cell_m=250, max_cells_side=60,
                             geometry=corridor)
    table = res["table"]
    table["river_distance_m"] = np.round(river_distance_m([Point(xy) for xy in zip(table["lon"], table["lat"])],
                                                          river))
    res.update({"corridor": corridor, "distance_m": distance_m,
                "flagged": table[table["priority"].isin(["HIGH", "WATCH"])].reset_index(drop=True)})
    return res


# --------------------------------------------------------------------------------- water & fusion

def water_stress_signal(ndmi, rain_14d_mm, et0_14d_mm=None):
    """0-100 inspection signal: canopy dryness from NDMI blended with the 14-day atmospheric water balance
    (reference evapotranspiration minus rainfall). Not an irrigation prescription."""
    if not _finite(ndmi):
        return None
    canopy = _clamp((0.5 - ndmi) / 0.7)
    rain = rain_14d_mm if _finite(rain_14d_mm) else 0.0
    if _finite(et0_14d_mm):
        deficit_mm = et0_14d_mm - rain
        balance = _clamp(deficit_mm / 60.0)
    else:
        deficit_mm = float("nan")
        balance = _clamp(1 - rain / 50.0)
    return {"water_stress_signal": round(100 * (0.6 * canopy + 0.4 * balance), 1),
            "canopy_dryness": round(canopy, 3), "water_balance_term": round(balance, 3),
            "water_deficit_14d_mm": round(deficit_mm, 1) if _finite(deficit_mm) else None}


def _direction(delta, threshold):
    if not _finite(delta):
        return None
    return "down" if delta <= -threshold else ("up" if delta >= threshold else "flat")


def fusion_assessment(opt_now, opt_prev, sar_now, sar_prev):
    """Do Sentinel-2 (NDVI) and Sentinel-1 (VH backscatter, same relative orbit) agree on the change?"""
    d_ndvi = opt_now["ndvi"] - opt_prev["ndvi"] if opt_prev else float("nan")
    d_vh = sar_now["vh_db"] - sar_prev["vh_db"] if sar_prev else float("nan")
    d_rvi = sar_now["rvi"] - sar_prev["rvi"] if sar_prev else float("nan")
    o, s = _direction(d_ndvi, 0.05), _direction(d_vh, 1.0)
    if o is None or s is None:
        label = "INCOMPLETE · need two clear optical scenes and two same-orbit radar passes"
    elif o == s:
        label = {"down": "AGREE · vegetation decline", "up": "AGREE · vegetation growth",
                 "flat": "AGREE · stable"}[o]
    else:
        label = "DISAGREE · verify on the ground"
    gap = abs((pd.Timestamp(opt_now["date"]) - pd.Timestamp(sar_now["date"])).days)
    return {"label": label, "optical_direction": o, "radar_direction": s,
            "ndvi_change": d_ndvi, "vh_change_db": d_vh, "rvi_change": d_rvi, "optical_radar_gap_days": gap}
