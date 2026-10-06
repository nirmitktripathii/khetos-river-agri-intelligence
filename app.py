"""KhetOS · River + Agriculture Intelligence.

A Streamlit proof of concept for Bareilly and Rohilkhand built only on free, open data: Sentinel-2, Sentinel-1 and
Landsat from Microsoft Planetary Computer, DLR WSF Evolution, Impact Observatory land cover, JRC Global Surface
Water, OpenStreetMap and Open-Meteo. Outputs are change flags and encroachment-risk signals for follow-up, never
legal determinations.

Every analysis runs through `eo.run_analysis` on long-lived worker threads. Streamlit runs each rerun on a new thread
that exits afterwards, and on Windows a thread that exits while holding GDAL state can deadlock later thread starts,
so nothing on the script thread touches GDAL (map overlays are reprojected on the I/O threads too).
"""
import functools
import json
import logging
import math
from datetime import date

import altair as alt
import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from pyproj import Geod
from shapely.geometry import shape
from streamlit_folium import st_folium

from src import ai, analytics, eo, flow
from src import river as rv
from src.boundaries import load_districts
from src.config import (APP_TITLE, ATTRIBUTIONS, DEFAULT_AOI, DISCLAIMER, NAKATIYA_REACHES, NAV_ITEMS, NEWS_CONTEXT,
                        REGION_BBOX)
from src.farmvibes_adapter import configuration as farmvibes_configuration
from src.farmvibes_adapter import explain_extension_points
from src.maps import (BRBG, PATCH_COLORS, RDYLGN, add_bbox, add_categorized, add_geojson, add_index_overlay,
                      add_layer_control, add_marker, add_scouting_cells, base_map)
from src.reports import build_markdown_report
from src.weather import get_weather_context

st.set_page_config(page_title=APP_TITLE, page_icon="🌾", layout="wide")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("khetos.app")

TTL = 6 * 3600
PAGES = dict(zip(["overview", "change-radar", "field-scanner", "water", "fusion", "scouting", "ask", "river",
                  "land-change", "river-flow"], NAV_ITEMS))
WHOLE_RIVER = "Whole mapped river"
REACH_OPTIONS = [*NAKATIYA_REACHES, WHOLE_RIVER]
BUFFER_OPTIONS = [10, 25, rv.GREEN_BELT_M, 50, 100, 250, 500, 1000]
AOI_PRESETS = {"Lower-reach farmland (default)": DEFAULT_AOI,
               **{name: point for name, (point, _) in NAKATIYA_REACHES.items()}}
CROPS = ["Unknown", "Wheat", "Sugarcane", "Paddy", "Mustard", "Potato", "Maize", "Pulses", "Vegetables", "Other"]
LATEST_RABI = eo.latest_rabi_year()
SEASONS = list(range(eo.LANDSAT_FIRST_YEAR, LATEST_RABI + 1))
FLAG_COLORS = {
    "HIGH": "red", "WATCH": "orange", "STABLE": "green", "NO DATA": "gray",
    "GREENNESS DECLINE": "red", "GREENING": "green", "TOO FEW CLEAR LOOKS": "gray",
    "WATER EXPANSION": "blue", "WATER RECESSION": "orange", "NO SIGNIFICANT CHANGE": "green",
    "SINGLE PASS ONLY": "gray", "SIGNIFICANT DECLINE": "red", "SIGNIFICANT INCREASE": "green",
    "NO SIGNIFICANT TREND": "gray", "TOO FEW SEASONS": "gray", "NO CANDIDATES": "green",
    "UNUSUAL DECLINE": "red", "DECLINE": "orange",
    "AGREE · vegetation decline": "red", "AGREE · vegetation growth": "green", "AGREE · stable": "green",
    "DISAGREE · verify on the ground": "orange",
}
SHARES = {"vegetation_pct": ("Vegetated", "#2E7D32"), "water_pct": ("Water", "#0277BD"),
          "nongreen_pct": ("Non-green (indicative)", "#8D6E63")}
LANDCOVER = {"water_pct": ("Water", "#419BDF"), "trees_pct": ("Trees", "#397D49"),
             "flooded_vegetation_pct": ("Flooded vegetation", "#7A87C6"), "crops_pct": ("Crops", "#E49635"),
             "built_pct": ("Built area", "#C4281B"), "bare_pct": ("Bare ground", "#A59B8F"),
             "rangeland_pct": ("Rangeland", "#E3E2C3")}
SOIL_LAYERS = {"0-1 cm": "soil_moisture_0_to_1cm", "3-9 cm": "soil_moisture_3_to_9cm",
               "9-27 cm": "soil_moisture_9_to_27cm", "27-81 cm": "soil_moisture_27_to_81cm"}
CORRIDOR_COLORS = ["#FDD835", "#FFB300", "#FB8C00", "#F4511E", "#E53935", "#C2185B", "#8E24AA", "#5E35B1"]
QILA_NOTE = ("The Qila (Kila) is described locally as a 112 km stream from Uttarakhand through Baheri, Deorania and "
             "Bhojipura to the Ramganga. OpenStreetMap does not name it: the dashed lines are unnamed OSM rivers that "
             "match that description, identified by local knowledge and **not verified**. The river analyses in "
             "KhetOS cover the Nakatiya only.")
_GEOD = Geod(ellps="WGS84")


# ------------------------------------------------------------------------------------------ helpers

def finite(v):
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def fmt(v, spec=".1f", suffix=""):
    return f"{float(v):{spec}}{suffix}" if finite(v) else "n/a"


def delta(v, spec="+.1f", suffix=" pp"):
    """A metric delta, or None so that a missing value shows no arrow."""
    return f"{float(v):{spec}}{suffix}" if finite(v) else None


def bullets(items):
    return "\n".join(f"- {s}" for s in items)


def buffer_label(b):
    return f"{b} m · reported green belt" if b == rv.GREEN_BELT_M else f"{b} m"


def error_text(exc):
    return str(exc) if isinstance(exc, (RuntimeError, ValueError)) else f"{type(exc).__name__}: {exc}"


def aoi_bbox(lon, lat, half_km):
    """Lon/lat box reaching `half_km` either side of a point."""
    dlat = half_km / 110.574
    dlon = half_km / (111.320 * math.cos(math.radians(lat)))
    return tuple(round(v, 6) for v in (lon - dlon, lat - dlat, lon + dlon, lat + dlat))


def bbox_centre(bbox):
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def in_region(lon, lat):
    w, s, e, n = REGION_BBOX
    return w <= lon <= e and s <= lat <= n


def reach_args(reach):
    """(point, radius_km) of a named reach; (None, None) means the whole mapped river."""
    return NAKATIYA_REACHES.get(reach, (None, None))


def fc_bounds(fc):
    return shape({"type": "GeometryCollection", "geometries": [f["geometry"] for f in fc["features"]]}).bounds


def course_facts(fc):
    try:
        return rv.nakatiya_course(fc)
    except Exception as exc:  # e.g. a refreshed extract without the bundled main-stem ways
        log.warning("Course summary unavailable: %s", exc)
        return {"ways": len(fc["features"]), "total_km": round(rv.length_km(rv.river_lines(fc)), 1),
                "retrieved": fc.get("retrieved"), "source": fc.get("source", "OpenStreetMap"),
                "reported_source": rv.REPORTED_SOURCE,
                "upstream_status": "Unverified: the course upstream of the mapped head is unknown."}


# ------------------------------------------------------------------ cached analyses (worker threads)

def has_skips(res):
    """True when an analysis result left out satellite scenes that could not be read."""
    if not isinstance(res, dict):
        return False
    if res.get("skipped") or res.get("skipped_now") or res.get("skipped_reference"):
        return True
    return any(has_skips(v) if isinstance(v, dict) else
               isinstance(v, pd.DataFrame) and "skipped_scenes" in v and bool((v["skipped_scenes"] > 0).any())
               for v in res.values())


def retry_incomplete(cached):
    """Show a result that left out unreadable scenes but drop it from the cache, so the next run tries them
    again instead of serving the gap for the whole cache lifetime."""
    @functools.wraps(cached)
    def call(*args):
        res = cached(*args)
        if has_skips(res):
            cached.clear(*args)
        return res
    call.clear = cached.clear
    return call


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=8, show_spinner=False)
def cached_trend(bbox, months, cloud):
    skipped = []
    series = eo.run_analysis(eo.trend_series, bbox, months, cloud, skipped=skipped)
    return {"series": series, "skipped": skipped}


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=8, show_spinner=False)
def cached_field_scan(bbox, geometry, months, cloud):
    return eo.run_analysis(analytics.field_scan, bbox, geometry, months, cloud)


@st.cache_data(ttl=3600, max_entries=32, show_spinner=False)
def cached_weather(lat, lon):
    return get_weather_context(lat, lon)  # plain HTTP, safe on the script thread


@st.cache_data(ttl=TTL, max_entries=8, show_spinner=False)
def cached_radar_pair(bbox):
    return eo.run_analysis(analytics.radar_pair, bbox)


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=4, show_spinner=False)
def cached_scouting(bbox, months, cloud):
    return eo.run_analysis(analytics.make_scouting_grid, bbox, months, cloud)


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=4, show_spinner=False)
def cached_fields_near(distance_m, point, radius_km, months, cloud, river):
    return eo.run_analysis(analytics.fields_near_river, distance_m, point, radius_km, months, cloud, river)


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=8, show_spinner=False)
def cached_land_change(point, radius_km, buffer_m, year_a, year_b, river):
    return eo.run_analysis(rv.river_land_change, point, radius_km, buffer_m, year_a, year_b, river)


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=4, show_spinner=False)
def cached_ladder(point, radius_km, buffers, year_a, year_b, river):
    return eo.run_analysis(rv.river_buffer_ladder, point, radius_km, buffers, year_a, year_b, river)


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=4, show_spinner=False)
def cached_timeline(point, radius_km, buffer_m, years, river):
    return eo.run_analysis(rv.river_timeline, point, radius_km, buffer_m, years, river)


@st.cache_data(ttl=TTL, max_entries=8, show_spinner=False)
def cached_water_watch(point, radius_km, buffer_m, river):
    return eo.run_analysis(rv.corridor_water_watch, point, radius_km, buffer_m, river)


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=6, show_spinner=False)
def cached_riparian(point, radius_km, buffer_m, year, river):
    return eo.run_analysis(rv.riparian_health, point, radius_km, buffer_m, river, year)


@retry_incomplete
@st.cache_data(ttl=TTL, max_entries=6, show_spinner=False)
def cached_alerts(point, radius_km, buffer_m, window_days, river):
    return eo.run_analysis(rv.construction_alerts, point, radius_km, buffer_m, window_days, river)


@st.cache_data(ttl=TTL, max_entries=32, show_spinner=False)
def cached_built_extent(year, point, radius_km, buffer_m, river):
    ext = eo.run_analysis(rv.corridor_built_extent, year, point, radius_km, buffer_m, river)
    ext["built"] = np.isfinite(ext.pop("array"))  # a byte per pixel in the cache instead of four
    return ext


def compute_radar(bbox, months, cloud):
    out = {"bbox": bbox, "trend": cached_trend(bbox, months, cloud), "scan": None, "scan_error": None}
    try:
        out["scan"] = cached_field_scan(bbox, None, months, cloud)
    except Exception as exc:
        out["scan_error"] = error_text(exc)
    return out


def compute_field(bbox, geometry, months, cloud, crop):
    scan = cached_field_scan(bbox, geometry, months, cloud)
    lon, lat = bbox_centre(bbox)
    weather, weather_error = None, None
    try:
        weather = cached_weather(round(lat, 3), round(lon, 3))
    except Exception as exc:
        weather_error = error_text(exc)
    # Without weather there is no water balance, so no stress signal rather than one that assumes no rain.
    stress = (analytics.water_stress_signal(scan["latest"]["ndmi"], weather["rain_14d_mm"], weather["et0_14d_mm"])
              if weather else None)
    brief = ai.field_brief({"latest": scan["latest"], "previous": scan["previous"], "weather": weather,
                            "stress": stress, "crop": crop})
    return {**scan, "weather": weather, "weather_error": weather_error, "stress": stress, "brief": brief,
            "crop": crop, "geometry": geometry, "bbox": bbox}


def compute_water(bbox, months, cloud):
    lon, lat = bbox_centre(bbox)
    out = {"bbox": bbox, "scan": None, "scan_error": None, "weather": None, "weather_error": None, "stress": None}
    try:
        out["scan"] = cached_field_scan(bbox, None, months, cloud)
    except Exception as exc:
        out["scan_error"] = error_text(exc)
    try:
        out["weather"] = cached_weather(round(lat, 3), round(lon, 3))
    except Exception as exc:
        out["weather_error"] = error_text(exc)
    if out["scan"] and out["weather"]:
        w = out["weather"]
        out["stress"] = analytics.water_stress_signal(out["scan"]["latest"]["ndmi"], w["rain_14d_mm"],
                                                      w["et0_14d_mm"])
    return out


def compute_fusion(bbox, months, cloud):
    scan = cached_field_scan(bbox, None, months, cloud)
    sar_now, sar_prev = cached_radar_pair(bbox)
    return {"bbox": bbox, "optical_now": scan["latest"], "optical_prev": scan["previous"],
            "skipped": scan.get("skipped"),
            "radar_now": sar_now, "radar_prev": sar_prev,
            "assessment": analytics.fusion_assessment(scan["latest"], scan["previous"], sar_now, sar_prev)}


def compute_scouting(bbox, months, cloud):
    return {**cached_scouting(bbox, months, cloud), "bbox": bbox, "near": False}


def compute_fields_near(distance_m, point, radius_km, months, cloud, river):
    res = cached_fields_near(distance_m, point, radius_km, months, cloud, river)
    return {**res, "bbox": shape(res["corridor"]).bounds, "near": True}


# ------------------------------------------------------------------------------- result storage

