# River flow research (Nakatiya)

Scripts that built the modelled flow history behind the **River Water Watch** page. Everything here is modelled, not measured: no gauge has ever recorded the Nakatiya.

## Rebuild

Run from the repository root. Each step is optional: the inputs are already in the repository.

```bash
# 1. Daily GEOGLOWS flows, five segments (network; CC BY 4.0) -> data/geoglows_daily.csv.gz + .json
python research/river_flow/pull_geoglows.py

# 2. Daily ERA5 rain and reference evapotranspiration, three 0.25-degree cells (Open-Meteo; CC BY 4.0)
#    -> research/river_flow/inputs/era5_daily.csv.gz
python research/river_flow/pull_rain.py

# 3. All tables, the width CSV and the Excel workbook (needs openpyxl, which the app itself does not)
uv run --with openpyxl python research/river_flow/build_flow_history.py
```

Outputs:

| File | What |
|---|---|
| `data/nakatiya_flow_history.xlsx` | 21 sheets, 6 charts; every derived cell is a live Excel formula where it can be |
| `data/nakatiya_open_water_width.csv` | open-water width of three reaches on clear Sentinel-2 dates, read by the app |
| `research/river_flow/out/csv/*.csv` | the same tables as plain CSV |

`workbook.py` writes the workbook, `build_flow_history.py` builds the tables, and `src/flow.py` holds the calculations shared with the app (so the page and the workbook cannot drift apart).

## Sub-pixel unmixing

`unmix_probe.py` splits each 10 m Sentinel-2 pixel along the river into water and land (reading ten Sentinel-2 bands, B02 to B12, with a water signature taken from the same date where it can be) and sums the water fractions into a width. `research/river_flow/unmix/` holds its outputs, and `inputs/s2_clear_scenes.csv` the list of clear scenes. Width is not flow. Noise is about ±0.5 m.

## Choices worth knowing

- Baseline 1991–2020 (the current 30-year normal); trends from 1985 (the start of the satellite record) and from 1950.
- The first two model years (1940–41) are left out of every statistic as a precaution (`flow.WARMUP_YEARS`). Both were drought years in ERA5 and nothing marks them as wrong; including them lowers the mean volume by about 2%.
- Catchment rain is the area-weighted mean of three ERA5 cells (0.10 north, 0.30 middle, 0.60 south). `models=era5` is set on purpose: Open-Meteo's default "best match" switches to a finer model in 2017 and would put a step in the series.
- Trends: Theil–Sen slope, Mann–Kendall test with a persistence adjustment (Yue and Wang, 2004). Wording comes from `flow.trend_reading`.
- The land-cover scenario applies the Soil Conservation Service curve-number method day by day as in Dr. S. S. Tripathi's thesis (2017), to 1991–2020 rain. It shows what a land-cover change *could* do to runoff; it is not a finding about the river.
- GEOGLOWS versus the WWF-India / INRM model at Chaubari (1973–2011): about 1.9–2.2× in the monsoon and 1.4–3.0× in the pre-monsoon. Do not scale the Nakatiya by one factor.
