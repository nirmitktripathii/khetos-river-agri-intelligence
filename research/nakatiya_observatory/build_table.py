"""One row per year: Khajuriya ghat flow in January, May and September; May permanent vegetation in the watershed;
Baheri rain in June-October. Run after pull_inputs.py, pull_imd.py and may_vegetation.py.

Writes data/nakatiya_yearly_observations.csv and data/nakatiya_yearly_observations.xlsx (with a notes sheet).
The workbook needs openpyxl, which the app's venv does not carry:

    uv run --with pandas --with openpyxl python research/nakatiya_observatory/build_table.py
"""
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "data"
OUT_CSV = DATA / "nakatiya_yearly_observations.csv"
OUT_XLSX = DATA / "nakatiya_yearly_observations.xlsx"
FLOW_MONTHS = {1: "jan", 5: "may", 9: "sep"}
RAIN_MONTHS = {6: "jun", 7: "jul", 8: "aug", 9: "sep", 10: "oct"}
SECONDS_PER_DAY = 86400

NOTES = [
    ("What", "One row per calendar year. Blank = not available (before the source starts, or month not over yet)."),
    ("Flow at Khajuriya", "GEOGLOWS v2 modelled daily flow, river segment 441105311: the Nakatiya at Khajuriya "
     "ghat by the Pilibhit bypass (28.3621 N, 79.4754 E), river km 29.4 from the mapped head, just before the "
     "city; the segment runs km 27-29 and its catchment is about 148 km2 (estimated). MODELLED, NOT "
     "MEASURED: ERA5 rain-runoff routed down the river network; it knows nothing of the city, canals, pumping, "
     "sewage or seepage, and runs 1.6-1.9 times above the real Ramganga at Chaubari. Use it for year-to-year "
     "climate swings, not as the river's real volume. 1940-41 are real ERA5 drought years."),
    ("Flow columns", "*_mean_m3s = mean flow over the month (cubic metres per second); *_volume_mcm = total water "
     "passing in the month (million cubic metres; 1 million m3 = 100 crore litres)."),
    ("Where is Khajuriya ghat?", "Located by the user near the Pilibhit bypass road (Suncity Vistar), 186 m from "
     "the mapped channel at river km 29.4. Saidpur Khajuria, about 4.6 km downstream (km 34), is a different "
     "place."),
    ("Permanent vegetation", "Landsat 5/7/8/9 (30 m) over the whole Nakatiya watershed (444 km2). A pixel is "
     "permanent vegetation if its greenness stands out (NDVI at least 0.10 above the watershed's median) both in "
     "May and in the November before (10 Nov-10 Dec). That removes mentha and summer vegetables but keeps "
     "sugarcane, a 10-12 month crop, with the trees and groves. A fixed NDVI threshold (green_both_abs_pct, NDVI >= "
     "0.40 in both) is kept for reference but jumps with the 2013 change to Landsat 8 and with haze. About half of "
     "the permanent pixels are ESA WorldCover trees (worldcover_precision); WorldCover's 2020 and 2021 maps use "
     "different algorithms, so their difference is not a change on the ground. Years with 1 look or under 80 % "
     "of the watershed seen are less reliable (see the *_looks and *_seen_pct columns). A green May (2023, 2026) "
     "still lifts it a little. No usable May + November pair exists before 1994, nor in 1997, 2002 and 2003; 2004 "
     "rests on Landsat 7 with its scan-line gaps. Read the trend over many years, not one year against the next."),
    ("Rain at Baheri (imd_*)", "India Meteorological Department gridded rainfall (0.25 degree, from 1901; "
     "Pai et al. 2014), built from daily gauge readings. 'Baheri area' = the mean of the 3 x 3 grid cells around "
     "the cell containing Baheri (28.75 N 79.5 E), about 80 km x 80 km: a single cell jumps when its nearby gauges "
     "come and go over the decades, a block of cells much less. imd_watershed_jun_oct_mm weights the three cells "
     "the watershed falls in by its area in each (28.25 N 51 %, 28.5 N 44 %, 28.75 N 5 %); it rests on single "
     "cells, so it shares their jumps (for example 2020, when the 28.25 N cell recorded 178 mm) - prefer "
     "imd_jun_oct_mm for comparing years. Early decades rest on fewer gauges. The grid for a year appears a few "
     "months after it ends, so the current year is blank."),
    ("Rain at Baheri (era5_*)", "ERA5 reanalysis (Open-Meteo, models=era5) for the 0.25-degree cell centred "
     "28.75 N 79.5 E, from 1940. A weather model's estimate, not a rain gauge: good for year-to-year swings and "
     "available to last week, but it can miss single storms and runs higher than IMD in the foothills. Baheri lies "
     "about 10 km north of the watershed's top edge (28.685 N)."),
    ("Watershed", "data/nakatiya_watershed.geojson: MERIT-Hydro 90 m delineation (Global Watersheds API). "
     "Whole river 444 km2; to Khajuriya ghat 200 km2. On flat plains the line is good to a few hundred metres; roads, "
     "canals and drains move water across it."),
    ("Licences", "GEOGLOWS CC BY 4.0; ERA5 via Open-Meteo CC BY 4.0; Landsat public domain (USGS); ESA WorldCover "
     "CC BY 4.0; MERIT-Hydro CC BY-NC 4.0 / ODbL; IMD gridded rainfall free for research and education "
     "(imdpune.gov.in)."),
]