def run_into(key, params, label, fn, *args):
    """Run an analysis under a spinner and keep its result (or error) in the session with the settings used."""
    with st.spinner(label, show_time=True):
        try:
            st.session_state[key] = {"params": params, "result": fn(*args)}
        except Exception as exc:
            log.warning("%s failed", label, exc_info=True)
            st.session_state[key] = {"params": params, "error": error_text(exc)}


def show_stored(key, params, render, render_key):
    """Show a kept result, saying so when the settings have changed since it was computed."""
    entry = st.session_state.get(key)
    if not entry:
        return
    if entry["params"] != params:
        st.info("The settings have changed since this result was computed: run it again to update it.", icon="🔁")
    if "error" in entry:
        st.error(entry["error"])
    else:
        render(entry["result"], render_key)


# ------------------------------------------------------------------------------- display pieces

def flag_color(flag):
    if flag in FLAG_COLORS:
        return FLAG_COLORS[flag]
    return "gray" if flag.startswith("INCOMPLETE") else "orange" if "CANDIDATE" in flag else "blue"


def flag_row(flag, extra=(), help=None):
    with st.container(horizontal=True):
        st.badge(flag, color=flag_color(flag), help=help)
        for f in extra:
            st.badge(f, color="orange")


def show_caveats(caveats):
    if caveats:
        with st.expander(f"Caveats ({len(caveats)})", expanded=True, icon=":material/warning:"):
            st.markdown(bullets(caveats))


def show_skips(skipped, what="Sentinel-2 scene"):
    """Warn about scenes an analysis could not read and left out."""
    note = eo.skip_note(skipped, what)
    if note:
        st.warning(note, icon=":material/cloud_off:")
        with st.expander(f"Unread scenes ({len(skipped)})"):
            st.dataframe(pd.DataFrame(skipped), hide_index=True)


def show_method(text):
    with st.expander("Method", icon=":material/science:"):
        st.markdown(text)


def stamp():
    return f"{date.today():%Y%m%d}"


def download_report(title, data, key, notes=None):
    st.download_button("Evidence report (Markdown)", build_markdown_report(title, data, notes),
                       file_name=f"khetos_{key}_{stamp()}.md", mime="text/markdown", key=f"{key}_md",
                       on_click="ignore", icon=":material/description:")


def download_csv(df, key, label="Table (CSV)"):
    st.download_button(label, df.to_csv(index=False), file_name=f"khetos_{key}_{stamp()}.csv", mime="text/csv",
                       key=f"{key}_csv", on_click="ignore", icon=":material/table:")


def download_geojson(fc, key, label="Map layer (GeoJSON)"):
    st.download_button(label, json.dumps(fc), file_name=f"khetos_{key}_{stamp()}.geojson",
                       mime="application/geo+json", key=f"{key}_geojson", on_click="ignore", icon=":material/map:")


def show_map(m, key, height=500):
    add_layer_control(m)
    st_folium(m, key=key, height=height, use_container_width=True, returned_objects=[])


def add_river(m, show=True, weight=3):
    add_geojson(m, RIVER_FC, "Nakatiya (OpenStreetMap)", color="#1565C0", weight=weight, show=show,
                tooltip_fields=["name", "osm_way"], tooltip_aliases=["Name", "OSM way"])


def add_corridor(m, corridor, name="Corridor", weight=2):
    add_geojson(m, corridor, name, color="#F9A825", weight=weight, fill_opacity=0.06 if weight > 1 else 0)


def corridor_map(corridor, key, name, overlays=(), height=480):
    """Satellite map of a corridor with optional index overlays: (array, grid, name, vmin, vmax, colors, show)."""
    m = base_map(shape(corridor).bounds, satellite=True)
    for array, grid, label, vmin, vmax, colors, show in overlays:
        add_index_overlay(m, array, grid, label, vmin, vmax, colors, show=show)
    add_corridor(m, corridor, name)
    add_river(m)
    return m


# --------------------------------------------------------------------------------------- charts

def trend_chart(df):
    d = df.melt(id_vars="date", value_vars=["ndvi", "ndmi"], var_name="index", value_name="value")
    d["index"] = d["index"].str.upper()
    return (alt.Chart(d).mark_line(point=True)
            .encode(x=alt.X("date:T", title=None),
                    y=alt.Y("value:Q", title="Median over clear cropland"),
                    color=alt.Color("index:N", title=None,
                                    scale=alt.Scale(domain=["NDVI", "NDMI"], range=["#2E7D32", "#0277BD"])),
                    tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("index:N", title="Index"),
                             alt.Tooltip("value:Q", title="Value", format=".3f")])
            .properties(height=300))


def landsat_chart(landsat, trend):
    d = landsat.melt(id_vars=["year", "used", "quality"], value_vars=list(SHARES), var_name="share",
                     value_name="pct")
    d["share"] = d["share"].map(lambda k: SHARES[k][0])
    d["season"] = np.where(d["used"], "used", "left out")
    names, colors = zip(*SHARES.values())
    x = alt.X("year:Q", title="Rabi season", axis=alt.Axis(format="d"), scale=alt.Scale(zero=False))
    y = alt.Y("pct:Q", title="% of corridor")
    base = alt.Chart(d).encode(x=x, y=y, color=alt.Color("share:N", title=None,
                                                         scale=alt.Scale(domain=list(names), range=list(colors))))
    layers = [base.transform_filter(alt.datum.used).mark_line(),
              base.mark_point(filled=True, size=60).encode(
                  shape=alt.Shape("season:N", title=None,
                                  scale=alt.Scale(domain=["used", "left out"], range=["circle", "cross"])),
                  tooltip=[alt.Tooltip("year:Q", title="Season", format="d"), alt.Tooltip("share:N", title="Share"),
                           alt.Tooltip("pct:Q", title="%", format=".1f"), alt.Tooltip("quality:N", title="Quality"),
                           alt.Tooltip("season:N", title="In trend")])]
    if trend.get("fitted_pct"):
        fit = pd.DataFrame({"year": [trend["first"], trend["last"]], "pct": list(trend["fitted_pct"])})
        layers.append(alt.Chart(fit).mark_line(strokeDash=[6, 4], color="#1B5E20").encode(x="year:Q", y="pct:Q"))
    return alt.layer(*layers).properties(height=320)


def builtup_chart(builtup):
    return (alt.Chart(builtup).mark_line(point=True)
            .encode(x=alt.X("year:Q", title=None, axis=alt.Axis(format="d"), scale=alt.Scale(zero=False)),
                    y=alt.Y("built_pct:Q", title="% of corridor"),
                    color=alt.Color("source:N", title=None, legend=alt.Legend(orient="bottom", direction="vertical")),
                    tooltip=[alt.Tooltip("year:Q", title="Year", format="d"), alt.Tooltip("source:N", title="Source"),
                             alt.Tooltip("built_pct:Q", title="%", format=".1f")])
            .properties(height=280))


def landcover_chart(lc):
    cols = [c for c in LANDCOVER if c in lc]
    d = lc.melt(id_vars="year", value_vars=cols, var_name="class", value_name="pct")
    d["class"] = d["class"].map(lambda c: LANDCOVER[c][0])
    names, colors = zip(*LANDCOVER.values())
    return (alt.Chart(d).mark_bar()
            .encode(x=alt.X("year:O", title=None), y=alt.Y("pct:Q", title="% of corridor", stack="zero"),
                    color=alt.Color("class:N", title=None, scale=alt.Scale(domain=list(names), range=list(colors))),
                    tooltip=[alt.Tooltip("year:O", title="Year"), alt.Tooltip("class:N", title="Class"),
                             alt.Tooltip("pct:Q", title="%", format=".1f")])
            .properties(height=280))


def ladder_chart(table):
    pp = [c for c in table.columns if "_pp" in c and not c.startswith("non_green")]
    d = table.melt(id_vars="buffer_m", value_vars=pp, var_name="measure", value_name="pp").dropna()
    return (alt.Chart(d).mark_bar()
            .encode(x=alt.X("buffer_m:O", title="Corridor half-width (m)"), xOffset="measure:N",
                    y=alt.Y("pp:Q", title="Change (percentage points)"),
                    color=alt.Color("measure:N", title=None, legend=alt.Legend(orient="bottom", direction="vertical")),
                    tooltip=[alt.Tooltip("buffer_m:O", title="Half-width (m)"),
                             alt.Tooltip("measure:N", title="Measure"), alt.Tooltip("pp:Q", title="pp", format="+.1f")])
            .properties(height=280))


def weather_chart(daily):
    base = alt.Chart(daily).encode(x=alt.X("time:T", title=None))
    rain = base.mark_bar(color="#0277BD", opacity=0.75).encode(
        y=alt.Y("rain_mm:Q", title="mm per day"),
        tooltip=[alt.Tooltip("time:T", title="Day"), alt.Tooltip("rain_mm:Q", title="Rain (mm)", format=".1f"),
                 alt.Tooltip("et0_mm:Q", title="ET0 (mm)", format=".1f")])
    et0 = base.mark_line(color="#E65100", point=True).encode(y="et0_mm:Q")
    return alt.layer(rain, et0).properties(height=240)


def soil_chart(weather):
    d = pd.DataFrame([{"depth": name, "sm": weather.get(k)} for name, k in SOIL_LAYERS.items()
                      if finite(weather.get(k))])
    if d.empty:
        return None
    return (alt.Chart(d).mark_bar(color="#6D4C41")
            .encode(x=alt.X("sm:Q", title="Volumetric soil moisture (m³/m³)"), y=alt.Y("depth:N", sort=None, title=None),
                    tooltip=[alt.Tooltip("depth:N", title="Depth"), alt.Tooltip("sm:Q", title="m³/m³", format=".3f")])
            .properties(height=170))


# ------------------------------------------------------------------------------------ renderers
# Each renderer takes (result, key); `key` keeps widget and map keys apart when a renderer is used twice.

def render_course(course, key):
    c = st.columns(4)
    c[0].metric("Main stem (mapped)", fmt(course.get("main_stem_km"), ".1f", " km"),
                help="From the mapped head east of Bhojipura to the Ramganga confluence")
    c[1].metric("All mapped ways", fmt(course.get("total_km"), ".1f", " km"), help="Including side channels")
    c[2].metric("Side channels", fmt(course.get("branches_km"), ".1f", " km"))
    c[3].metric("OSM ways", course.get("ways"))
    st.warning(f"**Upstream of the mapped head.** {course['upstream_status']} Reported source: "
               f"{course['reported_source']}.", icon="❓")
    m = base_map(shape(RIVER_GEOM).bounds)
    add_watershed(m)
    add_river(m)
    if course.get("head"):
        add_marker(m, course["head"][1], course["head"][0], "Mapped head (OpenStreetMap)", color="green", icon="play")
    if course.get("confluence"):
        add_marker(m, course["confluence"][1], course["confluence"][0], "Confluence with the Ramganga",
                   color="darkblue", icon="flag")
    show_map(m, f"{key}_course_map", 520)
    st.caption(f"Geometry: {course.get('source')} · retrieved {course.get('retrieved')}")


def render_qila(_, key):
    st.markdown(QILA_NOTE)
    qila = rv.load_qila_candidate()
    m = base_map(fc_bounds(qila))
    add_geojson(m, qila, "Qila candidate (unverified OSM ways)", color="#7B1FA2", weight=3, dash="6 6",
                tooltip_fields=["osm_way"], tooltip_aliases=["OSM way (unnamed river)"])
    add_river(m)
    show_map(m, f"{key}_qila_map", 480)


def render_radar(r, key):
    df, skipped = r["trend"]["series"], r["trend"]["skipped"]
    sig = analytics.change_signal(df) if len(df) else None
    if sig is None:
        st.warning("Fewer than two clear scenes in the window: widen the history window or raise the cloud "
                   "tolerance in the sidebar.")
    else:
        flag_row(sig["label"])
        c = st.columns(4)
        c[0].metric("NDVI", fmt(sig["ndvi"], ".2f"), delta(sig["ndvi_change"], "+.2f", ""),
                    help="Greenness: median over clear cropland pixels")
        c[1].metric("NDMI", fmt(sig["ndmi"], ".2f"), delta(sig["ndmi_change"], "+.2f", ""),
                    help="Canopy water content")
        c[2].metric("Change score", f"{sig['score']:.0f} / 100",
                    help="50 = an ordinary move for this area; higher = a more unusual decline")
        c[3].metric("Clear scenes", len(df), help=f"{len(skipped)} more could not be read" if skipped else None)
        st.caption(f"{sig['previous_date']} → {sig['latest_date']} · scored by {sig['method']}")
    show_skips(skipped)
    if len(df):
        st.altair_chart(trend_chart(df), width="stretch")
    scan = r.get("scan")
    if scan:
        m = base_map(r["bbox"], satellite=True)
        add_index_overlay(m, scan["ndvi"], scan["grid"], f"NDVI {scan['latest']['date']}", -0.1, 0.9, RDYLGN)
        if scan["ndvi_change"] is not None:
            add_index_overlay(m, scan["ndvi_change"], scan["grid"],
                              f"NDVI change since {scan['previous']['date']}", -0.4, 0.4, BRBG, show=False)
        add_bbox(m, r["bbox"])
        show_map(m, f"{key}_radar_map", 460)
    elif r.get("scan_error"):
        st.warning(f"No map: {r['scan_error']}")
    with st.expander("Scene table"):
        st.dataframe(df, hide_index=True)
    with st.container(horizontal=True):
        download_csv(df, f"{key}_scenes")
        download_report("Change radar", {"area_bbox": r["bbox"], **(sig or {}), **eo.skip_summary(skipped),
                                         "scenes": df}, f"{key}_radar", [eo.skip_note(skipped)] if skipped else None)


