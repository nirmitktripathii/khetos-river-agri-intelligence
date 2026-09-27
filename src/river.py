"""Nakatiya river geometry and river-corridor change analysis.

OpenStreetMap maps the Nakatiya ("Nakatia Nadi") from a head east of Bhojipura, across the south-east edge of
Bareilly city, to the Ramganga south of the city. Local reporting places its source further north, at
Dehnagar (Deenagar) village in the Baheri area; that upper course is not mapped and stays unverified here.
A bundled OpenStreetMap extract is the default geometry so the app never depends on a live Overpass call;
a refresh from Overpass is available on demand.

Outputs are change flags / encroachment-risk signals for follow-up, never legal determinations.
"""
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import shapely
from pyproj import Geod, Transformer
from rasterio.features import shapes
from shapely.geometry import Point, mapping, shape
from shapely.ops import nearest_points, transform as shp_transform, unary_union

from src.config import DATA_DIR, NAKATIYA_CONFLUENCE, OVERPASS_URLS, REGION_BBOX
from src.eo import (LANDSAT_FIRST_YEAR, LULC_YEARS, WSF_YEARS, built_extent_on_grid, composite_shares,
                    geometry_pixels, jrc_water_summary, landsat_season_composite, landsat_season_composite_metrics,
                    latest_rabi_year, latest_s1_pair, lulc_fractions, lulc_on_grid, make_grid, run_jobs,
                    s1_scene_metrics, s2_window_greenest, settlement_shares, wsf_evolution_on_grid)

log = logging.getLogger(__name__)

NAKATIYA_FILE = DATA_DIR / "nakatia_osm.geojson"
QILA_FILE = DATA_DIR / "qila_candidate_osm.geojson"
MAIN_STEM_WAYS = {355365315, 487806973, 562114087}  # head east of Bhojipura -> Ramganga confluence
USER_AGENT = {"User-Agent": "KhetOS-PoC/1.0 (river corridor research)"}
OVERPASS_QUERY = """[out:json][timeout:60];
way["waterway"~"^(river|stream)$"]["name"~"Nakat(i)?ya|Nakatia|Naktiya|Nakkatiya",i]({s},{w},{n},{e});
out geom tags;"""

# Reported by Amar Ujala (31 Aug 2026). Neither is verified against a survey or the plan document.
REPORTED_SOURCE = "Dehnagar (Deenagar) village, Baheri area"
GREEN_BELT_M = 30  # Master Plan 2031 green belt along the Nakatiya and Kila, as reported: a reference buffer only
UPSTREAM_LEAD_WAY = 488077884  # unnamed OSM stream ~0.5 km north of the mapped head: a lead, not a match

CHANGE_THRESHOLD_PP = 3.0  # built-up and water change (percentage points) that raises a flag
# The Landsat vegetated share of a single rabi season differs from the next season's by a median 8-10 points and
# by up to 45 (late, hazy or striped scenes; 1990-2026 on the urban and upper reaches). Each year of a comparison
# is therefore the median of its season and its neighbours (_season_sets), and vegetation change is flagged from
# 15 points: in the upper reach, where almost nothing was built, such medians 3-5 years apart differed by at most
# 14 points.
VEGETATION_THRESHOLD_PP = 15.0
MIN_USABLE_SEASONS = 2  # usable seasons each end of a comparison needs before water or vegetation is flagged
# The vegetated share of matched 1 Jan-31 Mar Sentinel-2 windows moved at most 4 points year on year on the urban
# and upper reaches (2024-2026, four pairs); matched Apr-Jun windows moved 7-12 points and Jul-Sep windows up to 18,
# with sowing dates and cloud (scripts/calibration). Riparian health therefore compares Jan-Mar windows only,
# flagged from 10 points.
RIPARIAN_THRESHOLD_PP = 10.0

_TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True).transform
_TO_LL = Transformer.from_crs("EPSG:32644", "EPSG:4326", always_xy=True).transform
_GEOD = Geod(ellps="WGS84")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_nakatiya():
    """Bundled OSM ways for the Nakatiya (FeatureCollection with provenance keys)."""
    return _load(NAKATIYA_FILE)


def load_qila_candidate():
    """Unnamed OSM river ways that match the Qila's described course (unverified identification)."""
    return _load(QILA_FILE)


def fetch_nakatiya_osm(timeout=90):
    """Live refresh from Overpass, trying each mirror. Raises on failure; failures are never cached."""
    w, s, e, n = REGION_BBOX
    query = OVERPASS_QUERY.format(s=s, w=w, n=n, e=e)
    last = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data=query.encode(), timeout=timeout, headers=USER_AGENT)
            r.raise_for_status()
            feats = [{"type": "Feature",
                      "properties": {"osm_way": el["id"], **el.get("tags", {})},
                      "geometry": {"type": "LineString",
                                   "coordinates": [(p["lon"], p["lat"]) for p in el["geometry"]]}}
                     for el in r.json().get("elements", []) if len(el.get("geometry") or []) >= 2]
            if feats:
                return {"type": "FeatureCollection", "retrieved": date.today().isoformat(), "source": url,
                        "attribution": "(c) OpenStreetMap contributors, ODbL 1.0", "features": feats}
            last = RuntimeError("the query returned no Nakatiya ways")
        except (requests.RequestException, ValueError, KeyError) as exc:
            last = exc
            log.warning("Overpass mirror %s failed: %s", url, exc)
    raise RuntimeError(f"Overpass refresh failed on every mirror ({last}); keeping the bundled geometry")


