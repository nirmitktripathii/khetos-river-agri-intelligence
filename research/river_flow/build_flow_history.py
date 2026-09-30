"""Build the Nakatiya flow-history workbook and its plain CSV twins.

Every flow statistic is computed by src/flow.py from the bundled GEOGLOWS daily flows, so the workbook and the
app's River Water Watch page cannot disagree. Rain is the ERA5 series written by pull_rain.py; satellite widths are
the output of unmix_probe.py. Writes

    data/nakatiya_flow_history.xlsx          the workbook (the app offers it for download)
    data/nakatiya_open_water_width.csv       the width table the app reads
    research/river_flow/out/csv/*.csv        one plain file per table

The workbook needs openpyxl, which the app does not. Run from the repository root:

    uv run --with openpyxl python research/river_flow/build_flow_history.py

Without openpyxl the CSV files are still written.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(HERE)]

from pull_rain import CELLS, SHARE  # noqa: E402
from src import flow  # noqa: E402
from src.config import DATA_DIR  # noqa: E402

RAIN_FILE = HERE / "inputs" / "era5_daily.csv.gz"
UNMIX_FILE = HERE / "unmix" / "unmix_scenes.csv"
CSV_DIR = HERE / "out" / "csv"
BOOK = DATA_DIR / "nakatiya_flow_history.xlsx"

KEYS = list(flow.SEGMENTS)
BASE = flow.BASELINE
FIRST = flow.WARMUP_YEARS[-1] + 1  # first year used in statistics
SEASON_OF = {m: s for s, months in flow.SEASONS.items() for m in months}
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")
MOUTH_KM2 = flow.SEGMENTS["mouth"]["area_km2"]
FDC_PROBS = (1, 2, 5, 10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 95, 98, 99)

# S. S. Tripathi (2017), "Modelling the Effects of Land Use and Land Cover Change on Hydrologic Regime of Ramganga
# River Basin", Table 5.12: the area-weighted curve number (CN-II) of the 4,120 km² study area, that year's rain
# and the runoff and recharge worked out for it (mm).
THESIS = {1979: {"cn": 75.63, "rain_mm": 489.27, "runoff_mm": 12.69, "recharge_mm": 29.26},
          1990: {"cn": 76.65, "rain_mm": 1023.5, "runoff_mm": 123.74, "recharge_mm": 167.21},
          2009: {"cn": 78.40, "rain_mm": 1040.1, "runoff_mm": 221.91, "recharge_mm": 151.13}}
CN_SWEEP = (70, 72, 74, 75.63, 76.65, 78.40, 80, 82, 85, 90)
REACH_KM = {"upper": 30.09, "urban": 14.46, "lower": 14.62}  # river length inside each reach (unmix_probe.py log)


# ----------------------------------------------------------------------------------------- tables

def part_notes(index, complete, first_day, last_day):
    notes = []
    for y, ok in zip(index, complete):
        parts = ["warm-up year, left out of statistics"] if y <= flow.WARMUP_YEARS[-1] else []
        if not ok:
            parts.append(f"part year (from {first_day:%d %b %Y})" if y == index[0]
                         else f"part year (to {last_day:%d %b %Y})")
        notes.append(", ".join(parts))
    return notes


def direct_runoff(rain, cn2):
    """Daily curve-number runoff over the catchment (mm): worked out cell by cell and then weighted, because the
    method is not linear in rain."""
    return sum(SHARE[n] * flow.scs_daily(rain[f"rain_{n}_mm"], cn2) for n in CELLS)


def budyko_runoff(rain_mm, pet_mm):
    """Long-run yearly runoff (mm) from yearly rain and potential evapotranspiration by the Budyko (1974) curve:
    evaporation / rain = sqrt(x tanh(1/x) (1 - exp(-x))) with x = potential evapotranspiration / rain."""
    x = pet_mm / rain_mm
    return rain_mm * (1 - math.sqrt(x * math.tanh(1 / x) * (1 - math.exp(-x))))


def months_total(series, months):
    """Yearly totals of a daily series over the given calendar months, for the years that have every such day."""
    s = series[series.index.month.isin(months)]
    g = s.groupby(s.index.year)
    days = g.size()
    return g.sum()[days == [sum(pd.Period(f"{y}-{m:02d}").days_in_month for m in months) for y in days.index]]


def full_years(series):
    """Yearly totals of a daily series, complete calendar years only."""
    return months_total(series, range(1, 13))


def segments(d):
    rows = []
    for k, seg in flow.SEGMENTS.items():
        a = flow.annual_table(d[k])
        a = a[a["complete"]].loc[BASE[0]:BASE[1]]
        sea = flow.seasonal_table(d[k]).loc[BASE[0]:BASE[1]]
        x = d[k].loc[str(BASE[0]):str(BASE[1])]
        dep = flow.dependable(a["volume_mcm"])
        good = d[k].loc[str(FIRST):]
        rows.append({
            "segment": k, "name": seg["name"], "river_id": seg["id"], "main_stem_km": seg["km"] or "",
            "area_km2": seg["area_km2"], "outlet_lat": seg["outlet"][1], "outlet_lon": seg["outlet"][0],
            "mean_m3s": x.mean(), "median_m3s": x.median(), "volume_mcm": a["volume_mcm"].mean(),
            "runoff_mm": flow.runoff_mm(a["volume_mcm"].mean(), seg["area_km2"]),
            "dep50_volume_mcm": dep[50], "dep75_volume_mcm": dep[75], "dep90_volume_mcm": dep[90],
            **{f"{s}_mean_m3s": sea["mean_m3s"][s].mean() for s in flow.SEASONS},
            "monsoon_share_pct": 100 * sea["volume_mcm"]["monsoon"].sum() / sea["volume_mcm"].sum().sum(),
            "low_7day_m3s": a["low_7day_m3s"].mean(), "days_below_0p1_pct": 100 * (x < 0.1).mean(),
            "peak_day_m3s": good.max(), "peak_day_date": good.idxmax().date()})
    return pd.DataFrame(rows)


def annual(d, rain, wy=False):
    tabs = {k: flow.annual_table(d[k], wy=wy) for k in KEYS}
    ref = tabs["mouth"]
    key = flow.water_year(rain.index) if wy else rain.index.year
    out = pd.DataFrame(index=ref.index)
    if wy:
        out["water_year"] = [flow.water_year_label(y) for y in ref.index]
    out["days"], out["complete"] = ref["days"], ref["complete"]
    out["note"] = part_notes(ref.index, ref["complete"], d.index[0], d.index[-1])
    out["rain_mm"] = rain["rain_mm"].groupby(key).sum().reindex(ref.index)
    out["et0_mm"] = rain["et0_mm"].groupby(key).sum().reindex(ref.index)
    stats = ["mean_m3s", "volume_mcm", "peak_day_m3s"] + ([] if wy else ["low_7day_m3s"])
    for k in KEYS:  # a June-May year splits the May-June low-flow season, so it carries no 7-day low
        for s in stats:
            out[f"{k}_{s}"] = tabs[k][s]
    out["mouth_runoff_mm"] = flow.runoff_mm(out["mouth_volume_mcm"], MOUTH_KM2)
    out["mouth_runoff_pct_of_rain"] = (100 * out["mouth_runoff_mm"] / out["rain_mm"]).where(out["complete"])
    good = out["complete"] & (out.index >= FIRST)
    out["mouth_volume_10yr_mcm"] = out["mouth_volume_mcm"].where(good).rolling(10).mean()
    return out.reset_index()


def seasons(d, rain):
    tabs = {k: flow.seasonal_table(d[k]) for k in KEYS}
    ref = tabs["mouth"]
    ok = flow.season_complete(ref)
    wet = rain["rain_mm"].groupby([rain.index.year, rain.index.month.map(SEASON_OF)]).sum().unstack()
    frames = []
    for order, s in enumerate(flow.SEASONS):
        f = pd.DataFrame({"year": ref.index, "order": order, "season": s, "months": flow.SEASON_MONTHS[s],
                          "days": ref["days"][s].to_numpy(), "complete": ok[s].to_numpy(),
                          "rain_mm": wet[s].reindex(ref.index).to_numpy()})
        for k in KEYS:
            f[f"{k}_mean_m3s"] = tabs[k]["mean_m3s"][s].to_numpy()
            f[f"{k}_volume_mcm"] = tabs[k]["volume_mcm"][s].to_numpy()
        frames.append(f)
    out = pd.concat(frames).dropna(subset=["days"]).sort_values(["year", "order"]).drop(columns="order")
    return out.astype({"days": int}).reset_index(drop=True)


def months(d, rain):
    tabs = {k: flow.monthly_table(d[k]) for k in KEYS}
    ref = tabs["mouth"]
    out = pd.DataFrame({"year": ref.index.year, "month_number": ref.index.month, "days": ref["days"],
                        "complete": ref["complete"]}, index=ref.index)
    wet = rain[["rain_mm", "et0_mm"]].groupby(rain.index.to_period("M")).sum()
    wet.index = wet.index.to_timestamp()
    out[["rain_mm", "et0_mm"]] = wet.reindex(ref.index)
    for k in KEYS:
        out[f"{k}_mean_m3s"], out[f"{k}_volume_mcm"] = tabs[k]["mean_m3s"], tabs[k]["volume_mcm"]
    return out.reset_index()


def days(d, rain):
    labels = {y: flow.water_year_label(y) for y in range(d.index[0].year - 1, d.index[-1].year + 1)}
    out = pd.DataFrame({"year": d.index.year, "month": d.index.month,
                        "water_year": pd.Index(flow.water_year(d.index)).map(labels),
                        "season": d.index.month.map(SEASON_OF)}, index=d.index)
    for k in KEYS:
        out[f"{k}_m3s"] = d[k]
    out[["rain_mm", "et0_mm"]] = rain[["rain_mm", "et0_mm"]].reindex(d.index)
    return out.reset_index()


def average_year(d):
    frames = []
    for k in KEYS:
        c = flow.climatology(d[k]).reset_index()
        c.insert(0, "segment", k)
        frames.append(c)
    return pd.concat(frames, ignore_index=True)


def average_year_wide(avg, month_table):
    """The average year laid out for reading: one row per month, flows side by side, then rain."""
    out = pd.DataFrame({"month": range(1, 13)})
    out["name"] = [MONTH_NAMES[m - 1] for m in out["month"]]
    for k in KEYS:
        c = avg[avg["segment"] == k].set_index("month")
        out[f"{k}_mean_m3s"] = c["mean_m3s"].reindex(out["month"]).to_numpy()
        out[f"{k}_volume_mcm"] = c["volume_mcm"].reindex(out["month"]).to_numpy()
    c = avg[avg["segment"] == "mouth"].set_index("month").reindex(out["month"])
    for col in ("median_m3s", "p10_m3s", "p90_m3s", "share_pct"):
        out[f"mouth_{col}"] = c[col].to_numpy()
    m = month_table[month_table["complete"] & month_table["year"].between(*BASE)]
    wet = m.groupby("month_number")[["rain_mm", "et0_mm"]].mean()
    out[["rain_mm", "et0_mm"]] = wet.reindex(out["month"]).to_numpy()
    return out


def dry_years(d, ann):
    """The two warm-up years beside the 1987 drought, the year after each, and the average year: if the model had
    started from an empty river, 1940-41 would turn less of their rain into flow than the later drought did."""
    a = ann.set_index("year")
    zero = (d["mouth"] < 0.01).groupby(d.index.year).sum()
    rows = []
    for y, label in ((1940, "warm-up year"), (1941, "warm-up year"), (1942, "year after"),
                     (1987, "drought year"), (1988, "year after")):
        rows.append({"year": str(y), "what": label, "rain_mm": a["rain_mm"][y],
                     "mouth_runoff_mm": a["mouth_runoff_mm"][y], "pct_of_rain": a["mouth_runoff_pct_of_rain"][y],
                     "mouth_mean_m3s": a["mouth_mean_m3s"][y], "mouth_low_7day_m3s": a["mouth_low_7day_m3s"][y],
                     "zero_flow_days": int(zero[y])})
    b = a[a["complete"]].loc[BASE[0]:BASE[1]]
    rows.append({"year": f"{BASE[0]}-{BASE[1]}", "what": "average year", "rain_mm": b["rain_mm"].mean(),
                 "mouth_runoff_mm": b["mouth_runoff_mm"].mean(),
                 "pct_of_rain": 100 * b["mouth_runoff_mm"].sum() / b["rain_mm"].sum(),
                 "mouth_mean_m3s": b["mouth_mean_m3s"].mean(), "mouth_low_7day_m3s": b["mouth_low_7day_m3s"].mean(),
                 "zero_flow_days": float(zero.loc[BASE[0]:BASE[1]].mean())})
    return pd.DataFrame(rows)


def dependable_table(d, windows):
    rows = []
    for k in KEYS:
        cal, wy, sea = flow.annual_table(d[k]), flow.annual_table(d[k], wy=True), flow.seasonal_table(d[k])
        ok = flow.season_complete(sea)
        series = {"calendar year (Jan-Dec)": cal["volume_mcm"][cal["complete"]],
                  "water year (Jun-May)": wy["volume_mcm"][wy["complete"]]}
        series.update({f"{s} ({flow.SEASON_MONTHS[s]})": sea["volume_mcm"][s][ok[s]] for s in flow.SEASONS})
        for name, v in series.items():
            for a, b in windows:
                x = v.loc[a:b]
                dep = flow.dependable(x)
                rows.append({"segment": k, "period": name, "years": f"{a}-{b}", "n_years": len(x),
                             "mean_mcm": x.mean(), "dep50_mcm": dep[50], "dep75_mcm": dep[75],
                             "dep90_mcm": dep[90], "lowest_mcm": x.min(), "lowest_year": int(x.idxmin()),
                             "highest_mcm": x.max(), "highest_year": int(x.idxmax())})
    return pd.DataFrame(rows)


def flow_duration_table(d, windows):
    rows = []
    for a, b in windows:
        curves = {k: flow.flow_duration(d[k].loc[str(a):str(b)], FDC_PROBS) for k in KEYS}
        rows += [{"years": f"{a}-{b}", "pct_of_days": p, **{f"{k}_m3s": curves[k][p] for k in KEYS}}
                 for p in FDC_PROBS]
    return pd.DataFrame(rows)


def reading(t):
    return flow.trend_reading(t)


def trends(d, rain):
    frames = []
    for k in KEYS:
        t = flow.trend_table(d[k])
        t.insert(0, "segment", k)
        t.insert(1, "unit", "m3/s")
        frames.append(t)
    # The weather the model is driven by: a trend in a flow means little until it is set against these.
    wet, pet = rain["rain_mm"], rain["et0_mm"]
    series = {"rain of the year": full_years(wet),
              "rain of the monsoon (Jun-Sep)": months_total(wet, flow.SEASONS["monsoon"]),
              "rain of the dry months (Jan-May)": months_total(wet, (1, 2, 3, 4, 5)),
              "evaporative demand of the year": full_years(pet),
              "evaporative demand of the pre-monsoon (Mar-May)": months_total(pet, flow.SEASONS["pre-monsoon"])}
    rows = []
    for name, s in series.items():
        for start in (flow.TREND_START, 1950):
            x = s.loc[start:]
            rows.append({"segment": "weather", "unit": "mm", "series": name,
                         "period": f"{x.index[0]}-{x.index[-1]}", **flow.trend(x)})
    out = pd.concat(frames + [pd.DataFrame(rows).drop(columns="intercept")], ignore_index=True)
    out["reading"] = [reading(t) for t in out.to_dict("records")]
    return out


def decades(ann, sea):
    a = ann[ann["complete"] & (ann["year"] >= FIRST)]
    s = sea[sea["complete"] & sea["year"].between(FIRST, a["year"].max())]
    rows = []
    for ten, g in a.groupby(a["year"] // 10 * 10):
        in_decade = s["year"].isin(g["year"])
        row = {"decade": f"{g['year'].min()}-{g['year'].max() % 100:02d}", "years": len(g),
               "rain_mm": g["rain_mm"].mean(), "et0_mm": g["et0_mm"].mean()}
        row.update({f"{k}_volume_mcm": g[f"{k}_volume_mcm"].mean() for k in KEYS})
        row["mouth_runoff_mm"] = g["mouth_runoff_mm"].mean()
        row["mouth_runoff_pct_of_rain"] = 100 * g["mouth_runoff_mm"].sum() / g["rain_mm"].sum()
        for season in ("pre-monsoon", "monsoon"):
            flows = s.loc[in_decade & (s["season"] == season), "mouth_mean_m3s"]
            row[f"mouth_{season.replace('-', '_')}_m3s"] = flows.mean()
        row["mouth_low_7day_m3s"] = g["mouth_low_7day_m3s"].mean()
        rows.append(row)
    return pd.DataFrame(rows)


def rain_runoff(ann, rain):
    year = rain.index.year
    g = rain.groupby(year)
    out = pd.DataFrame({"days": g.size()})
    out[[f"rain_{n}_mm" for n in CELLS] + ["rain_mm", "et0_mm"]] = g[
        [f"rain_{n}_mm" for n in CELLS] + ["rain_mm", "et0_mm"]].sum()
    wet = rain["rain_mm"][rain.index.month.isin(flow.SEASONS["monsoon"])]
    out["monsoon_rain_mm"] = wet.groupby(wet.index.year).sum()
    a = ann.set_index("year")
    out["mouth_runoff_mm"] = a["mouth_runoff_mm"]
    out["mouth_runoff_pct_of_rain"] = a["mouth_runoff_pct_of_rain"]
    for y, t in THESIS.items():
        q = direct_runoff(rain, t["cn"])
        out[f"direct_runoff_cn{y}_mm"] = q.groupby(year).sum()
    out["extra_runoff_mm"] = out["direct_runoff_cn2009_mm"] - out["direct_runoff_cn1979_mm"]
    out["extra_runoff_mcm"] = out["extra_runoff_mm"] * MOUTH_KM2 / 1000
    out.insert(1, "note", a["note"])
    out.index.name = "year"
    return out.reset_index()


def scenario(rain):
    wet = full_years(rain["rain_mm"]).loc[BASE[0]:BASE[1]].mean()
    rows = []
    for cn in CN_SWEEP:
        q = full_years(direct_runoff(rain, cn)).loc[BASE[0]:BASE[1]].mean()
        cn1, _, cn3 = flow.curve_numbers(cn)
        label = next((f"thesis, {y} land cover" for y, t in THESIS.items() if t["cn"] == cn), "")
        rows.append({"cn2": cn, "land_cover": label, "cn1_dry": cn1, "cn3_wet": cn3, "direct_runoff_mm": q,
                     "pct_of_rain": 100 * q / wet, "volume_mcm": q * MOUTH_KM2 / 1000})
    out = pd.DataFrame(rows)
    ref = out.loc[out["cn2"] == THESIS[1979]["cn"], "direct_runoff_mm"].iloc[0]
    out["change_mm"] = out["direct_runoff_mm"] - ref
    out["change_pct"] = 100 * out["change_mm"] / ref
    out["change_mcm"] = out["change_mm"] * MOUTH_KM2 / 1000
    return out


def thesis_years(rain):
    wet = rain.groupby(rain.index.year)[["rain_mm", "rain_south_mm"]].sum()
    runs = {y: direct_runoff(rain, t["cn"]).groupby(rain.index.year).sum() for y, t in THESIS.items()}
    rows = []
    for y, t in THESIS.items():
        rows.append({"year": y, "thesis_cn2": t["cn"], "thesis_rain_mm": t["rain_mm"],
                     "thesis_runoff_mm": t["runoff_mm"], "thesis_recharge_mm": t["recharge_mm"],
                     "era5_rain_mm": wet["rain_mm"][y], "era5_south_rain_mm": wet["rain_south_mm"][y],
                     "era5_runoff_own_cn_mm": runs[y][y], "era5_runoff_cn1979_mm": runs[1979][y],
                     "era5_runoff_cn2009_mm": runs[2009][y]})
    return pd.DataFrame(rows)


def width_table(d):
    u = pd.read_csv(UNMIX_FILE, parse_dates=["date"])
    borrowed, dirty = u["water_src"] == "library", u["em_water_B11"] > 0.05
    out = pd.DataFrame({
        "reach": u["reach"], "date": u["date"],
        "period": np.where(u["date"].dt.month.isin((10, 11)), "after monsoon", "dry months"),
        "width_m": u["weq_m"].round(2), "reach_km": u["reach"].map(REACH_KM)})
    out["area_ha"] = (out["width_m"].clip(lower=0) * out["reach_km"] / 10).round(2)
    out["reliable"] = ~(borrowed | dirty)
    out["water_signature"] = np.where(borrowed, "borrowed from another date",
                                      np.where(dirty, "same day, mixed with land", "same day"))
    out["mouth_flow_m3s"] = d["mouth"].reindex(out["date"]).to_numpy()
    out["scene"] = u["scene"]
    out["order"] = out["reach"].map({r: i for i, r in enumerate(REACH_KM)})
    return out.sort_values(["order", "date"]).drop(columns="order").reset_index(drop=True)


def yield_estimates(ann, rain, sc):
    a = ann[ann["complete"] & ann["year"].between(*BASE)]
    wet, pet = a["rain_mm"].mean(), a["et0_mm"].mean()
    cn = sc.set_index("cn2")["direct_runoff_mm"]
    rows = [("GEOGLOWS v2: ERA5 runoff routed down the river network",
             "storm runoff and seepage from the soil, from weather alone", a["mouth_runoff_mm"].mean()),
            ("Curve-number method with the thesis's 2009 land cover (CN 78.40)",
             "storm runoff only, no seepage into the river", cn[THESIS[2009]["cn"]]),
            ("Curve-number method with the thesis's 1979 land cover (CN 75.63)",
             "storm runoff only, no seepage into the river", cn[THESIS[1979]["cn"]]),
            ("Long-run water balance: rain minus evaporation (Budyko curve)",
             "all water leaving the catchment, in the river or underground", budyko_runoff(wet, pet))]
    out = pd.DataFrame(rows, columns=["method", "what_it_counts", "runoff_mm"])
    out["pct_of_rain"] = 100 * out["runoff_mm"] / wet
    out["volume_mcm"] = out["runoff_mm"] * MOUTH_KM2 / 1000
    out["mean_m3s"] = out["volume_mcm"] * 1e6 / (365.25 * flow.SECONDS_PER_DAY)
    return out


def build_tables():
    d = flow.load_daily()
    rain = pd.read_csv(RAIN_FILE, index_col=0, parse_dates=True)
    ann = annual(d, rain)
    windows = (BASE, (FIRST, int(ann.loc[ann["complete"], "year"].max())))
    t = {"segments": segments(d), "annual": ann, "water_year": annual(d, rain, wy=True),
         "seasons": seasons(d, rain), "months": months(d, rain), "days": days(d, rain),
         "average_year": average_year(d), "dependable": dependable_table(d, windows),
         "flow_duration": flow_duration_table(d, windows), "trends": trends(d, rain)}
    t["average_year_wide"] = average_year_wide(t["average_year"], t["months"])
    t["decades"] = decades(ann, t["seasons"])
    t["rain_runoff"] = rain_runoff(ann, rain)
    t["scenario"] = scenario(rain)
    t["thesis_years"] = thesis_years(rain)
    t["satellite_width"] = width_table(d)
    t["gauge_check"] = flow.chaubari_check(d["ramganga"])
    t["yield_estimates"] = yield_estimates(ann, rain, t["scenario"])
    t["dry_years"] = dry_years(d, ann)
    return t


def write_csvs(t):
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in t.items():
        df.to_csv(CSV_DIR / f"{name}.csv", index=False, lineterminator="\n", float_format="%.6g",
                  date_format="%Y-%m-%d")
    width = t["satellite_width"].drop(columns=["mouth_flow_m3s"])
    width.to_csv(flow.WIDTH_FILE, index=False, lineterminator="\n", date_format="%Y-%m-%d")


def main():
    t = build_tables()
    write_csvs(t)
    print(f"wrote {len(t)} tables to {CSV_DIR.relative_to(ROOT)}, and {flow.WIDTH_FILE.relative_to(ROOT)}",
          flush=True)
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("openpyxl is not installed: no workbook. Run with `uv run --with openpyxl python ...`.", flush=True)
        return
    from workbook import write_workbook
    write_workbook(t, BOOK)
    print(f"wrote {BOOK.relative_to(ROOT)} ({BOOK.stat().st_size / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    main()