def render_field(r, key):
    latest, prev, stress = r["latest"], r["previous"], r["stress"]
    change = lambda k: delta(latest[k] - prev[k], "+.2f", "") if prev else None
    c = st.columns(4)
    c[0].metric("NDVI", fmt(latest["ndvi"], ".2f"), change("ndvi"), help="Greenness, median over clear pixels")
    c[1].metric("NDMI", fmt(latest["ndmi"], ".2f"), change("ndmi"), help="Canopy water content")
    c[2].metric("Water-stress signal", f"{stress['water_stress_signal']:.0f} / 100" if stress else "n/a",
                help="Canopy dryness (NDMI) blended with the 14-day water balance; an inspection signal, not an "
                     "irrigation prescription")
    c[3].metric("Clear pixels", f"{latest['pixels']:,}", help=latest["pixels_used"])
    st.caption(f"Scene {latest['date']} ({latest['clear_pct']:.0f}% clear) · previous clear scene "
               f"{prev['date'] if prev else 'none in the 75 days before'} · crop as reported: {r['crop']}")
    if r.get("weather_error"):
        st.warning(f"Weather unavailable, so no water balance: {r['weather_error']}")
    show_skips(r.get("skipped"))
    left, right = st.columns([3, 2])
    with left:
        m = base_map(r["bbox"], satellite=True)
        add_index_overlay(m, r["ndvi"], r["grid"], f"NDVI {latest['date']}", -0.1, 0.9, RDYLGN)
        if r["ndvi_change"] is not None:
            add_index_overlay(m, r["ndvi_change"], r["grid"], "NDVI change since the previous scene", -0.4, 0.4,
                              BRBG, show=False)
        if r["geometry"]:
            add_geojson(m, r["geometry"], "Field", color="#FFEB3B", weight=3, fill_opacity=0)
        show_map(m, f"{key}_field_map", 440)
    with right:
        brief = r["brief"]
        st.markdown("**What the data shows**")
        st.markdown(bullets(brief["findings"]))
        st.markdown("**What to check**")
        st.markdown(bullets(brief["checks"]))
        with st.expander("Limits"):
            st.markdown(bullets(brief["limits"]))
    weather = {k: v for k, v in (r["weather"] or {}).items() if k != "daily"}
    notes = ([f"Finding: {s}" for s in brief["findings"]] + [f"Check: {s}" for s in brief["checks"]]
             + [f"Limit: {s}" for s in brief["limits"]]
             + ([f"Limit: {eo.skip_note(r['skipped'])}"] if r.get("skipped") else []))
    download_report("Field brief", {"crop_reported": r["crop"], "area_bbox": r["bbox"], "latest_scene": latest,
                                    "previous_scene": prev, **eo.skip_summary(r.get("skipped")), "weather": weather,
                                    "water_stress": stress}, f"{key}_field", notes)


def render_water(r, key):
    stress, w, scan = r["stress"], r["weather"], r["scan"]
    c = st.columns(4)
    c[0].metric("Water-stress signal", f"{stress['water_stress_signal']:.0f} / 100" if stress else "n/a",
                help="60% canopy dryness from NDMI, 40% the 14-day water balance; 60+ calls for a check")
    c[1].metric("Rain, last 14 days", fmt(w["rain_14d_mm"], ".0f", " mm") if w else "n/a")
    c[2].metric("Reference ET0, 14 days", fmt(w["et0_14d_mm"], ".0f", " mm") if w else "n/a",
                help="FAO-56 reference evapotranspiration: the water a well-watered crop would use")
    c[3].metric("Deficit (ET0 − rain)", fmt(w["water_deficit_14d_mm"], "+.0f", " mm") if w else "n/a",
                help="Positive = drier than the crop's demand")
    for err in (r["scan_error"], r["weather_error"]):
        if err:
            st.warning(err)
    if scan:
        show_skips(scan.get("skipped"))
    if w:
        st.caption(f"{w['period']} · Open-Meteo model cell {w['model_cell']} (a ~10 km forecast-model grid, not a "
                   "gauge on this field)")
        left, right = st.columns([3, 2])
        with left:
            st.markdown("**Daily rain (bars) and reference ET0 (line)**")
            st.altair_chart(weather_chart(w["daily"]), width="stretch")
        with right:
            chart = soil_chart(w)
            if chart is not None:
                st.markdown("**Modelled soil moisture now**")
                st.altair_chart(chart, width="stretch")
    if scan:
        m = base_map(r["bbox"], satellite=True)
        add_index_overlay(m, scan["ndmi"], scan["grid"], f"NDMI {scan['latest']['date']}", -0.2, 0.6, BRBG)
        add_bbox(m, r["bbox"])
        show_map(m, f"{key}_ndmi_map", 440)
        st.caption(f"NDMI (canopy water) of the latest clear Sentinel-2 scene, {scan['latest']['date']}: brown is "
                   "dry, teal is wet. Bare soil also reads dry.")
    data = {"area_bbox": r["bbox"], "water_stress": stress,
            "weather": {k: v for k, v in (w or {}).items() if k != "daily"},
            "latest_scene": scan["latest"] if scan else None, **eo.skip_summary(scan.get("skipped") if scan else None)}
    download_report("Water and moisture signals", data, f"{key}_water")


def render_fusion(r, key):
    a = r["assessment"]
    flag_row(a["label"])
    c = st.columns(4)
    c[0].metric("Optical: NDVI change", fmt(a["ndvi_change"], "+.2f"),
                help="Sentinel-2, latest clear scene against the one before; ±0.05 counts as a move")
    c[1].metric("Radar: VH change", fmt(a["vh_change_db"], "+.1f", " dB"),
                help="Sentinel-1 cross-polarised backscatter over cropland, same relative orbit; ±1 dB counts as a move")
    c[2].metric("Radar vegetation index change", fmt(a["rvi_change"], "+.2f"))
    c[3].metric("Optical-radar gap", f"{a['optical_radar_gap_days']} days",
                help="Days between the latest optical and radar looks; larger gaps weaken the comparison")
    rows = [{"sensor": name, "date": m["date"], "NDVI": m["ndvi"], "NDMI": m["ndmi"], "pixels": m["pixels"]}
            for name, m in (("Sentinel-2 latest", r["optical_now"]), ("Sentinel-2 previous", r["optical_prev"])) if m]
    rows += [{"sensor": name, "date": m["date"], "VV (dB)": m["vv_db"], "VH (dB)": m["vh_db"], "RVI": m["rvi"],
              "orbit": f"{m['relative_orbit']} {m['orbit_state']}", "pixels": m["pixels"]}
             for name, m in (("Sentinel-1 latest", r["radar_now"]), ("Sentinel-1 previous", r["radar_prev"])) if m]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    show_skips(r.get("skipped"))
    st.caption("Radar backscatter responds to canopy structure and moisture and sees through cloud; optical NDVI "
               "responds to greenness. When both move the same way the change is more likely real; when they "
               "disagree (e.g. harvest residue, flooding, rain on the leaves) it needs a field visit.")
    download_report("SAR + optical fusion", {"area_bbox": r["bbox"], "assessment": a, "optical_latest": r["optical_now"],
                                             "optical_previous": r["optical_prev"], "radar_latest": r["radar_now"],
                                             "radar_previous": r["radar_prev"], **eo.skip_summary(r.get("skipped"))},
                    f"{key}_fusion")


def render_scouting(r, key):
    t = r["table"]
    counts = t["priority"].value_counts()
    for col, p in zip(st.columns(len(analytics.PRIORITY_ORDER)), analytics.PRIORITY_ORDER):
        col.metric(p.capitalize(), int(counts.get(p, 0)))
    st.caption(f"Latest clear scene {r['date']} · previous {r['previous_date'] or 'none'} · {r['cell_m']} m cells · "
               f"{r['model']} · cropland: {r['cropland_source']}")
    show_skips(r.get("skipped"))
    m = base_map(r["bbox"], satellite=True)
    add_scouting_cells(m, r["geojson"])
    if r["near"]:
        add_corridor(m, r["corridor"], f"Within {r['distance_m']} m of the Nakatiya")
        add_river(m)
    else:
        add_bbox(m, r["bbox"])
    show_map(m, f"{key}_scout_map", 500)
    cols = [c for c in ["cell", "priority", "reason", "river_distance_m", "ndvi", "d_ndvi", "ndmi", "d_ndmi",
                        "cropland_pct", "clear_pct", "lat", "lon"] if c in t]
    shown = r["flagged"] if r["near"] and len(r["flagged"]) else t
    if r["near"]:
        st.markdown(f"**{len(r['flagged'])} HIGH / WATCH cell(s) within {r['distance_m']} m of the river**"
                    if len(r["flagged"]) else "**No HIGH or WATCH cell near the river: all cells below.**")
    st.dataframe(shown[cols], hide_index=True, column_config={
        "reason": st.column_config.TextColumn("Why", width="large"),
        "river_distance_m": st.column_config.NumberColumn("To river (m)", format="%.0f"),
        **{c: st.column_config.NumberColumn(format="%.3f") for c in ("ndvi", "d_ndvi", "ndmi", "d_ndmi")},
        **{c: st.column_config.NumberColumn(format="%.5f") for c in ("lat", "lon")}})
    with st.container(horizontal=True):
        download_csv(t, f"{key}_cells")
        download_geojson(r["geojson"], f"{key}_cells")


def render_land_change(r, key):
    flag_row(r["change_flag"], r["flags"], help="HIGH: two or more kinds of change; WATCH: one; STABLE: none")
    st.caption(f"Rabi {r['year_a']} → {r['year_b']} · {buffer_label(r['buffer_m'])} either side of the centreline · "
               f"corridor {r['corridor_area_km2']} km²")
    c = st.columns(4)
    c[0].metric(f"Settled area {r['settlement_period'] or '(not covered)'}",
                fmt(r["settlement_change_pp"], "+.1f", " pp"), help="DLR WSF Evolution, 30 m, 1985-2015")
    c[1].metric(f"Built area {r['builtup_period'] or '(not covered)'}", fmt(r["builtup_change_pp"], "+.1f", " pp"),
                help="Impact Observatory 10 m land cover, 2017-2025")
    c[2].metric(f"Vegetated {r['year_a']}→{r['year_b']}", fmt(r["vegetation_change_pp"], "+.1f", " pp"),
                help=f"Landsat rabi composites; flagged beyond ±{rv.VEGETATION_THRESHOLD_PP:.0f} points")
    c[3].metric(f"Water {r['year_a']}→{r['year_b']}", fmt(r["water_change_pp"], "+.1f", " pp"),
                help=f"Landsat rabi composites; flagged beyond ±{rv.CHANGE_THRESHOLD_PP:.0f} points")
    show_caveats(r["caveats"])
    show_map(corridor_map(r["corridor"], key, f"{r['buffer_m']} m corridor"), f"{key}_change_map", 460)
    jrc = r.get("jrc")
    if jrc:
        st.markdown(f"**Long-term water history, {jrc['period']} (JRC Global Surface Water)**")
        c = st.columns(4)
        c[0].metric("Ever water", fmt(jrc["ever_water_pct"], ".1f", "%"))
        c[1].metric("Water most of the time", fmt(jrc["frequent_water_pct"], ".1f", "%"),
                    help="Water in at least half of all observations")
        c[2].metric("Water lost", fmt(jrc["lost_water_pct"], ".1f", "%"),
                    help=f"{fmt(jrc['lost_share_of_historic_water_pct'], '.0f', '%')} of the ever-water area")
        c[3].metric("New water", fmt(jrc["new_water_pct"], ".1f", "%"))
    with st.expander("Evidence tables"):
        st.markdown("Landsat ends (median of the usable seasons around each year)")
        st.dataframe(r["landsat_ends"], hide_index=True)
        st.markdown("Landsat seasons")
        st.dataframe(r["table"], hide_index=True)
        if r["landcover_table"] is not None:
            st.markdown("Impact Observatory land cover, both years (% of corridor)")
            st.dataframe(r["landcover_table"], hide_index=True)
    show_method(r["method"])
    download_report("Nakatiya change flags", r, f"{key}_change")


def render_ladder(r, key):
    t = r["table"]
    st.caption(f"Rabi {r['year_a']} → {r['year_b']} · settled area {r['settlement_period'] or 'not covered'} · "
               f"built area {r['builtup_period'] or 'not covered'}")
    st.dataframe(t, hide_index=True, column_config={
        "buffer_m": st.column_config.NumberColumn("Half-width (m)"),
        "area_ha": st.column_config.NumberColumn("Area (ha)", format="%.1f"),
        "landsat_pixels": st.column_config.NumberColumn("Landsat pixels"),
        "flag": st.column_config.TextColumn("Flag"), "flags": st.column_config.TextColumn("Why")})
    st.altair_chart(ladder_chart(t), width="stretch")
    show_caveats(r["caveats"])
    widest = max(r["corridors"])
    m = base_map(shape(r["corridors"][widest]).bounds, satellite=True)
    for i, b in sorted(enumerate(sorted(r["corridors"])), key=lambda x: -x[1]):
        add_geojson(m, r["corridors"][b], f"{buffer_label(b)} corridor",
                    color=CORRIDOR_COLORS[min(i, len(CORRIDOR_COLORS) - 1)], weight=1.5, fill_opacity=0.08)
    add_river(m)
    show_map(m, f"{key}_ladder_map", 460)
    with st.expander("Landsat seasons (widest corridor)"):
        st.dataframe(r["seasons"], hide_index=True)
    show_method(r["method"])
    with st.container(horizontal=True):
        download_csv(t, f"{key}_ladder")
        download_report("Nakatiya buffer ladder", r, f"{key}_ladder")


