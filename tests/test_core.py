"""Offline tests of the pure logic: sensor scaling, grids, pools, scoring, the question parser and the river
corridor. No network access."""
import math
from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src import ai, analytics, boundaries, eo, maps, reports, river
from src.config import NAKATIYA_CONFLUENCE, NAKATIYA_POINT, NAKATIYA_REACHES

TODAY = date(2026, 9, 27)


def item(dt, **props):
    return SimpleNamespace(id=f"x{dt:%Y%m%d}", datetime=dt, properties=props)


def utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------- sensors

def test_s2_offset_follows_processing_baseline():
    assert eo.s2_boa_offset(item(utc(2023, 1, 1), **{"s2:processing_baseline": "05.09"})) == -1000
    assert eo.s2_boa_offset(item(utc(2020, 1, 1), **{"s2:processing_baseline": "02.14"})) == 0
    assert eo.s2_boa_offset(item(utc(2023, 1, 1))) == -1000  # no baseline: the date decides
    assert eo.s2_boa_offset(item(utc(2020, 1, 1))) == 0


def test_s2_reflectance_offset_nodata_and_clip():
    r = eo.s2_reflectance(np.array([0, 500, 1000, 3000]), -1000)
    assert np.isnan(r[0])
    assert r[1] == 0 and r[2] == 0  # below the offset clips to zero
    assert r[3] == pytest.approx(0.2)
    assert eo.s2_reflectance(np.array([2000]), 0)[0] == pytest.approx(0.2)


def test_scl_clear():
    assert eo.scl_clear(np.array([3, 4, 5, 6, 7, 8, 9, np.nan])).tolist() == [0, 1, 1, 1, 1, 0, 0, 0]


def test_landsat_qa_and_harmonisation():
    assert eo.landsat_clear(np.array([21824, 21824 | 0b1000, 1])).tolist() == [True, False, False]
    oli = eo.landsat_reflectance(np.array([20000.0]), "landsat-8", "red")[0]
    assert oli == pytest.approx(20000 * 2.75e-05 - 0.2)
    tm = eo.landsat_reflectance(np.array([20000.0]), "landsat-5", "red")[0]
    slope, intercept = eo.ETM_TO_OLI["red"]
    assert tm == pytest.approx(intercept + slope * oli)


def test_s1_db():
    db = eo.s1_db(np.array([1.0, 0.01, 0.0, -1.0]))
    assert db[0] == 0 and db[1] == pytest.approx(-20)
    assert np.isnan(db[2]) and np.isnan(db[3])


def test_normalized_difference_and_nanstat():
    nd = eo.normalized_difference(np.array([0.3, 0.0]), np.array([0.1, 0.0]))
    assert nd[0] == pytest.approx(0.5) and np.isnan(nd[1])
    out = eo._nanstat(np.nanmax, np.array([[1.0, np.nan], [3.0, np.nan]]))
    assert out[0] == 3 and np.isnan(out[1])


def test_latest_rabi_year():
    assert eo.latest_rabi_year(date(2026, 4, 30)) == 2025
    assert eo.latest_rabi_year(date(2026, 5, 1)) == 2026


# --------------------------------------------------------------------------------------- Landsat picks

def test_slc_off_and_peak():
    assert eo._slc_off(item(utc(2010, 2, 1), platform="landsat-7"))
    assert not eo._slc_off(item(utc(2002, 2, 1), platform="landsat-7"))
    assert not eo._slc_off(item(utc(2010, 2, 1), platform="landsat-5"))
    assert eo._in_peak(utc(2020, 1, 15)) and eo._in_peak(utc(2020, 3, 31))
    assert not eo._in_peak(utc(2020, 1, 14)) and not eo._in_peak(utc(2020, 12, 20))


