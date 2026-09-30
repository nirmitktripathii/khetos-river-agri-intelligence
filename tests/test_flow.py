"""Offline tests for src/flow.py: volumes, seasons, dependable flows, trends, the curve-number scenario and the
float-method arithmetic. The only file read is the bundled GEOGLOWS snapshot; HTTP is faked."""
import io
import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from src import flow


def daily(start, end, value=1.0):
    idx = pd.date_range(start, end, freq="D")
    return pd.Series(float(value), index=idx)


def month_number_flow(start, end):
    idx = pd.date_range(start, end, freq="D")
    return pd.Series(idx.month.astype("float64"), index=idx)


# ------------------------------------------------------------------------------- bundled snapshot

def test_bundled_snapshot_is_complete_and_grows_downstream():
    d = flow.load_daily()
    info = flow.snapshot_info()
    assert list(d.columns) == list(flow.SEGMENTS)
    assert d.index[0] == pd.Timestamp("1940-01-01")
    assert d.index[-1] == pd.Timestamp(info["last"])
    assert len(d) == len(pd.date_range(d.index[0], d.index[-1], freq="D")) == info["days"]
    assert d.notna().all().all() and (d >= 0).all().all()
    means = d.loc["1991":"2020"].mean()
    assert list(means[list(flow.NAKATIYA_KEYS)]) == sorted(means[list(flow.NAKATIYA_KEYS)])
    assert means["ramganga"] > 50 * means["mouth"]
    assert set(info["segments"]) == {str(s["id"]) for s in flow.SEGMENTS.values()}


def test_load_daily_returns_a_copy():
    d = flow.load_daily()
    d.iloc[0, 0] = -1
    assert flow.load_daily().iloc[0, 0] >= 0


def test_segment_areas_grow_downstream():
    areas = [flow.SEGMENTS[k]["area_km2"] for k in flow.NAKATIYA_KEYS]
    assert areas == sorted(areas)
    # the model's Ramganga segment should sit on the gauged main stem, not a tributary
    assert flow.SEGMENTS["ramganga"]["area_km2"] == pytest.approx(flow.CHAUBARI_GAUGE_AREA_KM2, rel=0.1)


# ------------------------------------------------------------------------------ volumes and years

def test_volume_and_runoff_depth():
    assert flow.volume_mcm(daily("2001-01-01", "2001-12-31", 1.0)) == pytest.approx(31.536)
    assert flow.volume_mcm([1.0, np.nan, 1.0]) == pytest.approx(0.1728)
    assert flow.runoff_mm(100.0, 400.0) == pytest.approx(250.0)  # 100 million m³ over 400 km² is 250 mm


def test_water_year_runs_june_to_may():
    idx = pd.DatetimeIndex(["2025-05-31", "2025-06-01", "2026-01-15"])
    assert list(flow.water_year(idx)) == [2024, 2025, 2025]
    assert flow.water_year_label(2024) == "2024-25"
    assert flow.water_year_label(1999) == "1999-00"


def test_annual_table_flags_part_years():
    t = flow.annual_table(daily("2000-01-01", "2002-06-30", 2.0))
    assert list(t.index) == [2000, 2001, 2002]
    assert list(t["days"]) == [366, 365, 181]
    assert list(t["complete"]) == [True, True, False]
    assert t.loc[2000, "volume_mcm"] == pytest.approx(2 * 366 * 86400 / 1e6)
    assert t.loc[2001, "mean_m3s"] == pytest.approx(2.0)
    assert t.loc[2001, "low_7day_m3s"] == pytest.approx(2.0)
    assert t.loc[2001, "peak_day_m3s"] == pytest.approx(2.0)


def test_annual_table_low_flow_is_a_seven_day_mean():
    s = daily("2001-01-01", "2001-12-31", 10.0)
    s.loc["2001-04-01":"2001-04-07"] = 1.0  # exactly one week of low flow
    s.loc["2001-08-10"] = 500.0
    t = flow.annual_table(s)
    assert t.loc[2001, "low_7day_m3s"] == pytest.approx(1.0)
    assert t.loc[2001, "peak_day_m3s"] == pytest.approx(500.0)