def river_lines(fc, ways=None):
    """Merge a waterway FeatureCollection into one (Multi)LineString, keeping every part."""
    lines = [shape(f["geometry"]) for f in fc["features"]
             if ways is None or f["properties"].get("osm_way") in ways]
    return shapely.line_merge(unary_union(lines))


def nakatiya_geometry(fc=None):
    return mapping(river_lines(fc or load_nakatiya()))


def length_km(geom):
    return _GEOD.geometry_length(shape(geom) if isinstance(geom, dict) else geom) / 1000


def nakatiya_course(fc=None):
    """Mapped course summary: lengths, head, the Ramganga confluence of the main stem, and the reported source
    upstream of the mapped head."""
    fc = fc or load_nakatiya()
    main = river_lines(fc, MAIN_STEM_WAYS)
    total = length_km(river_lines(fc))
    facts = {"ways": len(fc["features"]), "total_km": round(total, 1), "retrieved": fc.get("retrieved"),
             "source": fc.get("source", "OpenStreetMap")}
    if main.geom_type == "LineString":
        a, b = Point(main.coords[0]), Point(main.coords[-1])
        mouth_ref = Point(NAKATIYA_CONFLUENCE)
        head, mouth = (a, b) if a.distance(mouth_ref) > b.distance(mouth_ref) else (b, a)
        facts.update({"main_stem_km": round(length_km(main), 1), "head": (round(head.x, 5), round(head.y, 5)),
                      "confluence": (round(mouth.x, 5), round(mouth.y, 5)),
                      "branches_km": round(total - length_km(main), 1)})
    facts.update({"reported_source": f"{REPORTED_SOURCE} (Amar Ujala, 31 Aug 2026)",
                  "upstream_status": "Unverified: OpenStreetMap maps no Nakatiya north of the head east of "
                                     "Bhojipura, so the course from the reported source to that head is unknown."})
    return facts


def river_corridor_geometry(buffer_m=250, point=None, radius_km=None, river=None):
    """Corridor polygon (lon/lat GeoJSON): the centreline within `radius_km` of `point`, buffered by `buffer_m`.

    With no point or radius the whole mapped river is used. A point off the river is snapped to the nearest
    river location first. Buffers are measured from the OSM centreline, not from surveyed banks.
    """
    g_utm = shp_transform(_TO_UTM, shape(river or nakatiya_geometry()))
    if point is None or not radius_km:
        return mapping(shp_transform(_TO_LL, g_utm.buffer(buffer_m)))
    centre = shp_transform(_TO_UTM, Point(point))
    zone = centre.buffer(radius_km * 1000)
    reach = g_utm.intersection(zone)
    if reach.is_empty:  # point is off the river: centre the reach on the nearest river location
        centre = nearest_points(centre, g_utm)[1]
        zone = centre.buffer(radius_km * 1000)
        reach = g_utm.intersection(zone)
    corridor = reach.buffer(buffer_m).intersection(zone.buffer(buffer_m))
    return mapping(shp_transform(_TO_LL, corridor))


def river_distance_m(geometries, river=None):
    """Shortest distance (m) from each lon/lat geometry (GeoJSON or shapely) to the river centreline."""
    g_utm = shp_transform(_TO_UTM, shape(river or nakatiya_geometry()))
    out = []
    for g in geometries:
        g = shape(g) if isinstance(g, dict) else g
        out.append(float(shp_transform(_TO_UTM, g).distance(g_utm)))
    return out


def _area_km2(geom):
    return shp_transform(_TO_UTM, shape(geom)).area / 1e6


# ------------------------------------------------------------------------------------ change analysis

METHOD = ("Built-up change comes only from validated products, each over the part of the period it covers: DLR "
          "World Settlement Footprint Evolution (30 m, settled extent by year, 1985-2015) and Impact Observatory "
          "10 m land cover (built area, 2017-2025). The two products define built-up land differently, so they are "
          "never differenced against each other. Water and vegetation come from Landsat Collection 2 Tier-1 "
          "surface reflectance, cloud-masked per pixel, for the rabi season of each year (Dec-Mar, widened to "
          "Nov-Apr only when clear scenes are scarce), with TM/ETM+ harmonised to OLI (Roy et al. 2016): water = "
          "median MNDWI > 0.05, vegetated = greenest NDVI >= 0.50, non-green = greenest NDVI < 0.40 (built, bare "
          "or fallow alike, so indicative only). Single seasons swing 8-10 points between neighbouring years, so "
          "each year of a comparison is the median of its season and its neighbours, using only seasons with two "
          "or more clear mid-January-to-March scenes and not rated poor. Flags: built-up or water change beyond "
          "±3 percentage points, vegetation beyond ±15 points, and water or vegetation only when each end rests "
          "on two or more usable seasons. The Nakatiya's channel is narrow (under 3% of a 250 m corridor is water "
          "in the 10 m land-cover map), so Landsat water shares mostly reflect ponds, pools and floodplain water.")