def render_timeline(r, key):
    t = r["trend"]
    flag_row(t["flag"], help="Theil-Sen slope of the vegetated share over the usable seasons; significant when the "
                             "Mann-Kendall p-value is under 0.05")
    c = st.columns(4)
    c[0].metric("Vegetated share trend", fmt(t["slope_pp_per_decade"], "+.1f", " pp / decade"))
    c[1].metric(f"Fitted change {t['first']}→{t['last']}" if t["first"] else "Fitted change",
                fmt(t["change_pp"], "+.1f", " pp"))
    c[2].metric("p-value (Mann-Kendall)", fmt(t["p_value"], ".3f"))
    c[3].metric("Usable seasons", t["seasons"], help="At least 5 are needed for a trend")
    if len(r["landsat"]):
        st.markdown(f"**Landsat shares of the {r['buffer_m']} m corridor, rabi season by season**")
        st.altair_chart(landsat_chart(r["landsat"], t), width="stretch")
        st.caption("Lines join the usable seasons; crosses mark seasons left out (rated poor, or fewer than two clear "
                   "peak-season scenes). Dashed: the Theil-Sen fit of the vegetated share.")
    left, right = st.columns(2)
    with left:
        st.markdown("**Built-up share of the corridor**")
        if len(r["builtup"]):
            st.altair_chart(builtup_chart(r["builtup"]), width="stretch")
        st.caption("Two products with different definitions: read each line on its own.")
    with right:
        st.markdown("**Land cover (Impact Observatory, 10 m)**")
        if len(r["landcover"]):
            st.altair_chart(landcover_chart(r["landcover"]), width="stretch")
    if r["missing"]:
        st.warning("Seasons that could not be composited: " + "; ".join(r["missing"]))
    show_caveats(r["caveats"])
    show_map(corridor_map(r["corridor"], key, f"{r['buffer_m']} m corridor"), f"{key}_timeline_map", 420)
    with st.expander("Tables"):
        st.dataframe(r["landsat"], hide_index=True)
        st.dataframe(r["builtup"], hide_index=True)
        st.dataframe(r["landcover"], hide_index=True)
    show_method(r["method"])
    with st.container(horizontal=True):
        download_csv(r["landsat"], f"{key}_landsat", "Landsat seasons (CSV)")
        download_csv(r["builtup"], f"{key}_builtup", "Built-up by year (CSV)")
        download_report("Nakatiya corridor timeline", r, f"{key}_timeline")


def render_water_watch(r, key):
    now, prev = r["latest"], r["previous"]
    flag_row(r["flag"], help=f"Change of {rv.CHANGE_THRESHOLD_PP:.0f} points or more between passes")
    c = st.columns(3)
    c[0].metric(f"Water-like, {now['date']}", fmt(now["water_like_pct"], ".1f", "%"))
    c[1].metric(f"Water-like, {prev['date']}" if prev else "Previous pass", fmt(prev["water_like_pct"], ".1f", "%")
                if prev else "none")
    c[2].metric("Change", fmt(r["water_like_change_pp"], "+.1f", " pp"))
    st.caption(f"Sentinel-1 {now['platform']} · relative orbit {now['relative_orbit']} ({now['orbit_state']}) · "
               f"{now['pixels']:,} pixels at 30 m · {buffer_label(r['buffer_m'])} corridor")
    show_map(corridor_map(r["corridor"], key, f"{r['buffer_m']} m corridor"), f"{key}_flood_map", 440)
    show_method(r["method"])
    download_report("Nakatiya flood / water watch", r, f"{key}_flood")


def render_riparian(r, key):
    flag_row(r["flag"], help=f"Flagged beyond ±{rv.RIPARIAN_THRESHOLD_PP:.0f} points; TOO FEW CLEAR LOOKS when under "
                             "half the corridor was seen clear in both windows")
    c = st.columns(4)
    c[0].metric(f"Vegetated, {r['window']}", fmt(r["vegetated_now_pct"], ".1f", "%"),
                delta(r["vegetated_change_pp"]))
    c[1].metric(f"Vegetated, {r['reference_window']}", fmt(r["vegetated_year_ago_pct"], ".1f", "%"))
    c[2].metric("Corridor compared", fmt(r["compared_pct"], ".0f", "%"), help="Pixels seen clear in both windows")
    c[3].metric("Clear looks", f"{r['looks_now']} / {r['looks_reference']}", help="This year / the year before")
    st.caption(f"Tree cover: {fmt(r['trees_pct'], '.1f', '%')} of the corridor (Impact Observatory {r['trees_year']}) · "
               f"median greenest-NDVI change {fmt(r['median_ndvi_change'], '+.3f')}")
    show_caveats(r.get("caveats"))
    overlays = [(r["ndvi_now"], r["grid"], f"Greenest NDVI, {r['window']}", -0.1, 0.9, RDYLGN, False),
                (r["ndvi_change"], r["grid"], "Greenest-NDVI change on a year earlier", -0.4, 0.4, BRBG, True)]
    show_map(corridor_map(r["corridor"], key, f"{r['buffer_m']} m corridor", overlays), f"{key}_riparian_map", 460)
    with st.expander("Looks used"):
        st.markdown(f"**{r['window']}**: {', '.join(r['dates_now'])}\n\n"
                    f"**{r['reference_window']}**: {', '.join(r['dates_reference'])}")
    show_method(r["method"])
    download_report("Nakatiya riparian vegetation health", r, f"{key}_riparian")


def render_alerts(r, key):
    flag_row(r["flag"])
    c = st.columns(4)
    c[0].metric("Bare / built candidates", f"{r['lost_green_ha']:.2f} ha", help="Green a year ago, bare since")
    c[1].metric("New water / wet", f"{r['new_wet_ha']:.2f} ha", help="Lost greenness with low SWIR reflectance")
    c[2].metric("Corridor seen", fmt(r["seen_pct"], ".0f", "%"), help="Two or more clear looks now, one a year ago")
    c[3].metric("Clear looks", f"{r['looks_now']} / {r['looks_reference']}", help="This window / a year earlier")
    st.caption(f"Latest window {r['window']} against {r['reference_window']} · {buffer_label(r['buffer_m'])} corridor")
    show_caveats(r["caveats"])
    m = corridor_map(r["corridor"], key, f"{r['buffer_m']} m corridor",
                     [(r["ndvi_change"], r["grid"], "Greenest-NDVI change on a year earlier", -0.4, 0.4, BRBG, True)])
    add_categorized(m, r["geojson"], "Candidate patches", "kind", PATCH_COLORS,
                    ["patch", "kind", "area_ha", "river_distance_m"],
                    ["Patch", "Kind", "Area (ha)", "To centreline (m)"], weight=2, fill_opacity=0.35)
    show_map(m, f"{key}_alerts_map", 480)
    if len(r["table"]):
        st.dataframe(r["table"], hide_index=True, column_config={
            "area_ha": st.column_config.NumberColumn("Area (ha)", format="%.2f"),
            "river_distance_m": st.column_config.NumberColumn("To centreline (m)", format="%.0f"),
            **{c: st.column_config.NumberColumn(format="%.5f") for c in ("lat", "lon")}})
    show_method(r["method"])
    with st.container(horizontal=True):
        if len(r["table"]):
            download_csv(r["table"], f"{key}_patches")
            download_geojson(r["geojson"], f"{key}_patches")
        download_report("Nakatiya construction / clearing alerts", r, f"{key}_alerts")


# ------------------------------------------------------------------------------------ Ask the Map

def run_action(parsed, ctx):
    """Run the analysis a parsed question maps to, with the sidebar's area and settings."""
    a, p = parsed["action"], parsed["params"]
    point, radius = reach_args(p.get("reach"))
    river, bbox, months, cloud = ctx["river"], ctx["bbox"], ctx["months"], ctx["cloud"]
    if a == "river_course":
        return course_facts(ctx["fc"])
    if a == "river_fields":
        return compute_fields_near(p["distance_m"], point, radius, months, cloud, river)
    if a == "river_flood":
        return cached_water_watch(point, radius, p["buffer_m"], river)
    if a == "river_alerts":
        return cached_alerts(point, radius, p["buffer_m"], 90, river)
    if a == "river_vegetation":
        return cached_riparian(point, radius, p["buffer_m"], None, river)
    if a == "river_ladder":
        return cached_ladder(point, radius, tuple(p["buffers"]), p["year_a"], p["year_b"], river)
    if a == "river_timeline":
        return cached_timeline(point, radius, p["buffer_m"], tuple(p["years"]), river)
    if a == "river_change":
        return cached_land_change(point, radius, p["buffer_m"], p["year_a"], p["year_b"], river)
    if a == "water":
        return compute_water(bbox, months, cloud)
    if a == "field":
        return compute_field(bbox, None, months, cloud, "Unknown")
    if a == "scout":
        return compute_scouting(bbox, months, cloud)
    if a == "fusion":
        return compute_fusion(bbox, months, cloud)
    if a == "change":
        return compute_radar(bbox, months, cloud)
    raise ValueError(f"No analysis for '{a}'")


RENDERERS = {"river_course": render_course, "river_fields": render_scouting, "river_flood": render_water_watch,
             "river_alerts": render_alerts, "river_vegetation": render_riparian, "river_ladder": render_ladder,
             "river_timeline": render_timeline, "river_change": render_land_change, "water": render_water,
             "field": render_field, "scout": render_scouting, "fusion": render_fusion, "change": render_radar,
             "qila": render_qila}


def _use_example():
    if st.session_state.get("ask_example"):
        st.session_state["ask_q"] = st.session_state["ask_example"]


# ------------------------------------------------------------------------------------------ pages

def page_overview(ctx):
    st.header(PAGES["overview"], divider="green")
    st.markdown("KhetOS turns free satellite data into **inspection signals** for farms and for the Nakatiya river "
                "corridor in Bareilly. Each module answers one question from open data and says how sure it can be. "
                "Nothing here is a legal, cadastral or agronomic determination.")
    course = course_facts(ctx["fc"])
    c = st.columns(4)
    c[0].metric("Main stem", fmt(course.get("main_stem_km"), ".1f", " km"),
                help="The Nakatiya as mapped, from its head east of Bhojipura to the Ramganga")
    c[1].metric("All mapped ways", fmt(course.get("total_km"), ".1f", " km"), help="Including side channels")
    c[2].metric("OSM ways", course.get("ways"))
    c[3].metric("Reaches", len(NAKATIYA_REACHES), help="Named reaches for the river analyses")
    m = base_map(REGION_BBOX)
    add_geojson(m, load_districts(), "Rohilkhand districts", color="#6D4C41", weight=1.5, fill_opacity=0.02,
                tooltip_fields=["district"], tooltip_aliases=["District"])
    add_river(m)
    add_geojson(m, rv.load_qila_candidate(), "Qila candidate (unverified)", color="#7B1FA2", weight=2, dash="6 6",
                show=False)
    reaches = folium.FeatureGroup(name="Analysis reaches")
    for name, ((lon, lat), km) in NAKATIYA_REACHES.items():
        folium.Circle([lat, lon], radius=km * 1000, color="#F9A825", weight=2, fill=False,
                      tooltip=f"{name} · {km} km radius").add_to(reaches)
    reaches.add_to(m)
    if course.get("head"):
        add_marker(m, course["head"][1], course["head"][0], "Nakatiya: mapped head (OpenStreetMap)", color="green",
                   icon="play")
    if course.get("confluence"):
        add_marker(m, course["confluence"][1], course["confluence"][0], "Nakatiya: confluence with the Ramganga",
                   color="darkblue", icon="flag")
    add_bbox(m, ctx["bbox"])
    show_map(m, "overview_map", 560)
    st.caption(f"River geometry: {'refreshed' if ctx['river'] else 'bundled'} OpenStreetMap extract, retrieved "
               f"{course.get('retrieved')}. Purple box: the area of interest set in the sidebar.")
    st.info(f"**Nakatiya course.** OpenStreetMap maps it from a head east of Bhojipura, past the south-east edge of "
            f"Bareilly, to the Ramganga. {course['upstream_status']} Reported source: {course['reported_source']}.",
            icon="🌊")
    c1, c2 = st.columns(2)
    refresh = c1.button("Refresh the river from OpenStreetMap", icon=":material/sync:")
    reset = ctx["river"] is not None and c2.button("Use the bundled extract", icon=":material/undo:")
    if refresh:
        with st.spinner("Querying the Overpass API...", show_time=True):
            try:
                st.session_state["river_fc"] = rv.fetch_nakatiya_osm(timeout=45)
            except Exception as exc:
                st.error(error_text(exc))
            else:
                st.rerun()
    if reset:
        st.session_state.pop("river_fc", None)
        st.rerun()
    st.subheader("Modules")
    st.markdown("""
| Module | Question it answers | Main data |
|---|---|---|
| Change Radar | Is the area's greenness or canopy water moving unusually? | Sentinel-2 |
| Field Scanner | What does the latest imagery say about one field? | Sentinel-2, Open-Meteo |
| Water / Moisture | Is the canopy dry, and has the weather made up for it? | Sentinel-2 NDMI, Open-Meteo |
| SAR + Optical Fusion | Do radar and optical agree on the latest change? | Sentinel-1, Sentinel-2 |
| Scouting Queue | Which cells to visit first? | Sentinel-2, Impact Observatory, Isolation Forest |
| Ask the Map | Plain-language questions, routed to the analyses | all of the above |
| Nakatiya River Observatory | Built-up over the years, change flags, flood watch, riparian health, construction alerts | WSF Evolution, Impact Observatory, Landsat, Sentinel-1/2, JRC |
| Land Change / Riparian | Change by distance from the river, and its history since 1990 | as above |
| River Water Watch | How much water the river carries, season by season and over the decades; field readings | GEOGLOWS model, Sentinel-2, your float measurements |
""")
    with st.expander("Reported context (press and plan documents, not verified by KhetOS)", icon=":material/news:"):
        st.markdown(bullets(f"**{outlet}**, {when}: {text} [Link]({url})" for when, outlet, text, url in NEWS_CONTEXT))
    with st.expander("Extensions: FarmVibes.AI", icon=":material/extension:"):
        cfg = farmvibes_configuration()
        st.markdown("KhetOS runs without a FarmVibes.AI cluster. Set `FARMVIBES_BASE_URL` to connect one; these "
                    "workflows would slot in behind the same result dictionaries:")
        st.markdown(bullets(explain_extension_points()))
        st.caption(f"Status: {'connected to ' + cfg['base_url'] if cfg['enabled'] else 'not configured'}")