def test_water_year_table():
    t = flow.annual_table(daily("2000-06-01", "2004-05-31", 1.0), wy=True)
    assert list(t.index) == [2000, 2001, 2002, 2003]
    assert list(t["days"]) == [365, 365, 365, 366]  # June 2003 to May 2004 holds 29 February 2004
    assert t["complete"].all()
    part = flow.annual_table(daily("2000-01-01", "2000-12-31", 1.0), wy=True)
    assert list(part.index) == [1999, 2000] and not part["complete"].any()


def test_seasonal_table():
    t = flow.seasonal_table(month_number_flow("2001-01-01", "2001-11-30"))
    assert list(t["mean_m3s"].columns) == list(flow.SEASONS)
    row = t.loc[2001]
    assert row["mean_m3s"]["winter"] == pytest.approx(87 / 59)
    assert row["mean_m3s"]["pre-monsoon"] == pytest.approx(4.0)
    assert row["mean_m3s"]["monsoon"] == pytest.approx(7.5)
    assert row["volume_mcm"]["winter"] == pytest.approx(87 * 86400 / 1e6)
    assert row["days"]["monsoon"] == 122
    done = flow.season_complete(t).loc[2001]
    assert done["winter"] and done["pre-monsoon"] and done["monsoon"] and not done["post-monsoon"]


def test_every_month_belongs_to_one_season():
    months = sorted(m for ms in flow.SEASONS.values() for m in ms)
    assert months == list(range(1, 13))
    assert set(flow.SEASON_MONTHS) == set(flow.SEASONS)


def test_monthly_table_and_climatology():
    s = month_number_flow("2001-01-01", "2003-01-15")
    m = flow.monthly_table(s)
    assert m.index[0] == pd.Timestamp("2001-01-01") and len(m) == 25
    assert m["complete"].iloc[:-1].all() and not m["complete"].iloc[-1]
    assert m.loc["2001-02-01", "volume_mcm"] == pytest.approx(2 * 28 * 86400 / 1e6)
    c = flow.climatology(s, 2001, 2002)
    assert list(c.index) == list(range(1, 13))
    assert list(c["mean_m3s"]) == pytest.approx(list(range(1, 13)))
    assert list(c["p10_m3s"]) == pytest.approx(list(c["p90_m3s"]))
    assert c["share_pct"].sum() == pytest.approx(100.0)
    assert c.loc[12, "share_pct"] > c.loc[1, "share_pct"]


def test_flow_duration():
    fd = flow.flow_duration(pd.Series(np.arange(1, 101, dtype="float64")), probs=(10, 50, 90))
    assert fd[50] == pytest.approx(50.5)
    assert fd[90] == pytest.approx(10.9)  # the flow met or passed on 90 % of days is a low flow
    assert fd[10] > fd[50] > fd[90]


def test_dependable_uses_weibull_positions():
    got = flow.dependable(range(1, 10), probs=(50, 75, 90, 95))  # nine years: positions 10 %, 20 %, ... 90 %
    assert got[50] == pytest.approx(5.0)
    assert got[75] == pytest.approx(2.5)
    assert got[90] == pytest.approx(1.0)
    assert math.isnan(got[95])  # nine years cannot show a 95 % dependable value
    assert flow.dependable(pd.Series([3.0, np.nan, 1.0, 2.0]), probs=(50,))[50] == pytest.approx(2.0)
    assert math.isnan(flow.dependable([], probs=(75,))[75])


# ----------------------------------------------------------------------------------------- trends

def brute_force_mann_kendall(y):
    """S, its tie-corrected variance and the two-sided p-value, written the slow obvious way."""
    n = len(y)
    s = sum(int(y[j] > y[i]) - int(y[j] < y[i]) for i in range(n) for j in range(i + 1, n))
    counts = pd.Series(y).value_counts()
    var = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in counts)) / 18
    if s == 0:
        return s, var, 1.0
    z = (abs(s) - 1) / math.sqrt(var)
    return s, var, 2 * (1 - NormalDist().cdf(z))


def test_trend_recovers_a_known_slope():
    years = np.arange(1985, 2026)
    rng = np.random.default_rng(3)
    y = pd.Series(2.0 + 0.05 * (years - 1985) + rng.normal(0, 0.05, len(years)), index=years)
    t = flow.trend(y)
    assert t["n"] == 41
    assert t["slope_per_year"] == pytest.approx(0.05, abs=0.003)
    assert t["pct_per_decade"] == pytest.approx(1000 * t["slope_per_year"] / y.mean())
    assert t["p"] < 1e-6 and t["tau"] > 0.9
    assert t["p"] == pytest.approx(brute_force_mann_kendall(y.to_numpy())[2], rel=1e-6)