def default_timeline_years(today=None):
    """Roughly five-yearly rabi seasons from 1991 and the latest complete one, chosen where the Dec-Mar archive
    over the Nakatiya holds two or more clear peak-season scenes from a working sensor: 1990, 1992-1993, 1995
    and 2002 have one or two scenes at most, and 2004-2008 and 2012 have Landsat 7 SLC-off scenes only."""
    return tuple(sorted({1991, 1994, 1999, 2003, 2009, 2014, 2020, latest_rabi_year(today)}))


def _pp(b, a, key):
    return round(float(b[key] - a[key]), 1) if a and b and key in a and key in b else float("nan")


MIN_LANDSAT_BUFFER_M = 45  # 1.5 Landsat pixels; narrower corridors get Landsat shares but no Landsat flags


def _narrow_caveat(buffers):
    narrow = [str(b) for b in buffers if b < MIN_LANDSAT_BUFFER_M]
    if not narrow:
        return []
    return [f"Buffers under {MIN_LANDSAT_BUFFER_M} m ({', '.join(narrow)} m) are narrower than 1.5 Landsat pixels "
            "and within typical OSM centreline error: their Landsat water and vegetation shares are indicative only "
            "and raise no flags"]


def _period(change):
    return f"{change['period'][0]}→{change['period'][1]}" if change else None


def _result(job, what):
    try:
        return job.result()
    except Exception as exc:
        log.warning("%s unavailable: %s", what, exc)
        return None


def _check_years(year_a, year_b):
    latest = latest_rabi_year()
    year_a = int(year_a)
    year_b = int(year_b) if year_b is not None else latest
    if year_a >= year_b:
        raise ValueError("The earlier year must come before the later year")
    if year_a < LANDSAT_FIRST_YEAR:
        raise ValueError(f"The Landsat record over the Nakatiya starts with the {LANDSAT_FIRST_YEAR} rabi season")
    if year_b > latest:
        raise ValueError(f"The latest complete rabi season is {latest}")
    return year_a, year_b


def _season_sets(year_a, year_b):
    """The rabi seasons summarised for each end of year_a→year_b: the year and its neighbours, each kept on its
    own side of the midpoint so the two ends never share a season, within the Landsat record."""
    last, mid = latest_rabi_year(), (year_a + year_b) / 2
    return ([y for y in (year_a - 1, year_a, year_a + 1) if LANDSAT_FIRST_YEAR <= y < mid],
            [y for y in (year_b - 1, year_b, year_b + 1) if mid < y <= last])


def _usable(m):
    """A season whose shares are trusted: not rated poor, and two or more clear scenes in the mid-January-to-March
    peak (with fewer, the vegetated share can run tens of points low)."""
    return m["quality"] != "poor" and m["peak_scenes"] >= 2


def _combine(year, seasons):
    """One end of a comparison: the median shares over the usable seasons around `year`, or None."""
    ok = [m for m in seasons if m and _usable(m)]
    if not ok:
        return None
    med = lambda k: round(float(np.median([m[k] for m in ok])), 1)
    return {"year": year, "seasons_used": [m["year"] for m in ok], "water_pct": med("water_pct"),
            "vegetation_pct": med("vegetation_pct"), "nongreen_pct": med("nongreen_pct"),
            "pixels": min(m["pixels"] for m in ok)}


def _season_caveats(seasons):
    """Caveats for the seasons of a comparison: {year: season summary, or None if it could not be read}."""
    left = [f"{y} ({m['quality']}, {m['peak_scenes']} peak-season scene(s))"
            for y, m in sorted(seasons.items()) if m and not _usable(m)]
    failed = [str(y) for y, m in sorted(seasons.items()) if not m]
    return ([f"Landsat seasons left out of the medians: {', '.join(left)}"] if left else []) + \
           ([f"Landsat seasons that could not be composited: {', '.join(failed)}"] if failed else [])


def _season_table(seasons, set_a, year_a, year_b):
    rows = [{"end": year_a if y in set_a else year_b, "used": _usable(m), **m}
            for y, m in sorted(seasons.items()) if m]
    return pd.DataFrame(rows)


def _built_periods(year_a, year_b):
    """The parts of year_a→year_b covered by WSF Evolution and by Impact Observatory, and the uncovered gaps."""
    wsf = (max(year_a, WSF_YEARS[0]), min(year_b, WSF_YEARS[1]))
    io = (max(year_a, LULC_YEARS[0]), min(year_b, LULC_YEARS[1]))
    gaps = []
    if year_a < LULC_YEARS[0] and year_b > WSF_YEARS[1]:
        gaps.append((max(year_a, WSF_YEARS[1]), min(year_b, LULC_YEARS[0])))
    if year_b > LULC_YEARS[1]:
        gaps.append((max(year_a, LULC_YEARS[1]), year_b))
    return (wsf if wsf[0] < wsf[1] else None), (io if io[0] < io[1] else None), gaps