def page_radar(ctx):
    st.header(PAGES["change-radar"], divider="green")
    st.caption("Sentinel-2 NDVI (greenness) and NDMI (canopy water) of the area of interest, scene by scene, and "
               "whether the latest move is unusual for this area.")
    params = (ctx["bbox"], ctx["months"], ctx["cloud"])
    if st.button("Run change radar", type="primary", icon=":material/radar:"):
        run_into("res_radar", params, "Screening Sentinel-2 scenes...", compute_radar, *params)
    show_stored("res_radar", params, render_radar, "radar")


def page_field(ctx):
    st.header(PAGES["field-scanner"], divider="green")
    st.caption("Draw a field with the polygon or rectangle tool, then scan it: the latest clear Sentinel-2 scene, the "
               "change since the previous one, 14 days of weather and an evidence brief.")
    geom = st.session_state.get("field_geom")
    m = base_map(shape(geom).bounds if geom else ctx["bbox"], satellite=True)
    Draw(export=False, position="topleft",
         draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False,
                       "polygon": {"showArea": True}, "rectangle": {"showArea": True}},
         edit_options={"edit": False, "remove": False}).add_to(m)
    add_bbox(m, ctx["bbox"])
    if geom:
        add_geojson(m, geom, "Your field", color="#FFEB3B", weight=3, fill_opacity=0.08)
    add_layer_control(m)
    out = st_folium(m, key="field_draw", height=460, use_container_width=True,
                    returned_objects=["last_active_drawing"])
    drawn = ((out or {}).get("last_active_drawing") or {}).get("geometry")
    # The component keeps returning its last drawing, so only a drawing not seen before replaces the field.
    if drawn and drawn != st.session_state.get("field_seen"):
        st.session_state["field_seen"] = st.session_state["field_geom"] = geom = drawn
    c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
    crop = c1.selectbox("Crop, as you know it", CROPS, key="crop", persist_state="session")
    if geom:
        area_ha = abs(_GEOD.geometry_area_perimeter(shape(geom))[0]) / 1e4
        c2.metric("Field area", f"{area_ha:,.1f} ha")
        if c3.button("Clear field", icon=":material/delete:"):
            st.session_state.pop("field_geom", None)
            st.rerun()
        if area_ha > 2500:
            st.warning("That is a very large field: the scan reports one median for all of it.")
    else:
        c2.caption("No field drawn: the scan covers the whole area of interest.")
    bbox = shape(geom).bounds if geom else ctx["bbox"]
    params = (bbox, geom, ctx["months"], ctx["cloud"], crop)
    if st.button("Scan field", type="primary", icon=":material/search:"):
        run_into("res_field", params, "Finding the latest clear scene over the field...", compute_field, *params)
    show_stored("res_field", params, render_field, "field")


def page_water(ctx):
    st.header(PAGES["water"], divider="green")
    st.caption("Canopy water (Sentinel-2 NDMI) with the last 14 days of rain, reference evapotranspiration and "
               "modelled soil moisture at the centre of the area of interest.")
    params = (ctx["bbox"], ctx["months"], ctx["cloud"])
    if st.button("Check water signals", type="primary", icon=":material/water_drop:"):
        run_into("res_water", params, "Reading Sentinel-2 and the weather...", compute_water, *params)
    show_stored("res_water", params, render_water, "water")


def page_fusion(ctx):
    st.header(PAGES["fusion"], divider="green")
    st.caption("Do Sentinel-2 (optical NDVI) and Sentinel-1 (radar VH backscatter, same orbit) agree on the latest "
               "change over the area's cropland?")
    params = (ctx["bbox"], ctx["months"], ctx["cloud"])
    if st.button("Compare radar and optical", type="primary", icon=":material/compare:"):
        run_into("res_fusion", params, "Reading Sentinel-2 scenes and Sentinel-1 passes...", compute_fusion, *params)
    show_stored("res_fusion", params, render_fusion, "fusion")


def page_scouting(ctx):
    st.header(PAGES["scouting"], divider="green")
    st.caption("Ranks cells by how unusual their latest Sentinel-2 change is: robust z-scores, plus an Isolation "
               "Forest when there are 12 or more cropland cells. HIGH needs both an outlier and a downward move. It "
               "says where to look first, not what is wrong.")
    scope = st.segmented_control("Scope", ["Area of interest", "Near the Nakatiya"], default="Area of interest",
                                 required=True, key="scout_scope", persist_state="session")
    if scope == "Near the Nakatiya":
        c1, c2 = st.columns(2)
        reach = c1.selectbox("Reach", REACH_OPTIONS, key="scout_reach", persist_state="session")
        dist = c2.select_slider("Within", [100, 250, 500, 1000], value=500, format_func=lambda d: f"{d} m",
                                key="scout_dist", persist_state="session")
        point, radius = reach_args(reach)
        args = (dist, point, radius, ctx["months"], ctx["cloud"], ctx["river"])
        fn, params = compute_fields_near, ("near", *args)
    else:
        args = (ctx["bbox"], ctx["months"], ctx["cloud"])
        fn, params = compute_scouting, ("aoi", *args)
    if st.button("Build scouting queue", type="primary", icon=":material/format_list_numbered:"):
        run_into("res_scout", params, "Scoring cells on the latest two clear scenes...", fn, *args)
    show_stored("res_scout", params, render_scouting, "scout")


def page_ask(ctx):
    st.header(PAGES["ask"], divider="green")
    st.caption("Ask in plain language. A rule-based parser (offline, no API key) maps the question to one of the "
               "analyses; every number shown is computed from the data, and questions it cannot map get help "
               "instead of a guess.")
    st.pills("Examples", ai.EXAMPLES, key="ask_example", on_change=_use_example)
    q = st.text_input("Question", key="ask_q", persist_state="session",
                      placeholder="e.g. Any new construction within 100 m of the Nakatiya in the city?")
    if st.button("Ask", type="primary", icon=":material/send:", disabled=not (q or "").strip()):
        parsed = ai.parse_question(q)
        entry = {"question": q, "parsed": parsed}
        if parsed["action"] not in ("help", "qila"):
            with st.spinner(parsed["interpretation"], show_time=True):
                try:
                    entry["result"] = run_action(parsed, ctx)
                except Exception as exc:
                    log.warning("Ask the Map failed on %r", q, exc_info=True)
                    entry["error"] = error_text(exc)
        st.session_state["ask"] = entry
    entry = st.session_state.get("ask")
    if not entry:
        return
    parsed = entry["parsed"]
    st.markdown(f"**Question:** {entry['question']}")
    st.info(parsed["interpretation"], icon="🧭")
    if parsed["action"] == "help":
        st.markdown("Questions about the Nakatiya can name a distance (e.g. *within 100 m*), years (*since 2016*) and "
                    "a reach (*urban*, *Bhojipura*, *downstream*). Farm questions use the area of interest in the "
                    "sidebar. Try:\n\n" + bullets(ai.EXAMPLES))
    elif "error" in entry:
        st.error(entry["error"])
    else:
        RENDERERS[parsed["action"]](entry.get("result"), "ask")


def built_slider(point, radius, buffer, river):
    st.subheader("Built-up extent, 1984 to today")
    st.caption("Validated products only: DLR WSF Evolution (settled by each year, 1985-2015) and Impact Observatory "
               "10 m built area (from 2017). 1984 shows the 1985 extent (settled by 1985 or earlier), 2016 the 2015 "
               "extent, and years after the latest map the latest map. The two products define built-up land "
               "differently, so compare years within one product.")
    year = st.slider("Year", 1984, date.today().year, 2025, key="obs_year", persist_state="session")
    try:
        with st.spinner("Reading the built-up maps...", show_time=True):
            ext = cached_built_extent(year, point, radius, buffer, river)
    except Exception as exc:
        st.error(f"Built-up map unavailable: {error_text(exc)}")
        return
    left, right = st.columns([1, 3])
    left.metric(f"Built-up, {year}", fmt(ext["built_pct"], ".1f", "%"), help="Share of the corridor")
    left.caption(ext["label"])
    with right:
        m = base_map(shape(ext["corridor"]).bounds, satellite=True)
        add_index_overlay(m, np.where(ext["built"], 1.0, np.nan), ext["grid"], ext["label"], 0, 1,
                          ["#D32F2F", "#D32F2F"], opacity=0.8, legend=False)
        # Thin lines: a 10-100 m corridor is only a few screen pixels wide at the default zoom.
        add_corridor(m, ext["corridor"], f"{buffer} m corridor", weight=1)
        add_river(m, weight=1)
        show_map(m, "obs_built_map", 460)


def tab_change(point, radius, buffer, river):
    st.caption("Built-up growth from validated settlement and land-cover maps; water and vegetation from Landsat "
               "rabi-season composites (each end the median of its season and its neighbours); the 1984-2020 water "
               "history from JRC.")
    c1, c2 = st.columns(2)
    ya = c1.selectbox("From rabi season", SEASONS, index=SEASONS.index(2017), key="obs_ya", persist_state="session")
    yb = c2.selectbox("To rabi season", SEASONS, index=len(SEASONS) - 1, key="obs_yb", persist_state="session")
    params = (point, radius, buffer, ya, yb, river)
    if ya >= yb:
        st.caption("Pick a start season before the end season.")
    if st.button("Run change flags", type="primary", disabled=ya >= yb, key="obs_change_run"):
        run_into("res_obs_change", params, "Compositing Landsat seasons and reading the built-up maps (about a "
                 "minute on the first run)...", cached_land_change, *params)
    show_stored("res_obs_change", params, render_land_change, "obs")


def tab_flood(point, radius, river):
    st.caption("Sentinel-1 radar sees through monsoon cloud: the open-water-like share of the corridor on the latest "
               "pass against the previous pass on the same orbit.")
    width = st.select_slider("Corridor half-width", [250, 500, 1000, 2000], value=500, format_func=buffer_label,
                             key="obs_flood_buffer", persist_state="session")
    params = (point, radius, width, river)
    if st.button("Run water watch", type="primary", key="obs_flood_run"):
        run_into("res_obs_flood", params, "Reading the latest Sentinel-1 passes...", cached_water_watch, *params)
    show_stored("res_obs_flood", params, render_water_watch, "obs")


def tab_riparian(point, radius, buffer, river):
    st.caption("Vegetated share (greenest Sentinel-2 NDVI of 0.5 or more) of the corridor over 1 January-31 March "
               f"against the same window a year earlier; flagged beyond ±{rv.RIPARIAN_THRESHOLD_PP:.0f} points.")
    years = list(range(2019, rv.latest_rabi_peak_year() + 1))
    year = st.selectbox("Rabi peak window of", years, index=len(years) - 1, key="obs_rip_year",
                        persist_state="session")
    if buffer < 30:
        st.caption("Corridors under 30 m hold only a few 10 m pixels across: read the shares as indicative.")
    params = (point, radius, buffer, year, river)
    if st.button("Run riparian health", type="primary", key="obs_rip_run"):
        run_into("res_obs_rip", params, "Compositing Sentinel-2 looks for both windows...", cached_riparian, *params)
    show_stored("res_obs_rip", params, render_riparian, "obs")


def tab_alerts(point, radius, buffer, river):
    st.caption("Land that was green in the same window a year ago and has stayed bare through the latest window: "
               "construction, clearing, earthworks, brick kilns, dumping or fallow alike. Each candidate needs an "
               "imagery or field check.")
    window = st.segmented_control("Window", [60, 90, 120], default=90, required=True, format_func=lambda d: f"{d} days",
                                  key="obs_alert_window", persist_state="session")
    params = (point, radius, buffer, window, river)
    if st.button("Run construction alerts", type="primary", key="obs_alert_run"):
        run_into("res_obs_alerts", params, "Compositing Sentinel-2 looks for both windows...", cached_alerts, *params)
    show_stored("res_obs_alerts", params, render_alerts, "obs")


def page_observatory(ctx):
    st.header(PAGES["river"], divider="blue")
    st.caption("The Nakatiya corridor: built-up land over the years, change flags between two seasons, flood watch, "
               "riparian vegetation and new clearing. Change flags / encroachment-risk signals, not legal "
               "determinations; buffers are measured from the OpenStreetMap centreline, not surveyed banks.")
    c1, c2 = st.columns([2, 3])
    reach = c1.selectbox("Reach", REACH_OPTIONS, key="obs_reach", persist_state="session")
    buffer = c2.select_slider("Corridor half-width from the centreline", BUFFER_OPTIONS, value=100,
                              format_func=buffer_label, key="obs_buffer", persist_state="session")
    point, radius = reach_args(reach)
    river = ctx["river"]
    built_slider(point, radius, buffer, river)
    tabs = st.tabs(["Change flags", "Flood / water watch", "Riparian health", "Construction alerts"],
                   key="obs_tab", on_change="rerun")
    if tabs[0].open:
        with tabs[0]:
            tab_change(point, radius, buffer, river)
    if tabs[1].open:
        with tabs[1]:
            tab_flood(point, radius, river)
    if tabs[2].open:
        with tabs[2]:
            tab_riparian(point, radius, buffer, river)
    if tabs[3].open:
        with tabs[3]:
            tab_alerts(point, radius, buffer, river)