def test_trend_of_a_strictly_rising_series():
    t = flow.trend(pd.Series(np.arange(10, dtype="float64"), index=np.arange(2000, 2010)))
    assert t["tau"] == pytest.approx(1.0)
    assert t["slope_per_year"] == pytest.approx(1.0)
    assert t["p"] == pytest.approx(2 * (1 - NormalDist().cdf(44 / math.sqrt(125))))  # S = 45, variance = 125


def test_trend_with_ties_matches_brute_force():
    years = np.arange(1990, 2020)
    y = np.array([0.0] * 12 + [0.01] * 6 + [0.02, 0.0, 0.03, 0.01, 0.05, 0.02, 0.04, 0.0, 0.06, 0.03, 0.07, 0.05])
    t = flow.trend(pd.Series(y, index=years))
    s, var, p = brute_force_mann_kendall(y)
    assert var < 30 * 29 * 65 / 18  # ties shrink the variance
    assert t["p"] == pytest.approx(p, rel=1e-9)
    assert t["tau"] > 0 and 0 < t["p"] < 0.05


def test_trend_flat_short_and_gappy_series():
    flat = flow.trend(pd.Series(1.0, index=np.arange(2000, 2020)))
    assert flat["slope_per_year"] == 0 and flat["p"] == 1.0 and flat["p_persist"] == 1.0
    short = flow.trend(pd.Series([1.0, 2.0, 3.0], index=[2000, 2001, 2002]))
    assert short["n"] == 3 and math.isnan(short["p"]) and math.isnan(short["slope_per_year"])
    gappy = pd.Series(np.arange(20, dtype="float64"), index=np.arange(2000, 2020))
    gappy.iloc[[3, 9]] = np.nan
    assert flow.trend(gappy)["n"] == 18


def test_persistence_weakens_the_evidence():
    years = np.arange(1950, 2020)
    wave = pd.Series(0.004 * (years - 1950) + np.sin((years - 1950) / 6.0), index=years)  # slow swings
    t = flow.trend(wave)
    assert t["lag1"] > 1.96 / math.sqrt(len(years))
    assert t["p_persist"] > t["p"]
    rng = np.random.default_rng(11)
    noise = pd.Series(0.03 * (years - 1950) + rng.normal(0, 0.3, len(years)), index=years)
    t = flow.trend(noise)
    if t["lag1"] <= 1.96 / math.sqrt(len(years)):  # independent years: no correction is applied
        assert t["p_persist"] == t["p"]


def test_trend_agrees_with_scipy():
    stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(5)
    years = np.arange(1985, 2026)
    y = np.round(1.0 + 0.02 * (years - 1985) + rng.normal(0, 0.3, len(years)), 1)  # rounding makes ties
    t = flow.trend(pd.Series(y, index=years))
    assert t["slope_per_year"] == pytest.approx(stats.theilslopes(y, years)[0])
    assert t["tau"] == pytest.approx(stats.kendalltau(years, y).statistic)


def test_trend_table_on_the_bundled_mouth_series():
    table = flow.trend_table(flow.load_daily()["mouth"])
    assert {"series", "period", "n", "pct_per_decade", "p", "p_persist"} <= set(table.columns)
    assert len(table) == 2 * len(flow.yearly_series(flow.load_daily()["mouth"]))
    recent = table[table["period"].str.startswith(str(flow.TREND_START))]
    assert (recent["n"] >= 40).all() and recent["p"].between(0, 1).all()


# ---------------------------------------------------------------------------- land-cover scenario

def test_curve_numbers_match_the_thesis():
    cn1, cn2, cn3 = flow.curve_numbers(75.63)  # 1979 land cover in the Bareilly thesis
    assert (round(cn1, 2), cn2, round(cn3, 2)) == (56.59, 75.63, 87.71)
    # 2009 land cover: the thesis prints CN-I as 60.38, the formula gives 60.387 (the thesis cuts it short)
    assert flow.curve_numbers(78.40) == pytest.approx((60.38, 78.40, 89.30), abs=0.01)