def _settlement_change(geometry, period, grid):
    """Change in the WSF Evolution settled share over `period`, or None."""
    if not period:
        return None
    s = settlement_shares(geometry, period, grid)
    if len(s) < 2:
        return None
    return {"period": period, "from_pct": s[period[0]], "to_pct": s[period[1]],
            "change_pp": round(s[period[1]] - s[period[0]], 2)}


def _landcover_change(geometry, period, grid):
    """Change in the Impact Observatory built share over `period` (with both years' class shares), or None."""
    if not period:
        return None
    la, lb = lulc_fractions(geometry, period[0], grid), lulc_fractions(geometry, period[1], grid)
    if la is None or lb is None:
        return None
    return {"period": period, "from_pct": la["built_pct"], "to_pct": lb["built_pct"],
            "change_pp": round(lb["built_pct"] - la["built_pct"], 2), "table": pd.DataFrame([la, lb])}


def _assess(a, b, settlement=None, built=None, gaps=(), landsat_flags=True):
    """Change metrics, flags and caveats for one corridor.

    a, b: Landsat summaries for each end (see _combine) or None. settlement / built: validated built-up change
    over the part of the period each product covers (see _settlement_change and _landcover_change), or None.
    gaps: (from, to) years that no built-up product covers. landsat_flags: False for corridors too narrow for
    Landsat, whose shares are then reported without flags.
    """
    water, veg, nongreen = (_pp(b, a, k) for k in ("water_pct", "vegetation_pct", "nongreen_pct"))
    ends = [m for m in (a, b) if m]
    thin = [str(m["year"]) for m in ends if len(m["seasons_used"]) < MIN_USABLE_SEASONS]
    robust = len(ends) == 2 and not thin
    flags = []
    if settlement and settlement["change_pp"] > CHANGE_THRESHOLD_PP:
        flags.append("SETTLEMENT GROWTH")
    if built and built["change_pp"] > CHANGE_THRESHOLD_PP:
        flags.append("BUILT-UP GROWTH")
    if landsat_flags and robust and veg <= -VEGETATION_THRESHOLD_PP:
        flags.append("VEGETATION LOSS")
    if landsat_flags and robust and water <= -CHANGE_THRESHOLD_PP:
        flags.append("WATER FOOTPRINT DECLINE")
    kinds = {"built-up" if f.endswith("GROWTH") else f for f in flags}
    measured = bool(settlement or built or ends)
    caveats = []
    if len(ends) < 2:
        caveats.append("Water and vegetation change unavailable: no usable Landsat season at one end")
    elif thin:
        caveats.append(f"Water and vegetation change are shown but not flagged: {' and '.join(thin)} rest(s) on a "
                       "single usable Landsat season")
    caveats += [f"{g0}→{g1}: no validated built-up map covers this part of the period" for g0, g1 in gaps]
    return {"settlement_change_pp": settlement["change_pp"] if settlement else float("nan"),
            "settlement_period": _period(settlement),
            "builtup_change_pp": built["change_pp"] if built else float("nan"),
            "builtup_period": _period(built),
            "vegetation_change_pp": veg, "water_change_pp": water, "nongreen_change_pp": nongreen,
            "change_flag": ("NO DATA" if not measured else "HIGH" if len(kinds) >= 2 else
                            "WATCH" if kinds else "STABLE"),
            "flags": flags, "caveats": caveats}


def river_land_change(point, radius_km=6, buffer_m=250, year_a=2017, year_b=None, river=None):
    """Change between two years inside a river corridor: validated built-up change (WSF Evolution and Impact
    Observatory, over the years each covers), Landsat water and vegetation, and JRC's 1984-2020 water history.
    `year_b` defaults to the latest complete rabi season."""
    year_a, year_b = _check_years(year_a, year_b)
    corridor = river_corridor_geometry(buffer_m, point, radius_km, river)
    grid = make_grid(shape(corridor).bounds, res=10, max_px=2048)
    wsf, io, gaps = _built_periods(year_a, year_b)
    set_a, set_b = _season_sets(year_a, year_b)
    fj, fs, fl, *fy = run_jobs((jrc_water_summary, corridor),
                               (_settlement_change, corridor, wsf, grid),
                               (_landcover_change, corridor, io, grid),
                               *[(landsat_season_composite_metrics, corridor, y) for y in set_a + set_b])
    seasons = {y: _result(f, f"Landsat {y}") for y, f in zip(set_a + set_b, fy)}
    a = _combine(year_a, [seasons[y] for y in set_a])
    b = _combine(year_b, [seasons[y] for y in set_b])
    settlement, built = _result(fs, "Settlement history"), _result(fl, "Land cover")
    out = {"year_a": year_a, "year_b": year_b, "buffer_m": buffer_m, "radius_km": radius_km,
           "corridor_area_km2": round(_area_km2(corridor), 2)}
    out.update(_assess(a, b, settlement, built, gaps, landsat_flags=buffer_m >= MIN_LANDSAT_BUFFER_M))
    out["caveats"] += _season_caveats(seasons) + _narrow_caveat([buffer_m])
    out.update({"landsat_ends": pd.DataFrame([m for m in (a, b) if m]),
                "table": _season_table(seasons, set_a, year_a, year_b),
                "landcover_table": built.pop("table") if built else None,
                "settlement": settlement, "jrc": _result(fj, "JRC surface water"), "corridor": corridor,
                "method": METHOD})
    return out