def page_land_change(ctx):
    st.header(PAGES["land-change"], divider="blue")
    st.caption("How the land within set distances of the Nakatiya has changed, and its history since 1990. Change "
               "flags / encroachment-risk signals, not legal determinations.")
    reach = st.selectbox("Reach", REACH_OPTIONS, key="lc_reach", persist_state="session")
    point, radius = reach_args(reach)
    river = ctx["river"]
    ladder, timeline = st.tabs(["Buffer ladder", "Timeline since 1990"], key="lc_tab", on_change="rerun")
    if ladder.open:
        with ladder:
            st.caption("The same comparison for nested corridors, from one read of each dataset. Buffers under "
                       f"{rv.MIN_LANDSAT_BUFFER_M} m are narrower than 1.5 Landsat pixels: the 10 m land-cover "
                       "map is the meaningful source there.")
            buffers = st.multiselect("Corridor half-widths", BUFFER_OPTIONS, default=[10, 25, rv.GREEN_BELT_M, 50, 100],
                                     format_func=buffer_label, key="lc_buffers", persist_state="session")
            c1, c2 = st.columns(2)
            ya = c1.selectbox("From rabi season", SEASONS, index=SEASONS.index(2017), key="lc_ya",
                              persist_state="session")
            yb = c2.selectbox("To rabi season", SEASONS, index=len(SEASONS) - 1, key="lc_yb", persist_state="session")
            params = (point, radius, tuple(sorted(buffers)), ya, yb, river)
            if st.button("Run buffer ladder", type="primary", disabled=not buffers or ya >= yb, key="lc_ladder_run"):
                run_into("res_ladder", params, "Compositing Landsat seasons and reading the built-up maps...",
                         cached_ladder, *params)
            show_stored("res_ladder", params, render_ladder, "lc")
    if timeline.open:
        with timeline:
            st.caption("Landsat water and vegetation for each chosen rabi season, settled area for every year "
                       "1985-2015 and 10 m land cover for every year 2017-2025, with a robust trend of the vegetated "
                       "share.")
            c1, c2 = st.columns([1, 3])
            width = c1.selectbox("Corridor half-width", BUFFER_OPTIONS, index=BUFFER_OPTIONS.index(100),
                                 format_func=buffer_label, key="tl_buffer", persist_state="session")
            years = c2.multiselect("Rabi seasons", SEASONS, default=list(rv.default_timeline_years()),
                                   key="tl_years", persist_state="session")
            params = (point, radius, width, tuple(sorted(years)), river)
            if st.button("Run timeline", type="primary", disabled=len(years) < 2, key="tl_run"):
                run_into("res_timeline", params, f"Compositing {len(years)} Landsat seasons and reading the "
                         "settlement and land-cover maps (1-3 minutes on the first run)...", cached_timeline, *params)
            show_stored("res_timeline", params, render_timeline, "lc")


# ----------------------------------------------------------------------------------- river water watch

FLOW_BANNER = ("**Modelled, not measured.** These flows come from a global weather-driven model (GEOGLOWS). They "
               "leave out the city, sewage, irrigation, canals and groundwater pumping, and no gauge measures the "
               "Nakatiya. Use them for timing and seasons, not as the river's true discharge.")
WORKBOOK = flow.DATA_DIR / "nakatiya_flow_history.xlsx"
WIDTH_REACHES = {"upper": "Upper (30.1 km)", "urban": "Urban (14.5 km)", "lower": "Lower (14.6 km)"}
FLOW_COLORS = {"above": "#2E7D32", "entering": "#9E9D24", "khajuria": "#00897B", "below": "#EF6C00",
               "mouth": "#0277BD", "ramganga": "#6D4C41"}


@st.cache_data(show_spinner=False)
def flow_series():
    """The bundled daily flows without the warm-up years."""
    return flow.load_daily().loc[f"{flow.WARMUP_YEARS[-1] + 1}-01-01":]


@st.cache_data(show_spinner=False)
def flow_tables(key):
    """Every table the page shows for one segment, built once."""
    s = flow_series()[key].dropna()
    ann = flow.annual_table(s)
    complete = ann[ann["complete"]]
    base = complete.loc[flow.BASELINE[0]:flow.BASELINE[1]]
    clim = flow.climatology(s)
    trends = flow.trend_table(s)
    trends["reading"] = [flow.trend_reading(t) for t in trends.to_dict("records")]
    return {"annual": complete, "base": base, "area": flow.SEGMENTS[key]["area_km2"], "clim": clim,
            "trends": trends, "depend": flow.dependable(base["volume_mcm"]),
            "monsoon_share": float(clim.loc[6:9, "share_pct"].sum())}


@st.cache_data(ttl=3600, max_entries=8, show_spinner=False)
def cached_flow_forecast(river_id):
    return flow.fetch_forecast(river_id)


def annual_flow_chart(annual, key):
    d = annual.reset_index()
    return (alt.Chart(d).mark_bar(color=FLOW_COLORS[key])
            .encode(x=alt.X("year:O", title=None, axis=alt.Axis(labelOverlap=True, values=list(d["year"][::5]))),
                    y=alt.Y("volume_mcm:Q", title="Water carried in the year (million m³)"),
                    tooltip=[alt.Tooltip("year:O", title="Year"),
                             alt.Tooltip("volume_mcm:Q", title="Volume (million m³)", format=".1f"),
                             alt.Tooltip("mean_m3s:Q", title="Mean flow (m³/s)", format=".2f")])
            .properties(height=300))


def average_year_chart(clim, key):
    d = clim.reset_index()
    d["month_name"] = pd.to_datetime(d["month"], format="%m").dt.strftime("%b")
    order = list(d["month_name"])
    x = alt.X("month_name:O", sort=order, title=None)
    band = (alt.Chart(d).mark_area(opacity=0.25, color=FLOW_COLORS[key])
            .encode(x=x, y=alt.Y("p10_m3s:Q", title="Mean flow (m³/s)"), y2="p90_m3s:Q"))
    line = (alt.Chart(d).mark_line(point=True, color=FLOW_COLORS[key])
            .encode(x=x, y="mean_m3s:Q",
                    tooltip=[alt.Tooltip("month_name:O", title="Month"),
                             alt.Tooltip("mean_m3s:Q", title="Mean (m³/s)", format=".2f"),
                             alt.Tooltip("p10_m3s:Q", title="Dry year, 10th pct", format=".2f"),
                             alt.Tooltip("p90_m3s:Q", title="Wet year, 90th pct", format=".2f"),
                             alt.Tooltip("share_pct:Q", title="Share of the year's water (%)", format=".1f")]))
    return (band + line).properties(height=280)


def forecast_chart(fc, key):
    d = fc.reset_index()
    band = (alt.Chart(d).mark_area(opacity=0.25, color=FLOW_COLORS[key])
            .encode(x=alt.X("time:T", title=None), y=alt.Y("low:Q", title="Flow (m³/s)"), y2="high:Q"))
    line = (alt.Chart(d).mark_line(color=FLOW_COLORS[key])
            .encode(x="time:T", y="median:Q",
                    tooltip=[alt.Tooltip("time:T", title="Time (IST)"),
                             alt.Tooltip("median:Q", title="Median (m³/s)", format=".2f"),
                             alt.Tooltip("low:Q", title="Low (m³/s)", format=".2f"),
                             alt.Tooltip("high:Q", title="High (m³/s)", format=".2f")]))
    return (band + line).properties(height=260)


def width_chart(widths):
    d = widths.copy()
    d["reach_name"] = d["reach"].map(WIDTH_REACHES)
    d["quality"] = np.where(d["reliable"], "reliable", "unreliable")
    return (alt.Chart(d).mark_point(filled=True, size=70, opacity=0.85)
            .encode(x=alt.X("date:T", title=None), y=alt.Y("width_m:Q", title="Open-water width (m)"),
                    color=alt.Color("reach_name:N", title=None),
                    tooltip=[alt.Tooltip("reach_name:N", title="Reach"), alt.Tooltip("date:T", title="Date"),
                             alt.Tooltip("width_m:Q", title="Width (m)", format=".1f"),
                             alt.Tooltip("period:N", title="Period"), alt.Tooltip("quality:N", title="Quality")])
            .properties(height=300))


def flow_headline(tab, segment):
    base = tab["base"]
    mean_volume = base["volume_mcm"].mean()
    rows = [st.columns(2), st.columns(2)]
    rows[0][0].metric("Yearly water (1991-2020 mean)", fmt(mean_volume, ".1f", " million m³"),
                      help=f"Average of the complete years of the 30-year climate normal: "
                           f"{fmt(flow.runoff_mm(mean_volume, tab['area']), '.0f', ' mm')} over the "
                           f"{tab['area']:.0f} km² the model drains to this point.")
    rows[0][1].metric("Mean flow", fmt(base["mean_m3s"].mean(), ".2f", " m³/s"),
                      help="The median day is far lower: a few flood days carry much of the water.")
    rows[1][0].metric("Dry-year water (90% dependable)", fmt(tab["depend"][90], ".1f", " million m³"),
                      help="Matched or beaten in 9 years out of 10.")
    rows[1][1].metric("Share in June-September", fmt(tab["monsoon_share"], ".0f", "%"),
                      help="Share of the year's water that comes in the monsoon months.")
    st.caption(f"{segment['name']}: GEOGLOWS segment {segment['id']}, contributing area {segment['area_km2']:.1f} km²"
               + (" (estimated from the flows above and below it)." if segment.get("area_estimated") else "."))


def tab_modelled_flow():
    key = st.selectbox("Point on the river", flow.NAKATIYA_KEYS, index=flow.NAKATIYA_KEYS.index("mouth"),
                       format_func=lambda k: f"{flow.SEGMENTS[k]['name']} (km {flow.SEGMENTS[k]['km']})",
                       key="fw_segment", persist_state="session")
    segment = flow.SEGMENTS[key]
    tab = flow_tables(key)
    flow_headline(tab, segment)
    info = flow.snapshot_info()
    st.subheader("Water carried each year")
    st.altair_chart(annual_flow_chart(tab["annual"], key), width="stretch")
    st.caption(f"Complete calendar years {tab['annual'].index[0]}-{tab['annual'].index[-1]}. The model's first "
               f"two years ({flow.WARMUP_YEARS[0]}-{flow.WARMUP_YEARS[-1]}) are left out as a precaution. Snapshot "
               f"retrieved {info['retrieved']}; last day {info['last']}.")
    st.subheader("The average year")
    st.altair_chart(average_year_chart(tab["clim"], key), width="stretch")
    st.caption("Line: mean flow of each month over 1991-2020. Band: the month's 10th to 90th percentile across the "
               "years, so a dry year sits near the bottom and a wet one near the top.")
    st.subheader("Is the river changing?")
    t = tab["trends"][["series", "period", "n", "pct_per_decade", "p_persist", "reading"]].rename(columns={
        "series": "Series", "period": "Years", "n": "Years used", "pct_per_decade": "Change per decade (%)",
        "p_persist": "p-value", "reading": "Reading"})
    st.dataframe(t, hide_index=True, column_config={
        "Change per decade (%)": st.column_config.NumberColumn(format="%+.1f"),
        "p-value": st.column_config.NumberColumn(format="%.3f",
                                                 help="After allowing for wet years following wet years.")})
    st.caption("Theil-Sen slope and Mann-Kendall test, adjusted for year-to-year persistence. A rise in the "
               "*modelled* dry-season flow since 1985 goes with falling ERA5 evaporative demand, not with more "
               "rain, and this model cannot see built-up land, sewage or pumping: it cannot confirm or rule out "
               "a construction effect on the real river.")
    st.subheader("Next 15 days")
    st.caption("The model's own ensemble forecast (51 weather runs). It carries the same limits as the history.")
    if st.button("Load the 15-day forecast", key="fw_forecast_btn", icon=":material/cloud_download:"):
        run_into("res_flow_forecast", (key,), "Fetching the GEOGLOWS forecast...", cached_flow_forecast, segment["id"])
    entry = st.session_state.get("res_flow_forecast")
    if entry and entry["params"] == (key,):
        if "error" in entry:
            st.error(entry["error"])
        else:
            fc = entry["result"]
            st.altair_chart(forecast_chart(fc, key), width="stretch")
            st.caption(f"Median and the range of the 51 ensemble members, {fc.index[0]:%d %b %H:%M} to "
                       f"{fc.index[-1]:%d %b %H:%M} India Standard Time.")
            download_csv(fc.reset_index(), f"river_forecast_{key}", "Forecast (CSV)")
    elif entry:
        st.info("The point on the river has changed: load the forecast again.", icon="🔁")
    rain_forecast_section(key)
    st.divider()
    with st.container(horizontal=True):
        if WORKBOOK.exists():
            st.download_button("Historical flow workbook (Excel)", WORKBOOK.read_bytes(), file_name=WORKBOOK.name,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key="fw_workbook", on_click="ignore", icon=":material/table_view:")
        download_csv(tab["annual"].reset_index(), f"river_flow_{key}_annual", "Yearly table (CSV)")
    show_method("Daily flows are the GEOGLOWS v2 retrospective run: ECMWF ERA5 runoff routed down the TDX-Hydro river "
                "network, from 1940. The public flows are **not bias-corrected**. Volume = flow × 86,400 s per day, "
                "summed. Complete years only. The 7-day low is the lowest 7-day mean flow of the year.")


RAIN_MODEL_NAMES = {"honest (no future rain)": "Without a rain forecast",
                    "ECMWF rain forecast": "With the ECMWF rain forecast",
                    "BEST CASE (rain that fell)": "Ceiling: the rain that actually fell"}


def rain_hindcast_chart(d):
    long = d.melt(id_vars=["target_date"],
                  value_vars=["geoglows_m3s", "forecast_m3s", "no_rain_forecast_m3s"], var_name="series",
                  value_name="flow")
    long["series"] = long["series"].map({"geoglows_m3s": "GEOGLOWS model",
                                         "forecast_m3s": "With rain forecast",
                                         "no_rain_forecast_m3s": "Without rain forecast"})
    band = (alt.Chart(d).mark_area(opacity=0.2, color=FLOW_COLORS["mouth"])
            .encode(x=alt.X("target_date:T", title=None), y=alt.Y("forecast_low_cal_m3s:Q", title="Flow (m³/s)"),
                    y2="forecast_high_cal_m3s:Q"))
    lines = (alt.Chart(long).mark_line(strokeWidth=1.4)
             .encode(x="target_date:T", y="flow:Q",
                     color=alt.Color("series:N", title=None, legend=alt.Legend(orient="bottom"),
                                     scale=alt.Scale(range=["#444444", FLOW_COLORS["mouth"], "#d08a2c"])),
                     strokeDash=alt.StrokeDash("series:N", legend=None,
                                               scale=alt.Scale(range=[[1, 0], [1, 0], [4, 3]])),
                     tooltip=[alt.Tooltip("target_date:T", title="Day"), alt.Tooltip("series:N", title="Series"),
                              alt.Tooltip("flow:Q", title="Flow (m³/s)", format=".2f")]))
    return (band + lines).properties(height=300)


