"""Ask the Map: turn a plain-language question into one of the app's analyses and its parameters.

The parser is rule-based and runs offline, so there is no API key and no invented answer: every number the
app then shows is computed from the data. Unrecognised questions get help text instead of a guess.
"""
import re
from datetime import date

from src.eo import LANDSAT_FIRST_YEAR, latest_rabi_year
from src.river import default_timeline_years

DEFAULT_NEAR_M = 500
MAX_CORRIDOR_M = 2000

_DISTANCE = re.compile(r"(\d+(?:\.\d+)?)\s*(km|kilomet(?:er|re)s?|m|met(?:er|re)s?)\b")
_YEAR = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
# Keys match src.config.NAKATIYA_REACHES.
REACH_WORDS = {
    "Urban reach · Dohra Rd–Bisalpur Rd": ("urban", "city", "town", "dohra", "bisalpur"),
    "Upper reach · Bhojipura side": ("upper reach", "bhojipura", "upstream"),
    "Lower reach · Ramganga confluence": ("lower reach", "downstream", "near the confluence"),
}
EXAMPLES = [
    "Which fields within 500 m of the Nakatiya show unusual change?",
    "Any new construction or clearing within 100 m of the Nakatiya in the city?",
    "Show built-up change along the Nakatiya since 2016 within 100 m",
    "Is the Nakatiya flooding? Check water expansion within 500 m",
    "Trace the Nakatiya's course from origin to the Ramganga",
    "How has the land within 100 m of the Nakatiya changed over the years?",
    "Where is crop water stress in my area?",
    "Build the scouting queue for this area",
]


def _has(q, *words):
    return any(w in q for w in words)


def parse_distances_m(q):
    """Every distance in the text, in metres ("500 m", "0.5 km", "100m")."""
    return [float(v) * (1000 if unit.startswith("k") else 1) for v, unit in _DISTANCE.findall(q)]


def parse_years(q, today=None):
    this_year = (today or date.today()).year
    return sorted({int(y) for y in _YEAR.findall(q) if 1984 <= int(y) <= this_year})


def parse_reach(q):
    for name, words in REACH_WORDS.items():
        if _has(q, *words):
            return name
    return None


def _year_pair(years, default_start, today=None):
    """(start, end) for a comparison, clamped to the Landsat archive and ending by default with the latest
    complete rabi season; the note says what was adjusted."""
    latest = latest_rabi_year(today)
    if len(years) >= 2:
        a, b = years[0], years[-1]
    elif len(years) == 1 and years[0] < latest:
        a, b = years[0], latest
    else:
        a, b = default_start, latest
    notes = []
    if b > latest:
        notes.append(f"{b} moved to {latest}, the latest complete rabi season")
        b = latest
    if a < LANDSAT_FIRST_YEAR:
        notes.append(f"{a} moved to {LANDSAT_FIRST_YEAR}: the Landsat archive here starts then")
        a = LANDSAT_FIRST_YEAR
    if a >= b:
        notes.append(f"start moved to {b - 1}")
        a = b - 1
    return a, b, f" ({'; '.join(notes)})" if notes else ""


def _corridor(distances, default):
    d = distances[0] if distances else default
    return int(min(max(d, 10), MAX_CORRIDOR_M))


# ------------------------------------------------------------------------------------ field brief

def _finite(x):
    return isinstance(x, (int, float)) and x == x and abs(x) != float("inf")


def _canopy(ndvi):
    if ndvi < 0.2:
        return "bare soil, a harvested or fallow field, or a crop just sown"
    if ndvi < 0.4:
        return "a sparse or early canopy, or a crop that is senescing or stressed"
    if ndvi < 0.6:
        return "a moderate green canopy"
    return "a dense green canopy"