def river_buffer_ladder(point, radius_km=4, buffers=(10, 25, GREEN_BELT_M, 50, 100), year_a=2017, year_b=None,
                        river=None):
    """The same comparison for nested corridors of increasing width, from one read of each dataset.

    Narrow buffers (10-25 m) are below Landsat's 30 m pixel and within OSM centreline error; the 10 m land-cover
    map is the more meaningful source there. The 30 m row is the reported Master Plan green belt.
    """
    year_a, year_b = _check_years(year_a, year_b)
    buffers = sorted({int(b) for b in buffers})
    corridors = {b: river_corridor_geometry(b, point, radius_km, river) for b in buffers}
    widest = corridors[buffers[-1]]
    grid = make_grid(shape(widest).bounds, res=10, max_px=2048)
    wsf, io, gaps = _built_periods(year_a, year_b)
    set_a, set_b = _season_sets(year_a, year_b)
    comp_jobs = run_jobs(*[(landsat_season_composite, widest, y) for y in set_a + set_b])
    # Read the built-up products once, in parallel with Landsat; the nested buffers reuse the cached arrays.
    wsf_job = run_jobs((wsf_evolution_on_grid, grid))[0] if wsf else None
    io_jobs = run_jobs(*[(lulc_on_grid, grid, y) for y in io]) if io else []
    wsf_ok = wsf_job is not None and _result(wsf_job, "Settlement history") is not None
    io_ok = bool(io_jobs) and all(_result(f, "Land cover") is not None for f in io_jobs)
    comps = {y: _result(f, f"Landsat {y}") for y, f in zip(set_a + set_b, comp_jobs)}

    def shares(comp, g):
        try:
            return composite_shares(comp, g) if comp else None
        except RuntimeError as exc:  # no clear Landsat pixel inside a narrow buffer
            log.warning("%s", exc)
            return None

    rows, caveats = [], []
    for b in buffers:
        g = corridors[b]
        per = {y: shares(c, g) for y, c in comps.items()}
        sa, sb = _combine(year_a, [per[y] for y in set_a]), _combine(year_b, [per[y] for y in set_b])
        s = _settlement_change(g, wsf, grid) if wsf_ok else None
        lc = _landcover_change(g, io, grid) if io_ok else None
        r = _assess(sa, sb, s, lc, gaps, landsat_flags=b >= MIN_LANDSAT_BUFFER_M)
        caveats += [c for c in r["caveats"] if c not in caveats]
        row = {"buffer_m": b, "area_ha": round(100 * _area_km2(g), 1),
               "landsat_pixels": min(sa["pixels"], sb["pixels"]) if sa and sb else 0}
        if wsf:
            row["settlement_pp ({}→{})".format(*wsf)] = r["settlement_change_pp"]
        if io:
            row["built_up_pp ({}→{})".format(*io)] = r["builtup_change_pp"]
        row.update({f"vegetation_pp ({year_a}→{year_b})": r["vegetation_change_pp"],
                    f"water_pp ({year_a}→{year_b})": r["water_change_pp"],
                    "non_green_pp (indicative)": r["nongreen_change_pp"],
                    "flag": r["change_flag"], "flags": ", ".join(r["flags"]) or "-"})
        rows.append(row)
    widest_shares = {y: shares(c, widest) for y, c in comps.items()}
    caveats += _season_caveats(widest_shares) + _narrow_caveat(buffers)
    return {"table": pd.DataFrame(rows), "caveats": caveats, "corridors": corridors,
            "year_a": year_a, "year_b": year_b, "radius_km": radius_km,
            "settlement_period": "{}→{}".format(*wsf) if wsf_ok else None,
            "builtup_period": "{}→{}".format(*io) if io_ok else None,
            "seasons": _season_table(widest_shares, set_a, year_a, year_b),
            "method": METHOD}