def rain_forecast_section(key):
    st.subheader("Our rain-forecast model (experimental)")
    if key != "mouth":
        st.caption("Built for the whole river (the point at the Ramganga) only: choose that point above to see it.")
        return
    st.caption("A machine-learning model (gradient-boosted trees) that forecasts the flow 1, 3 or 7 days ahead "
               "from the last 90 days of flow, rain, evaporation and soil moisture, plus the rain the ECMWF "
               "(European Centre for Medium-Range Weather Forecasts) forecast for the days ahead. It learned from "
               "1942-2023 and was then tested on days it never saw, from March 2024, using the rain forecasts as "
               "they were actually issued.")
    hind, scores = flow.load_rain_hindcast(), flow.load_rain_scores()
    horizon = st.segmented_control("Days ahead", [1, 3, 7], default=3, key="fw_rain_h", persist_state="session",
                                   format_func=lambda h: f"{h} day{'s' if h > 1 else ''}") or 3
    d = hind[hind["horizon_days"] == horizon]
    years = sorted(d["target_date"].dt.year.unique())
    year = st.pills("Year", years, default=years[-2] if len(years) > 1 else years[-1], key="fw_rain_year",
                    persist_state="session") or years[-1]
    st.altair_chart(rain_hindcast_chart(d[d["target_date"].dt.year == year]), width="stretch")
    st.caption("Each point is the forecast made that many days earlier for that day. Shaded: the model's 10-90 % "
               "band with the rain forecast, widened by conformal calibration (below).")
    s = scores[scores["horizon_days"] == horizon].assign(model=lambda t: t["model"].map(RAIN_MODEL_NAMES))
    s = s[["model", "NSE", "NSE_monsoon", "PBIAS_%", "coverage_10_90"]].rename(columns={
        "model": "Model", "NSE": "Score, all days", "NSE_monsoon": "Score, June-September",
        "PBIAS_%": "Water bias (%)", "coverage_10_90": "Days inside the 10-90 % band"})
    st.dataframe(s, hide_index=True, column_config={
        "Score, all days": st.column_config.NumberColumn(format="%.2f", help=(
            "Nash-Sutcliffe efficiency: 1 is perfect, 0 is no better than the average flow.")),
        "Score, June-September": st.column_config.NumberColumn(format="%.2f"),
        "Water bias (%)": st.column_config.NumberColumn(format="%+.0f", help="Negative: too little water."),
        "Days inside the 10-90 % band": st.column_config.NumberColumn(format="percent", help="Should be near 80 %.")})
    c = flow.load_rain_calibration().set_index("horizon_days").loc[horizon]
    st.markdown(f"**Is the band honest?** Straight from the model, only {c['coverage_before']:.0%} of test days "
                f"fall inside the 10-90 % band (it should be 80 %), because the model learned from exact rain. "
                f"Conformal calibration widens it by an amount chosen on 2024 alone, separately for June-September "
                f"and the rest of the year. On 2025-2026, days it never saw: **{c['coverage_after']:.0%}** inside "
                f"({c['coverage_after_monsoon']:.0%} in the monsoon, {c['coverage_after_dry']:.0%} outside it).")
    st.warning("Judged against GEOGLOWS, not the river: this shows a rain forecast sharpens a 1-3 day forecast, not "
               "how well the real Nakatiya can be predicted. Only about two and a half monsoons are in the test. It "
               "is not run live, because the flow and weather it starts from arrive about a week late.", icon="🧪")
    download_csv(d, f"river_rain_forecast_test_{horizon}d", "Test forecasts (CSV)")


def tab_widths():
    st.caption("Open-water width of three reaches on clear Sentinel-2 dates, 2018-2025, found by splitting each "
               "10 m pixel into water and land (sub-pixel unmixing). Width is **not** flow: it tells you when the "
               "channel is wide or narrow, while depth and speed stay unknown.")
    widths = flow.load_widths()
    only = st.toggle("Only reliable dates", value=True, key="fw_reliable", persist_state="session",
                     help="Unreliable dates borrowed the water signature of another date or were contaminated.")
    shown = widths[widths["reliable"]] if only else widths
    st.altair_chart(width_chart(shown), width="stretch")
    summary = (widths[widths["reliable"]].assign(reach=lambda d: d["reach"].map(WIDTH_REACHES))
               .groupby("reach").agg(dates=("date", "size"), median_width_m=("width_m", "median"),
                                     narrowest_m=("width_m", "min"), widest_m=("width_m", "max")).reset_index())
    summary.columns = ["Reach", "Reliable dates", "Median width (m)", "Narrowest (m)", "Widest (m)"]
    st.dataframe(summary, hide_index=True, column_config={
        c: st.column_config.NumberColumn(format="%.1f") for c in summary.columns[2:]})
    with st.expander(f"All dates shown ({len(shown)})"):
        st.dataframe(shown.drop(columns=["scene"]), hide_index=True)
    download_csv(widths, "river_open_water_width", "Widths (CSV)")
    st.warning("Widths carry about ±0.5 m of noise, and most reaches are only a few pixels wide. The upper reach "
               "changes with crops and weeds as much as with water. Read the pattern between seasons, not a single "
               "date.", icon=":material/straighten:")