def field_brief(scan):
    """Evidence brief for a field scan: {"findings", "checks", "limits"}, each a list of sentences built only
    from the measured values in `scan` (keys: latest, previous, weather, stress, crop)."""
    latest, prev = scan["latest"], scan.get("previous")
    weather, stress = scan.get("weather") or {}, scan.get("stress")
    crop = scan.get("crop") or "Unknown"
    findings, checks = [], []
    ndvi, ndmi = latest.get("ndvi"), latest.get("ndmi")
    month = int(str(latest["date"])[5:7])
    if _finite(ndvi):
        findings.append(f"On {latest['date']} the median NDVI was {ndvi:.2f} over {latest['pixels']:,} clear "
                        f"{'cropland ' if latest.get('pixels_used') == 'clear cropland pixels' else ''}pixels: "
                        f"{_canopy(ndvi)}.")
    if _finite(ndmi):
        state = "low" if ndmi < 0 else "moderate" if ndmi < 0.2 else "high"
        findings.append(f"NDMI (canopy water) was {ndmi:.2f}, {state} for a green crop.")
    if prev and _finite(prev.get("ndvi")) and _finite(ndvi):
        d = ndvi - prev["ndvi"]
        days = (date.fromisoformat(str(latest["date"])) - date.fromisoformat(str(prev["date"]))).days
        trend = "fell" if d <= -0.05 else "rose" if d >= 0.05 else "held steady"
        findings.append(f"NDVI {trend} by {d:+.2f} since the previous clear scene on {prev['date']} ({days} days "
                        "earlier).")
        if d <= -0.05:
            if month in (3, 4, 10, 11):
                checks.append("A drop in March-April or October-November is often harvest: confirm on the ground "
                              "before reading it as stress.")
            else:
                checks.append("Walk the parts of the field that look paler on the NDVI map: check for pests, disease, "
                              "lodging, waterlogging or missed irrigation.")
    if weather:
        rain, et0 = weather.get("rain_14d_mm"), weather.get("et0_14d_mm")
        if _finite(rain) and _finite(et0):
            findings.append(f"Over {weather.get('period', 'the last 14 days')} the model grid cell had {rain:.0f} mm "
                            f"of rain against {et0:.0f} mm of reference evapotranspiration "
                            f"({'a deficit' if et0 > rain else 'a surplus'} of {abs(et0 - rain):.0f} mm).")
        sm = weather.get("soil_moisture_9_to_27cm")
        if _finite(sm):
            findings.append(f"Modelled root-zone soil moisture (9-27 cm) is {sm:.2f} m³/m³.")
    if stress:
        s = stress["water_stress_signal"]
        findings.append(f"The water-stress signal is {s:.0f}/100 (canopy dryness and the 14-day water balance).")
        if s >= 60:
            checks.append("Check the irrigation schedule and soil moisture by hand: the canopy is dry and the recent "
                          "weather has not made up for it.")
    if crop != "Unknown":
        findings.append(f"Crop reported by the user: {crop} (not verified from the imagery).")
    if not checks:
        checks.append("Nothing in the numbers calls for an urgent visit; keep to the normal scouting round.")
    limits = ["Satellite indices describe canopy greenness and water, not the cause: they are not a diagnosis.",
              "Weather values are model analyses for a ~10 km grid cell, not a rain gauge on this field.",
              "Sentinel-2 pixels are 10-20 m, so field edges, bunds and trees mix into the edge pixels."]
    return {"findings": findings, "checks": checks, "limits": limits}