def test_scs_runoff_matches_the_thesis_worked_days():
    assert float(flow.scs_runoff(24.6, 75.63)) == pytest.approx(0.751986, abs=2e-3)  # 1 July 1977
    assert float(flow.scs_runoff(28.4, 87.71)) == pytest.approx(7.962414, abs=5e-3)  # 3 July 1977
    assert float(flow.scs_runoff(13.6, 56.59)) == 0.0  # less rain than the soil can take up
    assert float(flow.scs_runoff(50.0, 100)) == pytest.approx(50.0)  # a sealed surface sheds everything
    assert float(flow.scs_runoff(0.0, 100)) == 0.0
    q = flow.scs_runoff(np.array([0.0, 20.0, 60.0, 120.0]), 78.4)
    assert (np.diff(q) >= 0).all() and (q <= np.array([0.0, 20.0, 60.0, 120.0])).all()


def test_scs_daily_uses_the_rain_of_the_five_days_before():
    idx = pd.date_range("2001-07-01", periods=7, freq="D")
    cn1, cn2, cn3 = flow.curve_numbers(75.63)
    dry = flow.scs_daily(pd.Series([60.0, 0, 0, 0, 0, 0, 60.0], index=idx), cn2)
    assert dry.iloc[0] == pytest.approx(float(flow.scs_runoff(60.0, cn1)))  # nothing before: dry soil
    assert dry.iloc[6] == pytest.approx(float(flow.scs_runoff(60.0, cn1)))  # the first storm is 6 days back
    average = flow.scs_daily(pd.Series([0, 40.0, 0, 0, 0, 0, 60.0], index=idx), cn2)
    assert average.iloc[6] == pytest.approx(float(flow.scs_runoff(60.0, cn2)))  # 40 mm in the 5 days before
    wet = flow.scs_daily(pd.Series([0, 30.0, 30.0, 0, 0, 0, 60.0], index=idx), cn2)
    assert wet.iloc[6] == pytest.approx(float(flow.scs_runoff(60.0, cn3)))  # 60 mm before: wet soil
    assert wet.iloc[6] > average.iloc[6] > dry.iloc[6]
    assert flow.scs_daily(pd.Series([np.nan, 5.0], index=idx[:2]), cn2).notna().all()


def test_a_higher_curve_number_sheds_more_of_the_same_rain():
    idx = pd.date_range("2001-06-01", periods=120, freq="D")
    rain = pd.Series(np.random.default_rng(2).gamma(0.4, 25.0, len(idx)), index=idx)
    totals = [flow.scs_daily(rain, cn).sum() for cn in (70, 75.63, 78.40, 85)]
    assert totals == sorted(totals) and totals[0] < totals[-1] < rain.sum()


# ---------------------------------------------------------------------------------- field gauging

def test_section_area_joins_depths_to_both_banks():
    assert flow.section_area(4.0, [0.2, 0.4, 0.2]) == pytest.approx(0.8)  # spacing 1 m x depths summing to 0.8 m
    assert flow.section_area(3.0, [0.6]) == pytest.approx(0.9)  # one reading: a triangle, 3 x 0.6 / 2
    for bad in ((0, [0.2]), (4, []), (4, [0.2, -0.1])):
        with pytest.raises(ValueError):
            flow.section_area(*bad)


def test_float_discharge():
    q = flow.float_discharge(4.0, [0.2, 0.4, 0.2], 10.0, [19.0, 20.0, 21.0])
    assert q["area_m2"] == pytest.approx(0.8)
    assert q["mean_depth_m"] == pytest.approx(0.2)
    assert q["surface_velocity_ms"] == pytest.approx(0.5)
    assert q["mean_velocity_ms"] == pytest.approx(0.425)
    assert q["discharge_m3s"] == pytest.approx(0.34)
    assert (q["discharge_low_m3s"], q["discharge_high_m3s"]) == pytest.approx((0.32, 0.36))
    assert q["discharge_mld"] == pytest.approx(0.34 * 86.4)
    assert q["discharge_lps"] == pytest.approx(340.0)
    assert q["time_spread"] == pytest.approx(0.1)
    assert (q["runs"], q["depth_readings"]) == (3, 3)
    assert flow.float_discharge(4.0, [0.2, 0.4, 0.2], 10.0, [20.0], 0.8)["discharge_m3s"] == pytest.approx(0.32)


@pytest.mark.parametrize("args", [(4.0, [0.2], 0.0, [20.0]), (4.0, [0.2], 10.0, []), (4.0, [0.2], 10.0, [0.0]),
                                  (4.0, [0.2], 10.0, [20.0], 1.2), (4.0, [0.2], 10.0, [20.0], 0.3)])