def test_choose_landsat_scenes_prefers_core_season_and_thins():
    mask = np.ones((2, 2), bool)
    core = [(item(utc(2020, m, d), platform="landsat-8"), mask) for m, d in ((1, 5), (1, 20), (2, 10), (3, 1))]
    edge = [(item(utc(2019, 11, 20), platform="landsat-8"), mask)]
    assert [u[0].datetime.month for u in eo.choose_landsat_scenes(core + edge)] == [1, 1, 2, 3]
    many = [(item(utc(2020, 1 + i // 28, 1 + i % 28), platform="landsat-8"), mask) for i in range(20)]
    assert len(eo.choose_landsat_scenes(many, max_scenes=8)) == 8
    thin = [(item(utc(2019, 12, 10), platform="landsat-8"), mask)] + edge
    assert len(eo.choose_landsat_scenes(thin)) == 2  # too few core scenes: the edge month is added


def test_composite_quality():
    good = {"peak_scenes": 3, "coverage_pct": 95, "slc_off_scenes": 0, "scenes": 5, "season": "rabi Dec-Mar"}
    assert eo.composite_quality(good)[0] == "good"
    assert eo.composite_quality({**good, "peak_scenes": 0})[0] == "poor"
    assert eo.composite_quality({**good, "coverage_pct": 70})[0] == "fair"
    assert eo.composite_quality({**good, "slc_off_scenes": 5})[0] == "fair"


# ------------------------------------------------------------------------------------------ grids, pools

def test_make_grid_snaps_and_limits_size():
    lon, lat = NAKATIYA_POINT
    bbox = (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)
    g = eo.make_grid(bbox, res=10)
    assert g.res == 10 and g.transform.c % 10 == 0 and g.transform.f % 10 == 0
    big = eo.make_grid((lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5), res=10, max_px=1024)
    assert max(big.shape) <= 1024 and big.res % 10 == 0
    assert (eo.make_grid(bbox, res=30, origin=(15.0, 15.0)).transform.c - 15) % 30 == 0
    w, s, e, n = g.lonlat_bounds()
    assert w <= bbox[0] + 1e-4 and n >= bbox[3] - 1e-4


def test_geometry_pixels():
    lon, lat = NAKATIYA_POINT
    g = eo.make_grid((lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01), res=30)
    assert eo.geometry_pixels(g).all()
    west = {"type": "Polygon", "coordinates": [[(lon - 0.02, lat - 0.02), (lon, lat - 0.02), (lon, lat + 0.02),
                                                (lon - 0.02, lat + 0.02), (lon - 0.02, lat - 0.02)]]}
    assert 0.4 < eo.geometry_pixels(g, west).mean() < 0.6


def test_wsf_tiles():
    assert eo.wsf_tiles((79.3, 28.1, 79.6, 28.5)) == [eo.WSF_EVO_URL.format(lon=78, lat=28)]
    assert len(eo.wsf_tiles((79.9, 27.9, 80.1, 28.1))) == 4


def test_pools_run_and_nest():
    assert eo.run_analysis(lambda a, b=0: a + b, 2, b=3) == 5
    assert [f.result() for f in eo.run_jobs((pow, 2, 3), (abs, -4))] == [8, 4]
    assert eo._on_io(sum, [1, 2]) == 3
    assert eo._pmap(lambda x: x * 2, range(5)) == [0, 2, 4, 6, 8]
    # A task that fans out again from inside a pool thread must not deadlock.
    assert eo.run_analysis(lambda: eo._pmap(lambda x: x + 1, [1, 2, 3], io=False)) == [2, 3, 4]


def test_safe_skips_failures():
    def read(x):
        if x == 2:
            raise OSError("unreadable")
        return x
    assert [r for r in eo._pmap(eo._safe(read), [1, 2, 3]) if r] == [1, 3]


# ---------------------------------------------------------------------------------------- scoring

def test_robust_z_and_anomaly_score():
    ref = [0.0, 0.1, -0.1, 0.05, -0.05, 0.02]
    assert analytics.robust_z(0.5, ref) > 3
    assert math.isnan(analytics.robust_z(0.5, ref[:3]))
    assert math.isnan(analytics.robust_z(0.5, [0.1] * 6))  # flat reference
    assert analytics.anomaly_score({}) == 50
    assert analytics.anomaly_score({"ndvi_change": -0.3, "ndmi_change": -0.3}) == 100


def test_change_signal():
    s = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=3, freq="10D"),
                      "ndvi": [0.6, 0.62, 0.45], "ndmi": [0.2, 0.21, 0.1]})
    out = analytics.change_signal(s)
    assert out["label"] == "DECLINE" and out["ndvi_change"] == pytest.approx(-0.17)
    assert analytics.change_signal(s.iloc[:1]) is None


def test_water_stress_signal():
    assert analytics.water_stress_signal(float("nan"), 0) is None
    assert analytics.water_stress_signal(0.5, 50, et0_14d_mm=20)["water_stress_signal"] == 0
    dry = analytics.water_stress_signal(-0.2, 0, et0_14d_mm=60)
    assert dry["water_stress_signal"] == 100 and dry["water_deficit_14d_mm"] == 60


def test_fusion_assessment():
    o_now, o_prev = {"ndvi": 0.4, "date": "2026-02-10"}, {"ndvi": 0.6, "date": "2026-01-20"}
    s_now, s_prev = {"vh_db": -18.0, "rvi": 0.4, "date": "2026-02-08"}, {"vh_db": -16.0, "rvi": 0.5}
    out = analytics.fusion_assessment(o_now, o_prev, s_now, s_prev)
    assert out["label"] == "AGREE · vegetation decline" and out["optical_radar_gap_days"] == 2
    assert analytics.fusion_assessment(o_now, None, s_now, s_prev)["label"].startswith("INCOMPLETE")
    assert analytics.fusion_assessment(o_now, o_prev, s_now, {**s_prev, "vh_db": -20})["label"].startswith("DISAGREE")


# --------------------------------------------------------------------------------------- Ask the Map

@pytest.mark.parametrize("question, action", [
    ("Which fields within 500 m of the Nakatiya show unusual change?", "river_fields"),
    ("Is the Nakatiya flooding? Check water expansion within 500 m", "river_flood"),
    ("Trace the Nakatiya's course from origin to the Ramganga", "river_course"),
    ("Any new construction or clearing within 100 m of the Nakatiya in the city?", "river_alerts"),
    ("Where is crop water stress in my area?", "water"),
    ("Build the scouting queue for this area", "scout"),
    ("asdf qwerty", "help"),
])
def test_parse_question(question, action):
    out = ai.parse_question(question, today=TODAY)
    assert set(out) >= {"action", "params", "interpretation"}
    assert out["action"] == action


def test_every_example_is_understood():
    for q in ai.EXAMPLES:
        assert ai.parse_question(q, today=TODAY)["action"] != "help", q


def test_parsers():
    assert ai.parse_distances_m("within 0.5 km or 100m") == [500, 100]
    assert ai.parse_years("from 1975, 1984 to 2030 and 2016", today=TODAY) == [1984, 2016]
    assert ai.parse_reach("near bhojipura") == "Upper reach · Bhojipura side"
    assert set(ai.REACH_WORDS) <= set(NAKATIYA_REACHES)
    assert ai._corridor([5000], 500) == ai.MAX_CORRIDOR_M
    assert ai._corridor([], 500) == 500 and ai._corridor([2], 500) == 10


def test_year_pair_clamps():
    assert ai._year_pair([2016], 2017, TODAY)[:2] == (2016, 2026)
    a, b, note = ai._year_pair([1984, 2030], 2017, TODAY)
    assert (a, b) == (eo.LANDSAT_FIRST_YEAR, 2026) and "moved" in note
    assert ai._year_pair([2026], 2017, TODAY)[:2] == (2017, 2026)


def test_field_brief_uses_measured_values():
    scan = {"latest": {"date": "2026-03-20", "ndvi": 0.45, "ndmi": 0.1, "pixels": 1200},
            "previous": {"date": "2026-03-05", "ndvi": 0.62}}
    brief = ai.field_brief(scan)
    assert set(brief) >= {"findings", "checks", "limits"}
    text = " ".join(brief["findings"])
    assert "0.45" in text and "fell" in text


# --------------------------------------------------------------------------------------------- river

def test_nakatiya_course_reaches_ramganga():
    c = river.nakatiya_course()
    assert c["ways"] >= 3 and c["main_stem_km"] > 60
    assert c["confluence"] == pytest.approx(NAKATIYA_CONFLUENCE, abs=0.01)
    assert c["head"][1] > c["confluence"][1]  # flows south to the Ramganga
    assert "Unverified" in c["upstream_status"]


def test_corridor_geometry_and_distance():
    narrow = river.river_corridor_geometry(10, NAKATIYA_POINT, 2)
    wide = river.river_corridor_geometry(100, NAKATIYA_POINT, 2)
    assert river._area_km2(wide) > 5 * river._area_km2(narrow)
    lon, lat = NAKATIYA_POINT
    near, far = river.river_distance_m([{"type": "Point", "coordinates": [lon, lat]},
                                        {"type": "Point", "coordinates": [lon + 0.05, lat]}])
    assert near < far


def test_year_checks_and_season_sets():
    assert river._check_years(2017, 2020) == (2017, 2020)
    for bad in ((2020, 2017), (1985, 2000)):
        with pytest.raises(ValueError):
            river._check_years(*bad)
    a, b = river._season_sets(2000, 2001)
    assert not set(a) & set(b) and 2000 in a and 2001 in b
    assert min(river._season_sets(eo.LANDSAT_FIRST_YEAR, 2010)[0]) == eo.LANDSAT_FIRST_YEAR


def season(year, veg, water=5.0, peak=3, quality="good"):
    return {"year": year, "quality": quality, "peak_scenes": peak, "water_pct": water, "vegetation_pct": veg,
            "nongreen_pct": 100 - veg - water, "pixels": 1000}


def test_combine_and_caveats():
    end = river._combine(2020, [season(2019, 60), season(2020, 70), season(2021, 80, peak=1), None])
    assert end["seasons_used"] == [2019, 2020] and end["vegetation_pct"] == 65
    assert river._combine(2020, [season(2020, 60, quality="poor")]) is None
    cav = river._season_caveats({2019: season(2019, 60), 2021: season(2021, 60, peak=1), 2022: None})
    assert any("2021" in c for c in cav) and any("2022" in c for c in cav)
    assert river._narrow_caveat([10, 25, 50]) and not river._narrow_caveat([50, 100])


def test_built_periods():
    wsf, io, gaps = river._built_periods(2000, 2026)
    assert wsf == (2000, 2015) and io == (2017, 2025) and gaps == [(2015, 2017), (2025, 2026)]
    assert river._built_periods(2018, 2020)[0] is None


def test_assess_flags():
    a = river._combine(2010, [season(2009, 70, 8), season(2010, 72, 8)])
    b = river._combine(2020, [season(2019, 50, 3), season(2020, 52, 3)])
    out = river._assess(a, b, built={"change_pp": 5.0, "period": (2017, 2020)})
    assert set(out["flags"]) == {"BUILT-UP GROWTH", "VEGETATION LOSS", "WATER FOOTPRINT DECLINE"}
    assert out["change_flag"] == "HIGH"
    assert river._assess(a, b, landsat_flags=False)["flags"] == []
    assert river._assess(None, None)["change_flag"] == "NO DATA"
    single = river._combine(2020, [season(2020, 40)])
    assert river._assess(a, single)["flags"] == []  # one usable season: shown, not flagged


def test_vegetation_trend():
    years = np.arange(1991, 2027, 5)
    df = pd.DataFrame({"year": years, "vegetation_pct": 80 - 0.5 * (years - 1991), "used": True})
    t = river.vegetation_trend(df)
    assert t["flag"] == "SIGNIFICANT DECLINE" and t["slope_pp_per_decade"] == pytest.approx(-5.0)
    assert river.vegetation_trend(df.iloc[:4])["flag"] == "TOO FEW SEASONS"


def test_river_dates():
    assert river._year_earlier(date(2024, 2, 29)) == date(2023, 2, 28)
    assert river.latest_rabi_peak_year(date(2026, 3, 31)) == 2025
    assert river.latest_rabi_peak_year(date(2026, 4, 1)) == 2026
    years = river.default_timeline_years(TODAY)
    assert years[0] == 1991 and years[-1] == 2026 and list(years) == sorted(years)


# -------------------------------------------------------------------------------- maps, reports, data

def test_maps_helpers():
    empty = {"type": "FeatureCollection", "features": []}
    assert maps._empty(empty)
    rgba = maps.colorize(np.array([[0.0, np.nan], [1.0, 0.5]]), 0, 1)
    assert rgba.shape == (2, 2, 4) and rgba[0, 1, 3] == 0 and rgba[0, 0, 3] == 255
    m = maps.base_map((79.4, 28.3, 79.5, 28.4))
    maps.add_categorized(m, empty, "none", "priority", maps.PRIORITY_COLORS, ["priority"])  # must not raise


def test_markdown_report():
    md = reports.build_markdown_report("Test", {"score": 1.5, "table": pd.DataFrame({"a": [1]}),
                                                "geojson": {"type": "Point"}}, notes=["a note"])
    assert md.startswith("# KhetOS · Test")
    assert "a note" in md and "## Table" in md and "Point" not in md


def test_districts():
    names = {f["properties"]["district"] for f in boundaries.load_districts()["features"]}
    assert names == {"Bareilly", "Budaun", "Pilibhit", "Rampur", "Shahjahanpur"}
    assert boundaries.district("bareilly")["properties"]["district"] == "Bareilly"