def parse_question(question, today=None):
    """-> {"action", "params", "interpretation"}; action "help" when the question is not understood."""
    q = " ".join(str(question).lower().split())
    distances, years, reach = parse_distances_m(q), parse_years(q, today), parse_reach(q)
    where = reach or "the whole mapped river"
    river = _has(q, "river", "nakat", "naktia", "nadi", "corridor", "riparian", "riverbank", "river bank",
                 "encroach", "floodplain", "stream", "ramganga")
    fields = _has(q, "field", "farm", "crop", "plot", "khet")

    if _has(q, "qila", "kila", "quila"):
        return {"action": "qila", "params": {},
                "interpretation": "Qila river: the map shows unverified candidate OpenStreetMap ways only."}
    if river and fields:
        d = _corridor(distances, DEFAULT_NEAR_M)
        return {"action": "river_fields", "params": {"distance_m": d, "reach": reach},
                "interpretation": f"Scouting cells within {d} m of the Nakatiya ({where}) whose latest Sentinel-2 "
                                  "change is unusual for that corridor."}
    if _has(q, "flood", "inundat", "waterlog", "overflow", "submerg", "water expansion", "water spread"):
        d = _corridor(distances, 500)
        return {"action": "river_flood", "params": {"buffer_m": d, "reach": reach},
                "interpretation": f"Sentinel-1 radar flood / water-expansion watch within {d} m of the Nakatiya "
                                  f"({where}): latest pass versus the previous pass on the same orbit."}
    if river and _has(q, "course", "path", "route", "origin", "source", "confluence", "length", "how long",
                      "trace", "drain", "flows", "where does", "map the"):
        return {"action": "river_course", "params": {},
                "interpretation": "Mapped course of the Nakatiya from its head to the Ramganga confluence."}
    if river and _has(q, "construction", "alert", "clearing", "cleared", "new build", "kiln", "mining", "dumping",
                      "recent", "this year"):
        d = _corridor(distances, 250)
        return {"action": "river_alerts", "params": {"buffer_m": d, "reach": reach},
                "interpretation": f"Construction / clearing candidates within {d} m of the Nakatiya ({where}): land "
                                  "green in the same 90 days a year ago that has stayed bare for the last 90 days."}
    if river and _has(q, "vegetation", "riparian", "green", "tree", "canopy"):
        d = _corridor(distances, 100)
        return {"action": "river_vegetation", "params": {"buffer_m": d, "reach": reach},
                "interpretation": f"Riparian vegetation health within {d} m of the Nakatiya ({where}): the "
                                  "vegetated share in the latest 1 January-31 March window against the year before "
                                  "(Sentinel-2)."}
    if river and len(distances) >= 2:
        buffers = tuple(sorted({_corridor([x], 100) for x in distances}))
        ya, yb, note = _year_pair(years, 2017, today)
        return {"action": "river_ladder",
                "params": {"buffers": buffers, "year_a": ya, "year_b": yb, "reach": reach},
                "interpretation": f"Change {ya}→{yb} in nested corridors of {', '.join(map(str, buffers))} m "
                                  f"around the Nakatiya ({where}){note}."}
    if river and (_has(q, "over the years", "timeline", "history", "trend", "decade", "every year",
                       "year by year", "footprint", "since 19") or len(years) >= 3):
        d = _corridor(distances, 100)
        yrs = tuple(y for y in years if y >= LANDSAT_FIRST_YEAR) if len(years) >= 3 else \
            default_timeline_years(today)
        return {"action": "river_timeline", "params": {"buffer_m": d, "years": yrs, "reach": reach},
                "interpretation": f"History within {d} m of the Nakatiya ({where}): settled area 1985-2015, built "
                                  "area from 2017, and Landsat water and vegetation for the rabi seasons of "
                                  f"{', '.join(map(str, yrs))}."}
    if river:
        d = _corridor(distances, 250)
        ya, yb, note = _year_pair(years, 2017, today)
        return {"action": "river_change", "params": {"buffer_m": d, "year_a": ya, "year_b": yb, "reach": reach},
                "interpretation": f"Change flags {ya}→{yb} within {d} m of the Nakatiya ({where}): built-up growth "
                                  f"from settlement and land-cover maps, water and vegetation from Landsat{note}."}
    if _has(q, "water", "moisture", "irrigat", "dry", "drought", "rain", "thirst"):
        return {"action": "water", "params": {},
                "interpretation": "Water-stress signal for the current area: canopy moisture plus the 14-day "
                                  "water balance."}
    if _has(q, "scout", "queue", "priorit", "visit", "inspect", "where should", "which fields", "hotspot"):
        return {"action": "scout", "params": {},
                "interpretation": "Scouting queue: cells of the current area ranked by unusual Sentinel-2 change."}
    if _has(q, "radar", "sar", "fusion", "sentinel-1", "optical", "agree"):
        return {"action": "fusion", "params": {},
                "interpretation": "Do radar (Sentinel-1) and optical (Sentinel-2) agree on the latest change?"}
    if _has(q, "change", "anomal", "unusual", "trend", "health", "ndvi", "declin", "stress", "greener"):
        return {"action": "change", "params": {},
                "interpretation": "Change radar: NDVI/NDMI time series and the latest move for the current area."}
    if _has(q, "field", "scan", "crop", "farm"):
        return {"action": "field", "params": {},
                "interpretation": "Field scan of the current area with an evidence brief."}
    return {"action": "help", "params": {}, "interpretation": "Not understood yet. Try one of the examples."}