def flow_table():
    q = pd.read_csv(HERE / "inputs" / "khajuria_flow_daily.csv.gz", index_col=0, parse_dates=True)["flow_m3s"]
    out = {}
    for m, name in FLOW_MONTHS.items():
        s = q[q.index.month == m]
        g = s.groupby(s.index.year)
        days = g.size()
        full = days == s.index.to_series().groupby(s.index.year).first().dt.days_in_month
        out[f"{name}_mean_m3s"] = g.mean().where(full)
        out[f"{name}_volume_mcm"] = (g.sum() * SECONDS_PER_DAY / 1e6).where(full)
    return pd.DataFrame(out)


def monthly_rain(r, prefix):
    """Rain totals for June-October of each year from a daily series; a month counts only when every day is there."""
    r = r.dropna()
    out = {}
    for m, name in RAIN_MONTHS.items():
        s = r[r.index.month == m]
        g = s.groupby(s.index.year)
        full = g.size() == s.index.to_series().groupby(s.index.year).first().dt.days_in_month
        out[f"{prefix}_{name}_mm"] = g.sum().where(full)
    t = pd.DataFrame(out)
    t[f"{prefix}_jun_oct_mm"] = t[[f"{prefix}_{n}_mm" for n in RAIN_MONTHS.values()]].sum(axis=1, min_count=5)
    return t


def rain_table():
    imd = pd.read_csv(HERE / "inputs" / "imd_rain_daily.csv.gz", index_col=0, parse_dates=True)
    era5 = pd.read_csv(HERE / "inputs" / "baheri_rain_daily.csv.gz", index_col=0, parse_dates=True)["rain_mm"]
    t = monthly_rain(imd["baheri_area_mm"], "imd")
    t["imd_watershed_jun_oct_mm"] = monthly_rain(imd["watershed_mm"], "w")["w_jun_oct_mm"]
    return t.join(monthly_rain(era5, "era5"), how="outer")


def main():
    t = flow_table().join(rain_table(), how="outer")
    veg_file = DATA / "nakatiya_may_vegetation.csv"
    if veg_file.exists():
        v = pd.read_csv(veg_file).set_index("year")
        keep = ["permanent_pct", "permanent_km2", "green_in_may_pct", "green_both_abs_pct", "may_median_ndvi",
                "nov_median_ndvi", "worldcover_trees_pct", "worldcover_precision", "may_looks", "nov_looks",
                "may_seen_pct", "both_seen_pct", "may_dates", "nov_dates", "platforms"]
        v = v[[c for c in keep if c in v]].add_prefix("veg_")
        t = t.join(v, how="outer")
    t.index.name = "year"
    t = t.round(3)
    t.to_csv(OUT_CSV, lineterminator="\n")
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as xw:
        t.to_excel(xw, sheet_name="Yearly")
        pd.DataFrame(NOTES, columns=["Item", "Note"]).to_excel(xw, sheet_name="Notes", index=False)
        ws = xw.sheets["Notes"]
        ws.column_dimensions["A"].width = 26
        ws.column_dimensions["B"].width = 140
        xw.sheets["Yearly"].freeze_panes = "B2"
    print(t.describe().T[["count", "mean", "min", "max"]].round(2).to_string())
    print("wrote", OUT_CSV, "and", OUT_XLSX)


if __name__ == "__main__":
    main()