def river_timeline(point, radius_km=4, buffer_m=100, years=None, river=None, progress=None):
    """History of one corridor: Landsat water / vegetation / non-green shares per rabi season with a quality
    grade, the settled share for every year 1985-2015 (WSF Evolution) and the 10 m land-cover classes for every
    year 2017-2025. `progress(done, total)` is called as Landsat seasons complete."""
    years = sorted({int(y) for y in (years or default_timeline_years())})
    corridor = river_corridor_geometry(buffer_m, point, radius_km, river)
    grid = make_grid(shape(corridor).bounds, res=10, max_px=2048)
    lulc_years = range(LULC_YEARS[0], LULC_YEARS[1] + 1)
    settle_job = run_jobs((settlement_shares, corridor, range(WSF_YEARS[0], WSF_YEARS[1] + 1), grid))[0]
    lulc_jobs = run_jobs(*[(lulc_fractions, corridor, y, grid) for y in lulc_years])
    jobs = run_jobs(*[(landsat_season_composite_metrics, corridor, y, 6) for y in years])
    rows, missing = [], []
    for n, (y, job) in enumerate(zip(years, jobs), 1):
        try:
            rows.append(job.result())
        except Exception as exc:
            missing.append(f"{y}: {exc}")
        if progress:
            progress(n, len(years))
    settled = _result(settle_job, "Settlement history") or {}
    lulc = [r for r in (_result(j, f"Land cover {y}") for y, j in zip(lulc_years, lulc_jobs)) if r]
    builtup = ([{"year": y, "built_pct": v, "source": "Settled (DLR WSF Evolution, 30 m)"} for y, v in settled.items()]
               + [{"year": r["year"], "built_pct": r["built_pct"], "source": "Built area (Impact Observatory, 10 m)"}
                  for r in lulc])
    caveats = ["Settled (WSF) and built area (Impact Observatory) are different definitions from different "
               "sensors; read each series on its own and do not join them across 2015-2017.",
               "WSF 1985 means settled in 1985 or earlier.",
               "Landsat sensors change in 1999 (ETM+) and 2013 (OLI); the bands are harmonised, but a residual "
               "difference of a few points between sensor generations cannot be ruled out."]
    landsat = pd.DataFrame(rows)
    if len(landsat):
        landsat = landsat.sort_values("year").reset_index(drop=True)
        landsat["used"] = [_usable(r) for r in landsat.to_dict("records")]
        left = landsat.loc[~landsat["used"], "year"].astype(str).tolist()
        if left:
            caveats.append(f"Landsat seasons left out of the trend (rated poor or under two clear peak-season "
                           f"scenes): {', '.join(left)}")
    return {"landsat": landsat, "builtup": pd.DataFrame(builtup, columns=["year", "built_pct", "source"]),
            "landcover": pd.DataFrame(lulc), "trend": vegetation_trend(landsat), "missing": missing,
            "caveats": caveats, "corridor": corridor, "buffer_m": buffer_m, "radius_km": radius_km,
            "method": METHOD}


def vegetation_trend(landsat):
    """Theil-Sen slope of the Landsat vegetated share over a timeline's usable seasons, with the two-sided
    Mann-Kendall p-value (seasons treated as independent) and the fitted line at the first and last season.
    Unlike a two-year comparison, one outlying season barely moves it."""
    d = landsat[landsat["used"]] if len(landsat) else landsat
    n = len(d)
    out = {"seasons": n, "first": None, "last": None, "slope_pp_per_decade": float("nan"),
           "change_pp": float("nan"), "p_value": float("nan"), "fitted_pct": None, "flag": "TOO FEW SEASONS"}
    if n < 5:
        return out
    x, y = d["year"].to_numpy(float), d["vegetation_pct"].to_numpy(float)
    i, j = np.triu_indices(n, 1)
    slope = float(np.median((y[j] - y[i]) / (x[j] - x[i])))
    intercept = float(np.median(y - slope * x))
    s = float(np.sign(y[j] - y[i]).sum())
    z = (s - np.sign(s)) / math.sqrt(n * (n - 1) * (2 * n + 5) / 18)
    p = math.erfc(abs(z) / math.sqrt(2))
    out.update({"first": int(x[0]), "last": int(x[-1]), "slope_pp_per_decade": round(10 * slope, 1),
                "change_pp": round(float(slope * (x[-1] - x[0])), 1), "p_value": round(p, 3),
                "fitted_pct": (round(intercept + slope * x[0], 1), round(intercept + slope * x[-1], 1)),
                "flag": ("SIGNIFICANT DECLINE" if slope < 0 else "SIGNIFICANT INCREASE") if p < 0.05
                else "NO SIGNIFICANT TREND"})
    return out


def corridor_built_extent(year, point=None, radius_km=None, buffer_m=100, river=None):
    """Built-up extent inside a corridor for one year of the time slider: settled land (WSF Evolution) up to
    2016, Impact Observatory built area from 2017 (see eo.built_extent_on_grid).

    Returns {"array": float32 grid, 1 where built and NaN elsewhere, "grid", "label", "built_pct", "corridor"}.
    """
    corridor = river_corridor_geometry(buffer_m, point, radius_km, river)
    grid = make_grid(shape(corridor).bounds, res=10, max_px=2048)
    inside = geometry_pixels(grid, corridor)
    built, label = built_extent_on_grid(grid, int(year))
    n = max(int(inside.sum()), 1)
    return {"array": np.where(inside & built, 1.0, np.nan).astype("float32"), "grid": grid, "label": label,
            "built_pct": round(100 * float((inside & built).sum()) / n, 1), "corridor": corridor, "year": int(year)}


