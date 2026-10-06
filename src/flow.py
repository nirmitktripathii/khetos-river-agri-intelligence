"""Modelled river flow for the Nakatiya and the Ramganga (GEOGLOWS v2), and the arithmetic of field gauging.

GEOGLOWS v2 routes ECMWF ERA5 reanalysis runoff down the TDX-Hydro river network and gives a daily flow for every
river segment from 1940 (CC BY 4.0). It is a climate-only model: it has no city, sewage, irrigation, canals, dams or
aquifer, so it shows what rain and evaporation alone would put in the river. Nobody gauges the Nakatiya, so nothing
here is a measured flow; `float_discharge` turns a tape, a float and a stopwatch into the first measurements.
"""
import io
import json
import math
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

from src.config import DATA_DIR

API = "https://geoglows.ecmwf.int/api/v2/"
DAILY_FILE = DATA_DIR / "geoglows_daily.csv.gz"
DAILY_META_FILE = DATA_DIR / "geoglows_daily.json"
WIDTH_FILE = DATA_DIR / "nakatiya_open_water_width.csv"
RAIN_HINDCAST_FILE = DATA_DIR / "nakatiya_rain_forecast_hindcast.csv"
RAIN_SCORES_FILE = DATA_DIR / "nakatiya_rain_forecast_scores.csv"
RAIN_CALIBRATION_FILE = DATA_DIR / "nakatiya_rain_forecast_calibration.csv"
YEARLY_FILE = DATA_DIR / "nakatiya_yearly_observations.csv"  # research/nakatiya_observatory/build_table.py
YEARLY_BOOK = DATA_DIR / "nakatiya_yearly_observations.xlsx"

# GEOGLOWS river segments (TDX-Hydro LINKNO), upstream to downstream. A flow is the flow leaving the segment, so
# `area_km2` is the model's contributing area at the segment's downstream end (v2 model table, DSContArea). `km` is
# the stretch of the OpenStreetMap main stem, measured from the mapped head, that the public API snaps to the
# segment, and `outlet` the (lon, lat) of the last mapped point on it (the gauge site for the Ramganga).
SEGMENTS = {
    "above": {"id": 440988347, "name": "Nakatiya above the city", "km": "10-21", "area_km2": 118.0,
              "outlet": (79.4736, 28.4023)},
    "entering": {"id": 441010366, "name": "Nakatiya entering the city", "km": "22-26", "area_km2": 138.4,
                 "outlet": (79.4639, 28.3821)},
    # Khajuria ghat (Saidpur Khajuria, 0.2 km from the river). The model table would not download, so its area is
    # estimated: the area at which the flow per km² between "entering" and "below" gives this segment's mean flow.
    # (MERIT-Hydro puts 235 km² above this point; it draws the whole catchment 20 % larger than TDX-Hydro does.)
    "khajuria": {"id": 441006241, "name": "Nakatiya at Khajuria ghat", "km": "33-34", "area_km2": 188.0,
                 "outlet": (79.4711, 28.3380), "area_estimated": True},
    "below": {"id": 441068162, "name": "Nakatiya below the city", "km": "45-52", "area_km2": 227.1,
              "outlet": (79.4363, 28.2444)},
    "mouth": {"id": 441161738, "name": "Nakatiya at the Ramganga (whole river)", "km": "72.6", "area_km2": 371.5,
              "outlet": (79.4846, 28.1379)},
    "ramganga": {"id": 441266307, "name": "Ramganga at Chaubari (Bareilly gauge site)", "km": None,
                 "area_km2": 19961.7, "outlet": (79.368, 28.298)},
}
NAKATIYA_KEYS = ("above", "entering", "khajuria", "below", "mouth")

# India Meteorological Department seasons, and the June-May water year used for Indian rivers.
SEASONS = {"winter": (1, 2), "pre-monsoon": (3, 4, 5), "monsoon": (6, 7, 8, 9), "post-monsoon": (10, 11, 12)}
SEASON_MONTHS = {"winter": "Jan-Feb", "pre-monsoon": "Mar-May", "monsoon": "Jun-Sep", "post-monsoon": "Oct-Dec"}
WATER_YEAR_START = 6
# The first two years of the model run are kept out of every statistic as a warm-up precaution: GEOGLOWS does not
# say how the 1940 run was started. Nothing in the data marks them as wrong: both were drought years in ERA5.
WARMUP_YEARS = (1940, 1941)
BASELINE = (1991, 2020)  # current 30-year climate normal period
TREND_START = 1985  # the first year of the app's satellite record
SECONDS_PER_DAY = 86400
MLD_PER_M3S = 86.4  # million litres per day in one cubic metre per second
FLOAT_COEFFICIENT = 0.85  # mean velocity / surface velocity; 0.8 rough and shallow, 0.9 smooth and deep