def field_reading_form():
    """The float-method form: (submitted, the values typed)."""
    with st.form("fw_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        when = c1.date_input("Date", value=date.today(), max_value=date.today(), key="fw_date")
        site = c2.text_input("Site", placeholder="for example: bridge at Bhojipura", key="fw_site")
        c1, c2 = st.columns(2)
        width = c1.number_input("Water width (m)", 0.0, 500.0, 0.0, 0.1, key="fw_width",
                                help="Edge of the water to edge of the water.")
        distance = c2.number_input("Float distance (m)", 0.0, 500.0, 10.0, 0.5, key="fw_distance",
                                   help="Length of the straight stretch the float travels.")
        depths = st.text_input("Depths across the river (m), evenly spaced", placeholder="0.3, 0.5, 0.6, 0.4",
                               key="fw_depths", help="Measure at equal spacing from bank to bank, leaving out the "
                                                     "two banks themselves.")
        times = st.text_input("Float times (s), one per run", placeholder="14, 15, 13", key="fw_times",
                              help="Time for a float (an orange or a stick) to cover the distance. Three runs or more.")
        coefficient = st.slider("Surface-to-average coefficient", 0.8, 0.9, flow.FLOAT_COEFFICIENT, 0.01,
                                key="fw_coeff", help="0.8 for a rough, shallow bed; 0.9 for a smooth, deep one.")
        c1, c2 = st.columns(2)
        lat = c1.number_input("Latitude (optional)", 27.0, 30.0, None, 0.0001, format="%.5f", key="fw_lat")
        lon = c2.number_input("Longitude (optional)", 77.0, 81.0, None, 0.0001, format="%.5f", key="fw_lon")
        note = st.text_input("Note (optional)", placeholder="water colour, smell, weeds, who measured", key="fw_note")
        submitted = st.form_submit_button("Work out the flow", type="primary", icon=":material/calculate:")
    return submitted, (when, site, width, depths, distance, times, coefficient, lat, lon, note)


def tab_field_readings():
    st.caption("Enter a float-method measurement and get the river's flow. A field reading is the only real "
               "measurement of the Nakatiya's flow this project can gather: a handful across the seasons would "
               "test the model. Readings live in this browser session only: download the CSV to keep them.")
    st.markdown("**Float method.** Measure the width, the depths across the river and how long a float takes to "
                "drift a known distance. Area × surface speed × about 0.85 gives the flow.")
    submitted, values = field_reading_form()
    log = st.session_state.setdefault("fw_log", [])
    if submitted:
        when, site, width, depths, distance, times, coefficient, lat, lon, note = values
        try:
            row = flow.field_reading(when, site or "unnamed site", width, flow.parse_numbers(depths), distance,
                                     flow.parse_numbers(times), coefficient, lat, lon, note)
        except ValueError as exc:
            st.error(str(exc))
        else:
            log.append(row)
            st.success(f"Flow {row['discharge_m3s']:.3f} m³/s ({row['discharge_low_m3s']:.3f} to "
                       f"{row['discharge_high_m3s']:.3f} for coefficients 0.8 to 0.9), about "
                       f"{row['discharge_mld']:.1f} million litres a day. Cross-section {row['area_m2']:.2f} m², "
                       f"surface speed {row['surface_velocity_ms']:.2f} m/s.", icon="✅")
    if log:
        df = pd.DataFrame(log, columns=flow.FIELD_COLUMNS)
        st.subheader(f"Readings this session ({len(df)})")
        st.dataframe(df[["date", "site", "width_m", "area_m2", "surface_velocity_ms", "discharge_m3s",
                         "discharge_low_m3s", "discharge_high_m3s", "note"]], hide_index=True)
        with st.container(horizontal=True):
            download_csv(df, "river_field_log", "Field log (CSV)")
            if st.button("Clear the log", icon=":material/delete:", key="fw_clear"):
                st.session_state["fw_log"] = []
                st.rerun()
    up = st.file_uploader("Add readings from a saved field log (CSV)", type="csv", key="fw_upload")
    if up is not None and st.button("Add these readings", key="fw_add_upload", icon=":material/upload:"):
        try:
            rows = flow.read_field_log(up).to_dict("records")
        except (ValueError, KeyError, pd.errors.ParserError) as exc:
            st.error(f"Could not read this file: {exc}")
        else:
            st.session_state["fw_log"] = log + rows
            st.rerun()
    show_method("Cross-section: depths at equal spacing, joined to zero at both banks (trapezoid rule). Surface speed: "
                "float distance over the mean of the timed runs. Flow = area × surface speed × coefficient. The "
                "range shown changes only the coefficient (0.8 and 0.9): it does not cover a bad cross-section, "
                "wind on the float, or a river that changes between runs. Uploaded rows are recomputed from their "
                "raw readings.")


YEARLY_FLOW_MONTHS = {"may": "May", "sep": "September", "jan": "January"}
YEARLY_RAIN_MONTHS = {"jun": "June", "jul": "July", "aug": "August", "sep": "September", "oct": "October"}
WATERSHED_NAMES = {"whole_river": "Nakatiya watershed", "khajuria": "Watershed above Khajuria ghat"}
BAHERI = (79.498, 28.774)  # lon, lat
VEGETATION_NOTE = ("Share of the watershed whose greenness stands out (Landsat NDVI at least 0.10 above the "
                   "watershed's median) both in May and in the November before: trees, groves, orchards and "
                   "sugarcane, without the summer crops. The switch to Landsat 8 in 2013 and haze still move it by "
                   "a few points, so read the trend over many years, not one year against the next. Purple "
                   "diamonds: ESA WorldCover tree cover (its 2020 and 2021 maps use different algorithms). About "
                   "half of the flagged pixels are WorldCover trees; the rest is mostly sugarcane and pixels that "
                   "mix trees with fields. The record starts in 1994: the archive holds no usable May and November "
                   "pair over the watershed before that, nor in 1997, 2002 and 2003.")


@st.cache_data(show_spinner=False)
def yearly_table():
    return flow.load_yearly()


def add_watershed(m, show=True):
    for f in rv.load_watershed()["features"]:
        p = f["properties"]
        whole = p["name"] == "whole_river"
        add_geojson(m, {"type": "FeatureCollection", "features": [f]},
                    f"{WATERSHED_NAMES.get(p['name'], p['name'])} ({p['area_km2']:.0f} km²)",
                    color="#6A1B9A" if whole else "#00897B", weight=3 if whole else 2, fill_opacity=0.03,
                    dash=None if whole else "6 4", show=show)


def watershed_map(key):
    minx, miny, maxx, maxy = fc_bounds(rv.load_watershed())
    m = base_map((minx, miny, maxx, max(maxy, BAHERI[1] + 0.01)))
    add_watershed(m)
    add_river(m)
    lon, lat = flow.SEGMENTS["khajuria"]["outlet"]
    add_marker(m, lat, lon, "Khajuria ghat (Saidpur Khajuria): flow point", color="green", icon="tint")
    add_marker(m, BAHERI[1], BAHERI[0], "Baheri: rain", color="blue", icon="cloud")
    show_map(m, key, 460)


def normal_of(series):
    return series.loc[flow.BASELINE[0]:flow.BASELINE[1]].mean()


def yearly_flow_chart(t, month):
    d = t[[f"{month}_mean_m3s", f"{month}_volume_mcm"]].dropna().reset_index()
    d.columns = ["year", "mean", "volume"]
    normal = normal_of(t[f"{month}_mean_m3s"])
    bars = (alt.Chart(d).mark_bar(color=FLOW_COLORS["khajuria"])
            .encode(x=alt.X("year:O", title=None, axis=alt.Axis(labelOverlap=True, values=list(d["year"][::10]))),
                    y=alt.Y("mean:Q", title=f"Mean flow in {YEARLY_FLOW_MONTHS[month]} (m³/s)"),
                    tooltip=[alt.Tooltip("year:O", title="Year"),
                             alt.Tooltip("mean:Q", title="Mean flow (m³/s)", format=".2f"),
                             alt.Tooltip("volume:Q", title="Water in the month (million m³)", format=".2f")]))
    rule = (alt.Chart(pd.DataFrame({"normal": [normal]})).mark_rule(color="#444444", strokeDash=[6, 4])
            .encode(y="normal:Q", tooltip=[alt.Tooltip("normal:Q", title="1991-2020 mean (m³/s)", format=".2f")]))
    return (bars + rule).properties(height=260)


def yearly_rain_chart(t):
    cols = [f"imd_{m}_mm" for m in YEARLY_RAIN_MONTHS]
    d = t[cols].dropna(how="all").reset_index().melt(id_vars="year", var_name="month", value_name="mm")
    d["month"] = d["month"].str[4:7].map(YEARLY_RAIN_MONTHS)
    names = list(YEARLY_RAIN_MONTHS.values())
    bars = (alt.Chart(d).mark_bar()
            .encode(x=alt.X("year:O", title=None, axis=alt.Axis(labelOverlap=True, values=list(range(1901, 2031, 10)))),
                    y=alt.Y("mm:Q", title="Rain, June-October (mm)", stack="zero"),
                    color=alt.Color("month:N", title=None, sort=names, legend=alt.Legend(orient="bottom"),
                                    scale=alt.Scale(domain=names,
                                                    range=["#90CAF9", "#42A5F5", "#1E88E5", "#1565C0", "#0D47A1"])),
                    order=alt.Order("month_order:Q"),
                    tooltip=[alt.Tooltip("year:O", title="Year"), alt.Tooltip("month:N", title="Month"),
                             alt.Tooltip("mm:Q", title="IMD rain (mm)", format=".0f")])
            .transform_calculate(month_order=f"indexof({names}, datum.month)"))
    era5 = t["era5_jun_oct_mm"].dropna().reset_index()
    line = (alt.Chart(era5).mark_line(color="#E65100", strokeWidth=1.3, point=alt.OverlayMarkDef(size=12))
            .encode(x="year:O", y="era5_jun_oct_mm:Q",
                    tooltip=[alt.Tooltip("year:O", title="Year"),
                             alt.Tooltip("era5_jun_oct_mm:Q", title="ERA5 June-October (mm)", format=".0f")]))
    return (bars + line).properties(height=300)


def yearly_vegetation_chart(t):
    d = t[["veg_permanent_pct", "veg_permanent_km2", "veg_may_looks", "veg_platforms",
           "veg_worldcover_trees_pct"]].dropna(subset=["veg_permanent_pct"])
    d = d.reset_index()
    d["sensor"] = np.where(d["veg_platforms"].str.contains("landsat-8|landsat-9"), "Landsat 8/9 (from 2013)",
                           "Landsat 5/7 (to 2012)")
    pts = (alt.Chart(d).mark_line(point=True, color="#2E7D32")
           .encode(x=alt.X("year:Q", title=None, axis=alt.Axis(format="d"), scale=alt.Scale(zero=False)),
                   y=alt.Y("veg_permanent_pct:Q", title="Permanent vegetation, % of watershed"),
                   tooltip=[alt.Tooltip("year:Q", title="Year", format="d"),
                            alt.Tooltip("veg_permanent_pct:Q", title="% of watershed", format=".1f"),
                            alt.Tooltip("veg_permanent_km2:Q", title="km²", format=".0f"),
                            alt.Tooltip("veg_may_looks:Q", title="Clear looks in May"),
                            alt.Tooltip("sensor:N", title="Sensor")]))
    dots = pts.mark_point(filled=True, size=55).encode(
        shape=alt.Shape("sensor:N", title=None, legend=alt.Legend(orient="bottom")))
    layers = [pts, dots]
    wc = d.dropna(subset=["veg_worldcover_trees_pct"])
    if len(wc):
        layers.append(alt.Chart(wc).mark_point(shape="diamond", size=110, color="#6A1B9A", filled=True)
                      .encode(x="year:Q", y="veg_worldcover_trees_pct:Q",
                              tooltip=[alt.Tooltip("year:Q", title="Year", format="d"),
                                       alt.Tooltip("veg_worldcover_trees_pct:Q", title="ESA WorldCover trees (%)",
                                                   format=".1f")]))
    return alt.layer(*layers).properties(height=280)


def latest_vs_normal(series):
    s = series.dropna()
    if s.empty:
        return None, None, None
    normal = normal_of(series)
    return int(s.index[-1]), s.iloc[-1], 100 * (s.iloc[-1] / normal - 1) if normal else None


def tab_yearly():
    t = yearly_table()
    st.markdown("**The Nakatiya observatory.** The boundary is the river's watershed: the land whose rain drains "
                "to the Nakatiya. Each year: the flow at Khajuria ghat just before the city in May, September and "
                "January; the monsoon rain at Baheri; and the land that stays green through the dry season.")
    watershed_map("fw_watershed_map")
    ws = {f["properties"]["name"]: f["properties"] for f in rv.load_watershed()["features"]}
    st.caption(f"Watershed: {ws['whole_river']['area_km2']:.0f} km² to the Ramganga, "
               f"{ws['khajuria']['area_km2']:.0f} km² above Khajuria ghat (MERIT-Hydro 90 m, Global Watersheds "
               "API). On these flat plains the line is good to a few hundred metres, and roads, canals and drains "
               "move water across it. Baheri lies about 10 km north of the top of the watershed.")

    st.subheader("Flow at Khajuria ghat")
    month = st.segmented_control("Month", list(YEARLY_FLOW_MONTHS), default="may", required=True,
                                 format_func=YEARLY_FLOW_MONTHS.get, key="fw_yearly_month", persist_state="session")
    flows = t.loc[t.index > flow.WARMUP_YEARS[-1]]
    cols = st.columns(3)
    for col, (m, name) in zip(cols, YEARLY_FLOW_MONTHS.items()):
        year, value, pct = latest_vs_normal(flows[f"{m}_mean_m3s"])
        col.metric(f"{name} {year}" if year else name, fmt(value, ".2f", " m³/s"),
                   delta(pct, "+.0f", "% vs 1991-2020") if pct is not None else None, delta_color="off",
                   help=f"Mean modelled flow over {name}; the change is against the 1991-2020 mean for {name}.")
    st.altair_chart(yearly_flow_chart(flows, month), width="stretch")
    st.caption("GEOGLOWS segment 441006241, river km 33-34. Modelled, not measured (see the banner above). Dashed "
               f"line: the 1991-2020 mean. Complete months of {flows.index[0]}-{flows.index[-1]}; the model's first "
               "two years are left out.")

    st.subheader("Monsoon rain at Baheri")
    year, value, pct = latest_vs_normal(t["imd_jun_oct_mm"])
    e_year, e_value, e_pct = latest_vs_normal(t["era5_jun_oct_mm"])
    c = st.columns(2)
    c[0].metric(f"IMD, June-October {year}", fmt(value, ".0f", " mm"),
                delta(pct, "+.0f", "% vs 1991-2020") if pct is not None else None, delta_color="off",
                help="India Meteorological Department gauge-based grid, mean of the 3 × 3 cells around Baheri.")
    c[1].metric(f"ERA5, June-October {e_year}", fmt(e_value, ".0f", " mm"),
                delta(e_pct, "+.0f", "% vs 1991-2020") if e_pct is not None else None, delta_color="off",
                help="ERA5 reanalysis, the 0.25° cell containing Baheri. Available to last week.")
    st.altair_chart(yearly_rain_chart(t), width="stretch")
    imd = t["imd_jun_oct_mm"].dropna()
    st.caption(f"Bars: IMD gridded rainfall (0.25°, from gauges), {imd.index[0]}-{imd.index[-1]}, averaged over "
               "the 3 × 3 grid cells around Baheri (about 80 × 80 km): one cell alone jumps as nearby gauges come "
               "and go. Early decades rest on fewer gauges. Orange line: ERA5 for the Baheri cell from 1940, a "
               "weather model's estimate that runs higher than IMD here.")

    st.subheader("Permanent vegetation in May")
    if "veg_permanent_pct" in t and t["veg_permanent_pct"].notna().any():
        st.altair_chart(yearly_vegetation_chart(t), width="stretch")
        st.caption(VEGETATION_NOTE)
    else:
        st.info("The vegetation record has not been built yet.", icon=":material/hourglass_empty:")

    st.divider()
    with st.container(horizontal=True):
        if flow.YEARLY_BOOK.exists():
            st.download_button("Yearly observatory workbook (Excel)", flow.YEARLY_BOOK.read_bytes(),
                               file_name=flow.YEARLY_BOOK.name,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key="fw_yearly_book", on_click="ignore", icon=":material/table_view:")
        download_csv(t.reset_index(), "nakatiya_yearly", "Yearly table (CSV)")
        download_geojson(rv.load_watershed(), "nakatiya_watershed", "Watershed (GeoJSON)")
    show_method("**Flow**: GEOGLOWS v2 daily flow at Khajuria ghat, averaged over each complete month. **Rain**: "
                "IMD 0.25° gridded daily rainfall (Pai et al. 2014) and ERA5 (Open-Meteo), summed over each "
                "complete month. **Vegetation**: Landsat 30 m medians of the clear looks in May and in the "
                "November before, within the watershed. **Watershed**: MERIT-Hydro flow directions, delineated "
                "from the Ramganga confluence and from Khajuria ghat. Built by research/nakatiya_observatory.")


def tab_flow_checks():
    st.markdown("**Check against the gauged neighbour.** The Ramganga at Chaubari (Bareilly) has a Central Water "
                "Commission gauge. WWF-India and INRM built a hydrological model calibrated to it. Comparing "
                "GEOGLOWS with that model over 1973-2011 shows how far the global model can be trusted in this "
                "river basin.")
    check = flow.chaubari_check(flow_series()["ramganga"])
    show = check[["season", "months", "met_in_pct_of_years", "geoglows_m3s", "swat_present_m3s", "swat_natural_m3s",
                  "ratio_to_present"]].rename(columns={
        "season": "Season", "months": "Months", "met_in_pct_of_years": "Met in % of years",
        "geoglows_m3s": "GEOGLOWS (m³/s)", "swat_present_m3s": "WWF model, today (m³/s)",
        "swat_natural_m3s": "WWF model, no dams or farming (m³/s)", "ratio_to_present": "GEOGLOWS ÷ today"})
    st.dataframe(show, hide_index=True, column_config={
        c: st.column_config.NumberColumn(format="%.2f") for c in show.columns[3:]})
    st.caption("The model runs roughly twice the gauge-calibrated river in the monsoon and up to three times in the "
               "pre-monsoon. That gap is irrigation, canals and dams that the global model ignores, and it differs "
               "by season, so **never scale the Nakatiya's flows by one factor**.")
    st.subheader("What this page cannot tell you")
    st.markdown(bullets([
        "No gauge has ever measured the Nakatiya. Every flow here is modelled; only a field reading is a measurement.",
        "The model has no city, no sewage, no irrigation, no canals and no aquifer: it cannot show the effect of "
        "riparian construction on seepage and runoff, the question this project wants to answer.",
        "The Nakatiya's model stream starts about 25-30 km below the mapped head, and a 371 km² catchment is small "
        "for a 0.1° weather grid. A finer elevation model (MERIT-Hydro, 90 m) draws the watershed at 444 km².",
        "GEOGLOWS flows are not bias-corrected. The 30-year climate normal is 1991-2020; the first two model "
        "years are left out.",
        "Open-water width from Sentinel-2 is a hint about the channel, not a flow.",
        "A machine-learning forecast trained on these flows would copy the model, not the river. It needs measured "
        "flows first."]))
    st.info("Nothing on this page is a legal, engineering or flood-warning determination. Where a number matters, "
            "measure it.", icon=":material/info:")


def page_river_flow(ctx):
    st.header(PAGES["river-flow"], divider="blue")
    st.caption("How much water the Nakatiya carries, through the year and over the decades: modelled flows, a "
               "yearly record of flow, rain and vegetation in the watershed, what the satellites see of the "
               "channel, and a form for your own field readings.")
    st.warning(FLOW_BANNER, icon=":material/water_drop:")
    tabs = st.tabs(["Modelled flow", "Yearly record", "Satellite width", "Field readings", "Check and limits"],
                   key="fw_tab", on_change="rerun")
    for tab, render in zip(tabs, (tab_modelled_flow, tab_yearly, tab_widths, tab_field_readings, tab_flow_checks)):
        if tab.open:
            with tab:
                render()


PAGE_FUNCS = {"overview": page_overview, "change-radar": page_radar, "field-scanner": page_field,
              "water": page_water, "fusion": page_fusion, "scouting": page_scouting, "ask": page_ask,
              "river": page_observatory, "land-change": page_land_change, "river-flow": page_river_flow}


# ------------------------------------------------------------------------------------------ sidebar

def _apply_preset():
    st.session_state["aoi_lon"], st.session_state["aoi_lat"] = AOI_PRESETS[st.session_state["aoi_preset"]]


st.session_state.setdefault("aoi_lon", DEFAULT_AOI[0])
st.session_state.setdefault("aoi_lat", DEFAULT_AOI[1])

with st.sidebar:
    st.title("🌾 KhetOS")
    st.caption("River + agriculture intelligence for Bareilly and Rohilkhand, from free satellite data")
    page = st.radio("Module", list(PAGES), format_func=PAGES.get, key="page", bind="query-params")
    with st.expander("Area of interest", expanded=True, icon=":material/crop_free:"):
        st.selectbox("Jump to", list(AOI_PRESETS), key="aoi_preset", on_change=_apply_preset)
        c1, c2 = st.columns(2)
        lon = c1.number_input("Longitude", 77.0, 81.0, step=0.001, format="%.5f", key="aoi_lon")
        lat = c2.number_input("Latitude", 27.0, 30.0, step=0.001, format="%.5f", key="aoi_lat")
        half_km = st.slider("Half-width (km)", 0.5, 5.0, 1.5, 0.5, key="aoi_km")
        months = st.slider("History window (months)", 2, 12, 6, key="months")
        cloud = st.slider("Cloud tolerance over the area (%)", 5, 60, 35, 5, key="cloud")
        if not in_region(lon, lat):
            st.warning("This point is outside Rohilkhand: the analyses still run, but the defaults and the river "
                       "layers are tuned for Bareilly.")
    st.caption(DISCLAIMER)
    with st.expander("Data sources and licences", icon=":material/copyright:"):
        st.markdown(bullets(f"**{k}**: {v}" for k, v in ATTRIBUTIONS.items()))

refreshed = st.session_state.get("river_fc")
RIVER_FC = refreshed or rv.load_nakatiya()
RIVER_GEOM = rv.nakatiya_geometry(RIVER_FC)
ctx = {"bbox": aoi_bbox(lon, lat, half_km), "months": months, "cloud": cloud, "fc": RIVER_FC,
       "river": RIVER_GEOM if refreshed else None}  # None: the bundled geometry, which keeps cache keys small
PAGE_FUNCS[page](ctx)