def corridor_water_watch(point, radius_km=4, buffer_m=500, river=None, lookback_days=45):
    """Flood / water-expansion watch: open-water-like share of the corridor (Sentinel-1 VV < -18 dB) on the
    latest radar pass versus the previous pass on the same orbit. Radar sees through monsoon cloud."""
    corridor = river_corridor_geometry(buffer_m, point, radius_km, river)
    bbox = shape(corridor).bounds
    latest, prev = latest_s1_pair(bbox, lookback_days)
    now = s1_scene_metrics(latest, bbox, crop=False, geometry=corridor)
    before = s1_scene_metrics(prev, bbox, crop=False, geometry=corridor) if prev else None
    delta = now["water_like_pct"] - before["water_like_pct"] if before else float("nan")
    if before is None:
        flag = "SINGLE PASS ONLY"
    elif delta >= CHANGE_THRESHOLD_PP:
        flag = "WATER EXPANSION"
    elif delta <= -CHANGE_THRESHOLD_PP:
        flag = "WATER RECESSION"
    else:
        flag = "NO SIGNIFICANT CHANGE"
    return {"latest": now, "previous": before, "water_like_change_pp": delta, "flag": flag,
            "corridor": corridor, "buffer_m": buffer_m,
            "method": "Sentinel-1 RTC VV backscatter averaged to 30 m; pixels below -18 dB count as open-water-like "
                      "(calm water reflects the radar away). Flooded fields count as water; smooth tarmac or sand "
                      "can also fall below the threshold."}


GREEN_NDVI = 0.50  # vegetated: green at some point in the window
BARE_NOW_NDVI = 0.30  # never green in this year's window
WET_SWIR = 0.08  # SWIR-1 reflectance below this: water or waterlogged soil, not construction


def _nanmedian(x):
    x = x[np.isfinite(x)]
    return float(np.median(x)) if x.size else float("nan")


def _year_earlier(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, day=28)


def latest_rabi_peak_year(today=None):
    """The latest year whose 1 January-31 March window is complete."""
    today = today or date.today()
    return today.year if today.month >= 4 else today.year - 1


def _greenest_pair(corridor, start, end):
    """Greenest-NDVI composites of a window and of the same window a year earlier, on one 10 m grid."""
    bbox = shape(corridor).bounds
    grid = make_grid(bbox, res=10, max_px=1536)
    aoi = geometry_pixels(grid, corridor)
    ref = (_year_earlier(start), _year_earlier(end))
    f_now, f_ref = run_jobs((s2_window_greenest, bbox, grid, aoi, start, end),
                            (s2_window_greenest, bbox, grid, aoi, *ref))
    return f_now.result(), f_ref.result(), grid, aoi, ref


def riparian_health(point=None, radius_km=None, buffer_m=100, river=None, year=None):
    """Riparian vegetation health: the vegetated share (greenest Sentinel-2 NDVI >= 0.50) of the corridor in the
    rabi peak window, 1 January-31 March, of `year` (default: the latest complete one) against the year before,
    on pixels seen clear in both windows. Only rabi windows are compared because they are stable year on year
    (see RIPARIAN_THRESHOLD_PP); the long-term Landsat trend comes from river_timeline."""
    year = int(year or latest_rabi_peak_year())
    corridor = river_corridor_geometry(buffer_m, point, radius_km, river)
    f_lc = run_jobs((lulc_fractions, corridor, LULC_YEARS[1]))[0]
    start, end = datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year, 4, 1, tzinfo=timezone.utc)
    now, ref, grid, aoi, _ = _greenest_pair(corridor, start, end)
    landcover = _result(f_lc, "Land cover")
    both = aoi & (now["n_obs"] >= 1) & (ref["n_obs"] >= 1)
    n_both = int(both.sum())
    share = lambda m: round(100 * float((m & both).sum()) / n_both, 1) if n_both else float("nan")
    veg_now, veg_ref = share(now["ndvi_max"] >= GREEN_NDVI), share(ref["ndvi_max"] >= GREEN_NDVI)
    d_veg = round(veg_now - veg_ref, 1)
    compared = round(100 * n_both / max(int(aoi.sum()), 1), 1)
    flag = ("TOO FEW CLEAR LOOKS" if compared < 50 else "GREENNESS DECLINE" if d_veg <= -RIPARIAN_THRESHOLD_PP
            else "GREENING" if d_veg >= RIPARIAN_THRESHOLD_PP else "STABLE")
    return {"year": year, "window": f"1 Jan to 31 Mar {year}", "reference_window": f"1 Jan to 31 Mar {year - 1}",
            "looks_now": len(now["dates"]), "looks_reference": len(ref["dates"]), "compared_pct": compared,
            "vegetated_now_pct": veg_now, "vegetated_year_ago_pct": veg_ref, "vegetated_change_pp": d_veg,
            "median_ndvi_change": round(_nanmedian(now["ndvi_max"][both]) - _nanmedian(ref["ndvi_max"][both]), 3),
            "flag": flag, "trees_pct": landcover["trees_pct"] if landcover else None, "trees_year": LULC_YEARS[1],
            "ndvi_now": np.where(aoi, now["ndvi_max"], np.nan).astype("float32"),
            "ndvi_change": np.where(both, now["ndvi_max"] - ref["ndvi_max"], np.nan).astype("float32"),
            "grid": grid, "corridor": corridor, "buffer_m": buffer_m, "radius_km": radius_km,
            "dates_now": now["dates"], "dates_reference": ref["dates"],
            "method": (f"Per-pixel greenest Sentinel-2 NDVI over 1 January-31 March of {year} and {year - 1}, "
                       f"cloud-masked with the scene classification. Vegetated = greenest NDVI >= {GREEN_NDVI}, "
                       f"compared on pixels clear in both windows; flagged beyond ±{RIPARIAN_THRESHOLD_PP:.0f} "
                       "points. Matched Jan-Mar windows moved at most 5 points year on year on two reaches, matched "
                       "Apr-Sep windows 7-19 points, so no other season is compared.")}