# Seasonal mean flow of the Ramganga at the Chaubari gauge site that is met in 25, 50, 75 and 90 % of the years
# 1973-2011 (m³/s): WWF-India / INRM, "Hydrological Modelling of the Ramganga River Basin", Appendix 1. "present
# day" is a SWAT model calibrated to the Central Water Commission gauge with today's dams, canals and irrigation;
# "natural" is the same model without them and with rain-fed farming.
CHAUBARI_PERIOD = (1973, 2011)
CHAUBARI_SWAT = {
    "present day": {"monsoon": {25: 513.87, 50: 412.30, 75: 318.46, 90: 242.06},
                    "pre-monsoon": {25: 46.54, 50: 31.50, 75: 15.40, 90: 10.99}},
    "natural": {"monsoon": {25: 1436.93, 50: 1297.53, 75: 1082.43, 90: 853.00},
                "pre-monsoon": {25: 424.90, 50: 372.37, 75: 341.90, 90: 331.93}},
}
CHAUBARI_GAUGE_AREA_KM2 = 18456  # Central Water Commission, site CW1RAM000145

FIELD_COLUMNS = ["date", "site", "lat", "lon", "width_m", "depths_m", "distance_m", "times_s", "coefficient",
                 "area_m2", "mean_depth_m", "surface_velocity_ms", "discharge_m3s", "discharge_low_m3s",
                 "discharge_high_m3s", "discharge_mld", "note"]


# ------------------------------------------------------------------------------------------- data

@lru_cache(maxsize=1)
def _daily():
    d = pd.read_csv(DAILY_FILE, index_col=0, parse_dates=True)
    ids = {str(s["id"]): key for key, s in SEGMENTS.items()}
    return d.rename(columns=ids)[list(SEGMENTS)].astype("float64")


def load_daily():
    """Bundled daily modelled flow (m³/s) from 1940: one column per key of SEGMENTS, indexed by date."""
    return _daily().copy()


@lru_cache(maxsize=1)
def snapshot_info():
    """{"retrieved", "first", "last", "source", "licence"} of the bundled daily flows."""
    with open(DAILY_META_FILE, encoding="utf-8") as f:
        return json.load(f)


def _get(path, timeout):
    r = requests.get(API + path, params={"format": "csv"}, timeout=timeout)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


def fetch_daily(river_id, timeout=90):
    """Daily modelled flow (m³/s) of one segment, 1940 to about a week ago, from the GEOGLOWS API."""
    df = _get(f"retrospectivedaily/{int(river_id)}", timeout)
    s = pd.Series(df.iloc[:, 1].to_numpy("float64"),
                  index=pd.to_datetime(df.iloc[:, 0], utc=True).dt.tz_localize(None).dt.normalize(), name=river_id)
    s.index.name = "date"
    return s[~s.index.duplicated()].sort_index()