def test_float_discharge_rejects_impossible_readings(args):
    with pytest.raises(ValueError):
        flow.float_discharge(*args)


def test_parse_numbers():
    assert flow.parse_numbers("0.4, 0.7; 0.5  0.2") == [0.4, 0.7, 0.5, 0.2]
    assert flow.parse_numbers("") == [] and flow.parse_numbers(None) == []
    with pytest.raises(ValueError):
        flow.parse_numbers("0.4, deep")


def test_field_log_round_trip_recomputes_discharge():
    rows = [flow.field_reading("2027-03-14", " Dohra Road bridge ", 4.0, [0.2, 0.4, 0.2], 10.0, [19, 20, 21],
                               lat=28.3734, lon=79.4691, note="clear water"),
            flow.field_reading(pd.Timestamp("2027-03-15"), "Bisalpur Road", 6.5, [0.3, 0.5], 8.0, [16.0], 0.8)]
    log = pd.DataFrame(rows, columns=flow.FIELD_COLUMNS)
    assert list(log.columns) == flow.FIELD_COLUMNS
    assert log.loc[0, "site"] == "Dohra Road bridge" and log.loc[0, "discharge_m3s"] == pytest.approx(0.34)
    assert log.loc[0, "depths_m"] == "0.2 0.4 0.2" and log.loc[0, "times_s"] == "19 20 21"

    tampered = log.copy()
    tampered.loc[0, "discharge_m3s"] = 99.0
    back = flow.read_field_log(io.StringIO(tampered.to_csv(index=False)))
    assert back.loc[0, "discharge_m3s"] == pytest.approx(0.34)  # rebuilt from the raw readings
    assert back.loc[1, "coefficient"] == pytest.approx(0.8)
    assert back.loc[1, "note"] == "" and back.loc[0, "lat"] == pytest.approx(28.3734)
    pd.testing.assert_frame_equal(back.drop(columns=["lat", "lon"]), log.drop(columns=["lat", "lon"]))

    with pytest.raises(ValueError, match="not a KhetOS field log"):
        flow.read_field_log(io.StringIO("date,site\n2027-03-14,x\n"))


# ---------------------------------------------------------------------------------------- fetching

class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_fetch_daily_parses_the_api_csv(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen.update(url=url, params=params)
        return FakeResponse("time,441161738\n1940-01-01 00:00:00+00:00,0.01\n1940-01-02 00:00:00+00:00,0.02\n")

    monkeypatch.setattr(flow.requests, "get", fake_get)
    s = flow.fetch_daily(441161738)
    assert seen["url"].endswith("retrospectivedaily/441161738") and seen["params"] == {"format": "csv"}
    assert list(s.index) == [pd.Timestamp("1940-01-01"), pd.Timestamp("1940-01-02")] and s.index.tz is None
    assert list(s) == [0.01, 0.02]


def test_fetch_forecast_converts_to_india_time(monkeypatch):
    text = ("datetime,flow_uncertainty_upper,flow_median,flow_uncertainty_lower\n"
            "2026-09-29T00:00:00+00:00,120.5,110.7,101.2\n2026-09-29T03:00:00+00:00,118.0,108.1,99.4\n")
    monkeypatch.setattr(flow.requests, "get", lambda url, params=None, timeout=None: FakeResponse(text))
    f = flow.fetch_forecast(441161738)
    assert list(f.columns) == ["median", "low", "high"]
    assert f.index[0] == pd.Timestamp("2026-09-29 05:30") and f.index.tz is None
    assert f.iloc[0].to_dict() == {"median": 110.7, "low": 101.2, "high": 120.5}


# -------------------------------------------------------------------------------- satellite widths

def test_width_table():
    w = flow.load_widths()
    assert {"reach", "date", "period", "width_m", "reach_km", "area_ha", "reliable"} <= set(w.columns)
    assert set(w["reach"]) == {"upper", "urban", "lower"}
    assert np.allclose(w["area_ha"], (w["width_m"].clip(lower=0) * w["reach_km"] / 10).round(2), atol=0.006)
    assert w["reliable"].dtype == bool and w["reliable"].any() and not w["reliable"].all()
    assert w["date"].between("2018-01-01", "2025-12-31").all()