def construction_alerts(point=None, radius_km=None, buffer_m=100, window_days=90, river=None, end=None,
                        min_patch_ha=0.1, max_patches=60):
    """River-side construction / clearing candidates: land green (greenest Sentinel-2 NDVI >= 0.50) in the same
    window a year ago that stayed bare (greenest NDVI < 0.30 over two or more clear looks) through the latest
    `window_days`. Bare patches with low SWIR reflectance are reported as new water or waterlogging. Candidates
    include construction, earth or sand works, brick kilns, dumping and fields left fallow alike."""
    corridor = river_corridor_geometry(buffer_m, point, radius_km, river)
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=window_days)
    now, ref, grid, aoi, (ref_start, ref_end) = _greenest_pair(corridor, start, end)
    seen = aoi & (now["n_obs"] >= 2) & (ref["n_obs"] >= 1)
    lost = seen & (ref["ndvi_max"] >= GREEN_NDVI) & (now["ndvi_max"] < BARE_NOW_NDVI)
    wet = lost & (now["swir_median"] < WET_SWIR)
    px_ha = grid.res * grid.res / 1e4
    feats = []
    for kind, mask in (("BARE / BUILT CANDIDATE", lost & ~wet), ("NEW WATER / WET", wet)):
        for geom, _ in shapes(mask.astype("uint8"), mask=mask, connectivity=8, transform=grid.transform):
            poly = shape(geom)
            if poly.area / 1e4 < min_patch_ha:
                continue
            ll = shp_transform(_TO_LL, poly)
            c = ll.centroid
            feats.append({"type": "Feature", "geometry": mapping(ll),
                          "properties": {"kind": kind, "area_ha": round(poly.area / 1e4, 2),
                                         "lat": round(c.y, 5), "lon": round(c.x, 5)}})
    feats.sort(key=lambda f: -f["properties"]["area_ha"])
    feats = feats[:max_patches]
    for i, (f, d) in enumerate(zip(feats, river_distance_m([f["geometry"] for f in feats], river)), 1):
        f["properties"].update({"patch": f"P{i:02d}", "river_distance_m": round(d)})
    table = pd.DataFrame([f["properties"] for f in feats],
                         columns=["patch", "kind", "area_ha", "river_distance_m", "lat", "lon"])
    n_bare = int((table["kind"] == "BARE / BUILT CANDIDATE").sum()) if len(table) else 0
    seen_pct = round(100 * int(seen.sum()) / max(int(aoi.sum()), 1), 1)
    caveats = []
    if {7, 8, 9} & set(pd.date_range(start, end, freq="D").month):
        caveats.append("Monsoon window: flooded, waterlogged and late-sown fields can stay below the green "
                       "threshold, so check candidates against recent imagery")
    if seen_pct < 50:
        caveats.append(f"Only {seen_pct}% of the corridor had two or more clear looks in the latest window")
    both = aoi & (now["n_obs"] >= 1) & (ref["n_obs"] >= 1)
    return {"window": f"{start:%d %b %Y} to {end:%d %b %Y}",
            "reference_window": f"{ref_start:%d %b %Y} to {ref_end:%d %b %Y}",
            "looks_now": len(now["dates"]), "looks_reference": len(ref["dates"]), "seen_pct": seen_pct,
            "lost_green_ha": round(float((lost & ~wet).sum()) * px_ha, 2),
            "new_wet_ha": round(float(wet.sum()) * px_ha, 2),
            "flag": f"{n_bare} CANDIDATE PATCH(ES)" if n_bare else "NO CANDIDATES",
            "table": table, "geojson": {"type": "FeatureCollection", "features": feats}, "caveats": caveats,
            "ndvi_change": np.where(both, now["ndvi_max"] - ref["ndvi_max"], np.nan).astype("float32"),
            "grid": grid, "corridor": corridor, "buffer_m": buffer_m, "radius_km": radius_km,
            "dates_now": now["dates"], "dates_reference": ref["dates"],
            "method": (f"Per-pixel greenest Sentinel-2 NDVI over the last {window_days} days and the same days a "
                       f"year earlier, cloud-masked with the scene classification. Candidate = greenest NDVI >= "
                       f"{GREEN_NDVI} a year ago and < {BARE_NOW_NDVI} throughout the latest window (two or more "
                       f"clear looks); patches from {min_patch_ha} ha. Candidates need a field or imagery check: "
                       "fallow fields, sand or soil works and brick kilns look the same from orbit.")}