def fetch_forecast(river_id, timeout=60):
    """The latest 15-day modelled forecast of one segment: 3-hourly `median`, `low` and `high` (m³/s) over the
    51 ensemble members, indexed by India Standard Time."""
    df = _get(f"forecast/{int(river_id)}", timeout)
    out = df.rename(columns={"flow_median": "median", "flow_uncertainty_lower": "low",
                             "flow_uncertainty_upper": "high"})
    out.index = (pd.to_datetime(out.pop("datetime"), utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    out.index.name = "time"
    return out[["median", "low", "high"]].dropna(how="all")


@lru_cache(maxsize=1)
def _widths():
    return pd.read_csv(WIDTH_FILE, parse_dates=["date"])


@lru_cache(maxsize=1)
def _rain_hindcast():
    return pd.read_csv(RAIN_HINDCAST_FILE, parse_dates=["target_date", "origin_date"])


def load_rain_hindcast():
    """How a rain-forecast model would have done at the Nakatiya mouth, 2024-03 onward: for each forecast origin
    and horizon (1, 3, 7 days), the GEOGLOWS flow on the target day, the model's median and 10-90 % band when given
    the archived ECMWF rain forecast, the band after conformal calibration (`*_cal_m3s`), and the same model family
    without any rain forecast (research/flow_forecast)."""
    return _rain_hindcast().copy()


def load_rain_scores():
    """Skill scores behind `load_rain_hindcast`, one row per horizon and model."""
    return pd.read_csv(RAIN_SCORES_FILE)


def load_rain_calibration():
    """Per horizon: how often the flow fell inside the 10-90 % band on test days from 2025, before and after the
    band was widened by conformal calibration on 2024 (separately for June-September and the rest of the year)."""
    return pd.read_csv(RAIN_CALIBRATION_FILE)


def load_yearly():
    """One row per year (indexed by year) for the Nakatiya observatory: modelled flow at Khajuria ghat in January,
    May and September; June-October rain at Baheri (IMD gauge grid from 1901, ERA5 from 1940); and permanent
    vegetation in the watershed in May (Landsat, from 1985). Blank where a source does not reach."""
    return pd.read_csv(YEARLY_FILE, index_col="year")


def load_widths():
    """Open-water width of three Nakatiya reaches on clear Sentinel-2 dates, 2018-2025, from sub-pixel unmixing
    (research/river_flow). `reliable` is False where the water signature was borrowed or contaminated."""
    return _widths().copy()


# ---------------------------------------------------------------------------------------- volumes

def volume_mcm(flow):
    """Water volume carried by a daily-mean flow series (m³/s), in million cubic metres."""
    return float(np.nansum(flow)) * SECONDS_PER_DAY / 1e6


def runoff_mm(volume, area_km2):
    """Depth of water over the catchment (mm) that a volume in million m³ amounts to."""
    return 1000 * volume / area_km2


def water_year(index):
    """Start year of the June-May water year of each date: May 2025 belongs to 2024 (written 2024-25)."""
    return index.year - (index.month < WATER_YEAR_START)


def water_year_label(year):
    return f"{int(year)}-{(int(year) + 1) % 100:02d}"


def _expected_days(year, wy):
    a = pd.Timestamp(int(year), WATER_YEAR_START if wy else 1, 1)
    return (a + pd.DateOffset(years=1) - a).days


def annual_table(flow, wy=False):
    """One row per calendar year (or June-May water year, `wy`): days of data, mean flow, volume, the highest
    daily flow and the lowest 7-day mean flow. `complete` is False for a year with missing days."""
    flow = flow.dropna()
    key = water_year(flow.index) if wy else flow.index.year
    g = flow.groupby(key)
    out = pd.DataFrame({"days": g.size(), "mean_m3s": g.mean(), "volume_mcm": g.sum() * SECONDS_PER_DAY / 1e6,
                        "peak_day_m3s": g.max(), "low_7day_m3s": flow.rolling(7).mean().groupby(key).min()})
    out.index.name = "year"
    out["complete"] = out["days"] == [_expected_days(y, wy) for y in out.index]
    return out


def seasonal_table(flow):
    """Mean flow and volume of each season of each calendar year: columns (`mean_m3s` | `volume_mcm` | `days`,
    season). A season with missing days keeps its mean but shows in `days`."""
    flow = flow.dropna()
    season = pd.Series(flow.index.month.map({m: s for s, months in SEASONS.items() for m in months}),
                       index=flow.index)
    g = flow.groupby([flow.index.year, season])
    out = pd.DataFrame({"mean_m3s": g.mean(), "volume_mcm": g.sum() * SECONDS_PER_DAY / 1e6, "days": g.size()})
    out = out.unstack().reindex(columns=list(SEASONS), level=1)
    out.index.name = "year"
    return out


def season_complete(table):
    """Boolean frame (year x season): True where the season has every one of its days."""
    days = table["days"]
    full = pd.DataFrame({s: [sum(pd.Period(f"{y}-{m:02d}").days_in_month for m in months) for y in days.index]
                         for s, months in SEASONS.items()}, index=days.index)
    return days.eq(full)


def monthly_table(flow):
    """Mean flow and volume of every month: index = first day of the month."""
    flow = flow.dropna()
    g = flow.groupby(flow.index.to_period("M"))
    out = pd.DataFrame({"mean_m3s": g.mean(), "volume_mcm": g.sum() * SECONDS_PER_DAY / 1e6, "days": g.size()})
    out["complete"] = out["days"] == out.index.days_in_month
    out.index = out.index.to_timestamp()
    out.index.name = "month"
    return out


def climatology(flow, start=BASELINE[0], end=BASELINE[1]):
    """The average year over `start`-`end`: for each calendar month the mean, median, 10th and 90th percentile of
    that month's mean flow across the years, its mean volume and its share of the yearly volume."""
    m = monthly_table(flow.loc[str(start):str(end)])
    m = m[m["complete"]]
    g = m.groupby(m.index.month)
    out = pd.DataFrame({"mean_m3s": g["mean_m3s"].mean(), "median_m3s": g["mean_m3s"].median(),
                        "p10_m3s": g["mean_m3s"].quantile(0.1), "p90_m3s": g["mean_m3s"].quantile(0.9),
                        "volume_mcm": g["volume_mcm"].mean()})
    out["share_pct"] = 100 * out["volume_mcm"] / out["volume_mcm"].sum()
    out.index.name = "month"
    return out


def flow_duration(flow, probs=(1, 5, 10, 25, 50, 75, 90, 95, 99)):
    """Flow equalled or exceeded on `p` percent of days (m³/s), for each p: the flow-duration curve."""
    v = flow.dropna().to_numpy()
    return {p: float(np.percentile(v, 100 - p)) for p in probs}


def dependable(values, probs=(50, 75, 90)):
    """Value equalled or exceeded in `p` percent of years, by the Weibull plotting position m / (n + 1): the
    "75 % dependable" yield of Indian water planning. NaN where the record is too short to reach `p`."""
    v = np.asarray(values, "float64")
    v = np.sort(v[np.isfinite(v)])[::-1]
    n = len(v)
    if not n:
        return {p: float("nan") for p in probs}
    exceed = 100 * np.arange(1, n + 1) / (n + 1)
    return {p: float(np.interp(p, exceed, v)) if exceed[0] <= p <= exceed[-1] else float("nan") for p in probs}


# ----------------------------------------------------------------------------------------- trends

def trend(series, min_years=10):
    """Monotonic trend of a yearly series: Theil-Sen slope, Mann-Kendall two-sided p-value (tie-corrected), and
    the same p-value after allowing for year-to-year persistence.

    Persistence (a wet year tending to follow a wet year) makes a plain Mann-Kendall test find trends too
    easily, so `p_persist` inflates the variance for the lag-1 autocorrelation of the detrended series when that
    is positive and significant (Yue and Wang, 2004). Returns NaN statistics under `min_years` values.
    """
    s = pd.Series(series).dropna()
    x, y = np.asarray(s.index, "float64"), s.to_numpy("float64")
    n = len(y)
    out = {"n": n, "mean": float(y.mean()) if n else float("nan"), "slope_per_year": float("nan"),
           "pct_per_decade": float("nan"), "tau": float("nan"), "p": float("nan"), "lag1": float("nan"),
           "p_persist": float("nan"), "intercept": float("nan")}
    if n < min_years:
        return out
    i, j = np.triu_indices(n, 1)
    slope = float(np.median((y[j] - y[i]) / (x[j] - x[i])))
    intercept = float(np.median(y - slope * x))
    sgn = np.sign(y[j] - y[i])
    s_stat = float(sgn.sum())
    ties = np.unique(y, return_counts=True)[1]
    var = (n * (n - 1) * (2 * n + 5) - float((ties * (ties - 1) * (2 * ties + 5)).sum())) / 18
    resid = y - slope * x
    resid = resid - resid.mean()
    denom = float((resid ** 2).sum())
    lag1 = float((resid[:-1] * resid[1:]).sum() / denom) if denom else 0.0
    inflate = 1.0
    if lag1 > 1.96 / math.sqrt(n):
        k = np.arange(1, n)
        inflate = max(1.0, 1 + 2 * float(((1 - k / n) * lag1 ** k).sum()))

    def p_value(v):
        if v <= 0 or s_stat == 0:
            return 1.0
        return math.erfc(abs(s_stat - np.sign(s_stat)) / math.sqrt(v) / math.sqrt(2))

    n_pairs = n * (n - 1) / 2
    ties_pairs = float((ties * (ties - 1) / 2).sum())
    out.update({"slope_per_year": slope, "intercept": intercept,
                "pct_per_decade": 1000 * slope / out["mean"] if out["mean"] else float("nan"),
                "tau": s_stat / math.sqrt(n_pairs * (n_pairs - ties_pairs)) if n_pairs > ties_pairs else 0.0,
                "p": p_value(var), "lag1": lag1, "p_persist": p_value(var * inflate)})
    return out


def trend_reading(t):
    """Plain wording for one `trend` result, read from the persistence-adjusted p-value."""
    if not math.isfinite(t["p_persist"]):
        return "too few years"
    word = "rise" if t["slope_per_year"] > 0 else "fall"
    for limit, text in ((0.01, f"clear {word}"), (0.05, f"likely {word}"), (0.1, f"weak sign of a {word}")):
        if t["p_persist"] < limit:
            return text
    return "no trend detected"


def yearly_series(flow):
    """The yearly series a trend is read from, over complete calendar years: mean flow of the year and of each
    season, the lowest 7-day flow and the highest daily flow (all m³/s)."""
    ann = annual_table(flow)
    ann = ann[ann["complete"]]
    sea = seasonal_table(flow)
    ok = season_complete(sea)
    out = {"year": ann["mean_m3s"]}
    out.update({f"{s} ({SEASON_MONTHS[s]})": sea["mean_m3s"][s][ok[s]] for s in SEASONS})
    out.update({"lowest 7-day flow": ann["low_7day_m3s"], "highest daily flow": ann["peak_day_m3s"]})
    return out


def trend_table(flow, periods=((TREND_START, None), (1950, None))):
    """Trend of every yearly series over each (start, end) period; `end` None means the last complete year."""
    rows = []
    for name, s in yearly_series(flow).items():
        for a, b in periods:
            x = s.loc[a:b]
            if len(x):
                rows.append({"series": name, "period": f"{x.index[0]}-{x.index[-1]}", **trend(x)})
    return pd.DataFrame(rows).drop(columns=["intercept"])


def chaubari_check(ramganga):
    """The model against the WWF-India one where the Ramganga is gauged: for the monsoon and the pre-monsoon, the
    seasonal mean flow (m³/s) met in 25, 50, 75 and 90 % of the years 1973-2011, from a daily `ramganga` series
    and from both SWAT runs, with the ratios of this model to the present-day river and to the natural one."""
    sea = seasonal_table(ramganga)
    ok = season_complete(sea)
    rows = []
    for season in ("monsoon", "pre-monsoon"):
        v = sea["mean_m3s"][season][ok[season]].loc[CHAUBARI_PERIOD[0]:CHAUBARI_PERIOD[1]]
        dep = dependable(v, probs=(25, 50, 75, 90))
        for p, value in dep.items():
            present, natural = (CHAUBARI_SWAT[run][season][p] for run in ("present day", "natural"))
            rows.append({"season": season, "months": SEASON_MONTHS[season], "met_in_pct_of_years": p,
                         "geoglows_m3s": value, "swat_present_m3s": present, "swat_natural_m3s": natural,
                         "ratio_to_present": value / present, "ratio_to_natural": value / natural})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------- land-cover scenario

# Soil Conservation Service curve-number (CN) method, applied day by day as in Dr. S. S. Tripathi's thesis on
# Bareilly district: the rain of the five days before sets the day's antecedent moisture condition (AMC).
AMC_LIMITS_MM = (35.6, 53.3)  # growing-season limits (1.4 and 2.1 inches) between AMC I, II and III


def curve_numbers(cn2):
    """(CN-I, CN-II, CN-III): the curve number for dry, average and wet soil, from the average one."""
    return 4.2 * cn2 / (10 - 0.058 * cn2), float(cn2), 23 * cn2 / (10 + 0.13 * cn2)


def scs_runoff(rain_mm, cn):
    """Direct storm runoff (mm) from a day's rain (mm): Q = (P - 0.2 S)² / (P + 0.8 S) with the retention
    S = 25400 / CN - 254, and no runoff until the rain exceeds 0.2 S."""
    p = np.asarray(rain_mm, "float64")
    s = 25400 / np.asarray(cn, "float64") - 254
    excess = np.clip(p - 0.2 * s, 0, None)
    return excess ** 2 / np.where(excess > 0, p + 0.8 * s, 1.0)


def scs_daily(rain, cn2):
    """Daily direct runoff (mm) of a daily rain series (mm) for an average-condition curve number `cn2`."""
    rain = rain.fillna(0.0)
    before = rain.rolling(5).sum().shift(1).fillna(0.0)
    cn1, _, cn3 = curve_numbers(cn2)
    cn = np.select([before < AMC_LIMITS_MM[0], before > AMC_LIMITS_MM[1]], [cn1, cn3], cn2)
    return pd.Series(scs_runoff(rain.to_numpy(), cn), index=rain.index)


# ---------------------------------------------------------------------------------- field gauging

def section_area(width_m, depths_m):
    """Wetted cross-section (m²) from depths taken at equal spacing across the water, banks not included: with n
    readings the spacing is width / (n + 1), and joining the readings to zero depth at both banks (trapezoid
    rule) gives area = spacing x sum of depths."""
    depths = [float(d) for d in depths_m]
    if width_m <= 0 or not depths or min(depths) < 0:
        raise ValueError("Enter a width above zero and at least one depth, none of them negative.")
    return float(width_m) / (len(depths) + 1) * sum(depths)


def float_discharge(width_m, depths_m, distance_m, times_s, coefficient=FLOAT_COEFFICIENT):
    """Discharge by the float method: cross-section x surface velocity x a coefficient.

    A float travels at the surface, where water runs faster than the average of the whole section; the
    coefficient (about 0.85; 0.8 for a rough shallow bed, 0.9 for a smooth deep one) brings it down to the mean.
    Surface velocity is the distance over the mean of the timed runs. `discharge_low_m3s` and
    `discharge_high_m3s` use coefficients 0.8 and 0.9: they show that one source of error only.
    """
    times = [float(t) for t in times_s]
    if distance_m <= 0 or not times or min(times) <= 0:
        raise ValueError("Enter a float distance above zero and at least one time above zero.")
    if not 0.5 <= coefficient <= 1:
        raise ValueError("The surface coefficient is normally 0.8 to 0.9.")
    area = section_area(width_m, depths_m)
    surface = float(distance_m) / (sum(times) / len(times))
    q = area * surface * coefficient
    spread = (max(times) - min(times)) / (sum(times) / len(times)) if len(times) > 1 else float("nan")
    return {"area_m2": area, "mean_depth_m": area / float(width_m), "surface_velocity_ms": surface,
            "mean_velocity_ms": surface * coefficient, "discharge_m3s": q, "discharge_low_m3s": area * surface * 0.8,
            "discharge_high_m3s": area * surface * 0.9, "discharge_mld": q * MLD_PER_M3S,
            "discharge_lps": q * 1000, "time_spread": spread, "runs": len(times), "depth_readings": len(depths_m)}


def parse_numbers(text):
    """Numbers typed as "0.4, 0.7 0.5" (commas, semicolons or spaces) as a list of floats."""
    parts = [p for p in str(text or "").replace(";", ",").replace(",", " ").split() if p]
    try:
        return [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"Could not read these as numbers: {text!r}. Separate them with commas.") from None


def field_reading(when, site, width_m, depths_m, distance_m, times_s, coefficient=FLOAT_COEFFICIENT, lat=None,
                  lon=None, note=""):
    """One row of the field log (FIELD_COLUMNS) from a float-method measurement."""
    q = float_discharge(width_m, depths_m, distance_m, times_s, coefficient)
    depths, times = (" ".join(f"{float(v):g}" for v in values) for values in (depths_m, times_s))
    return {"date": pd.Timestamp(when).date().isoformat(), "site": str(site).strip(), "lat": lat, "lon": lon,
            "width_m": float(width_m), "depths_m": depths, "distance_m": float(distance_m),
            "times_s": times, "coefficient": float(coefficient), "area_m2": round(q["area_m2"], 3),
            "mean_depth_m": round(q["mean_depth_m"], 3), "surface_velocity_ms": round(q["surface_velocity_ms"], 3),
            "discharge_m3s": round(q["discharge_m3s"], 4), "discharge_low_m3s": round(q["discharge_low_m3s"], 4),
            "discharge_high_m3s": round(q["discharge_high_m3s"], 4), "discharge_mld": round(q["discharge_mld"], 2),
            "note": str(note).strip()}


def read_field_log(source):
    """A field log saved from the app, checked: the discharge of every row is recomputed from its raw readings
    so that an edited file cannot carry numbers that do not follow from them."""
    raw = pd.read_csv(source)
    missing = [c for c in ("date", "site", "width_m", "depths_m", "distance_m", "times_s") if c not in raw]
    if missing:
        raise ValueError(f"This file is not a KhetOS field log: it has no column {', '.join(missing)}.")
    rows = []
    for r in raw.to_dict("records"):
        coeff = r.get("coefficient")
        rows.append(field_reading(r["date"], r["site"], float(r["width_m"]), parse_numbers(r["depths_m"]),
                                  float(r["distance_m"]), parse_numbers(r["times_s"]),
                                  float(coeff) if pd.notna(coeff) else FLOAT_COEFFICIENT,
                                  r.get("lat") if pd.notna(r.get("lat")) else None,
                                  r.get("lon") if pd.notna(r.get("lon")) else None,
                                  r.get("note") if pd.notna(r.get("note")) else ""))
    return pd.DataFrame(rows, columns=FIELD_COLUMNS)
