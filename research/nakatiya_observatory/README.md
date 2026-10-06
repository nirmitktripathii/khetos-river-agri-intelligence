# Nakatiya observatory: boundary and yearly records

The observatory's outer edge is the Nakatiya's **watershed**, the line dividing land that drains to the Nakatiya
from land that drains elsewhere. Everything inside it (rain, fields, trees, buildings, drains) can change the river;
nothing outside it can, except through canals and pipes. Inside that boundary, the river corridor (for example the
30 m belt) is the zone for encroachment change flags.

| File | What |
|---|---|
| `watershed.py` → `data/nakatiya_watershed.geojson` | Watershed to 1.5 km above the Ramganga (444 km²) and to Khajuriya ghat (200 km²). MERIT-Hydro 90 m via the Global Watersheds API |
| `pull_inputs.py` → `inputs/` | Daily GEOGLOWS flow at Khajuriya ghat (segment 441105311) and ERA5 rain at Baheri, both 1940 onward |
| `pull_imd.py` → `inputs/imd_rain_daily.csv.gz` | IMD 0.25° gridded daily rain, 1901 onward: the three cells the watershed falls in, the 3 × 3 block around Baheri, and watershed-weighted rain. Downloads each year's national file (about 25 MB), keeps three numbers a day and deletes it; four years at a time, resumable (2–3 hours: the IMD server is slow and drops connections) |
| `may_vegetation.py` → `data/nakatiya_may_vegetation.csv` | Permanent vegetation in May, Landsat searched from 1985; the first usable May + November pair is 1994 (about an hour) |
| `build_table.py` → `data/nakatiya_yearly_observations.csv` / `.xlsx` | One row per year: flow in Jan/May/Sep, May vegetation, Jun–Oct rain; the workbook has a Notes sheet |

Run order: `watershed.py`, `pull_inputs.py`, `pull_imd.py`, `may_vegetation.py`, `build_table.py`.
`build_table.py` needs `uv run --no-project --with pandas --with openpyxl python ...` and `pull_imd.py`
`uv run --no-project --with imdlib --with shapely --with pyproj python -u ...`; the others run with
`.venv/Scripts/python.exe -u`. The app reads `data/nakatiya_yearly_observations.csv` (River Water Watch → Yearly
record) and the watershed (that tab and the course map).

## How far back each record goes

| Record | Source | From | Kind |
|---|---|---|---|
| Flow at Khajuriya ghat | GEOGLOWS v2 retrospective | 1940 | Modelled from ERA5 rain; no gauge exists on the Nakatiya |
| Rain at Baheri | ERA5 (Open-Meteo, `models=era5`) | 1940 | Weather-model reanalysis |
| Rain at Baheri, gauge-based | IMD 0.25° gridded rain (Pai et al. 2014) | 1901 | Gauges interpolated to a grid; early decades rest on fewer gauges |
| May vegetation | Landsat 5/7/8/9 Collection 2, Tier 1 | first clear May look (1980s–1990s) | Satellite, 30 m |
| Tree cover check | ESA WorldCover | 2020, 2021 | Satellite land-cover map, 10 m |

## Caveats

- **Khajuriya ghat** is by the Pilibhit bypass road (28.3621 N, 79.4754 E, located by the user), 186 m from the
  mapped channel at river km 29.4. It is not in OpenStreetMap under that name; Saidpur Khajuria, 4.6 km
  downstream, is a different place. Its model segment (441105311) has no published area here; about 148 km² is
  estimated from the flows above and below.
- **Flow is modelled.** GEOGLOWS knows nothing of the city, canals, pumping or seepage, and at Chaubari it runs
  1.6–1.9× the gauged Ramganga. Use it for year-to-year swings, not the river's real volume.
- **Permanent vegetation** = greenness that stands out (NDVI at least 0.10 above the watershed's median) in May
  *and* in the November before. That also counts sugarcane. A fixed threshold (NDVI ≥ 0.40, kept as
  `green_both_abs_pct`) was tried first and dropped as the headline: it gave 0.7–2.1 % in the Landsat 5/7 years
  and 2.1–24.8 % in the Landsat 8 years, because haze and the sensor move the whole scene's NDVI. The relative
  test gave 4.8–8.4 % and 3.5–11.5 % on the same years, with the same agreement with ESA WorldCover trees (about
  half of the flagged pixels). NDMI-based tests did no better. Read the trend over many years.
- **IMD rain, single cell vs block.** One 0.25° cell jumps when nearby gauges enter or leave the record, so
  "Baheri area" is the 3 × 3 block mean (about 80 × 80 km); the single Baheri cell and the watershed-weighted
  series are kept in `inputs/imd_rain_daily.csv.gz` for comparison. ERA5 runs higher than IMD here.
- **Baheri lies outside the watershed**, about 10 km north of its top edge at 28.685 N. If the reported source
  near Baheri (Dehnagar/Deenagar) is right, the 90 m terrain model misses the river's top reach, which is common
  on flat land cut by canals and roads. Only a ground check can settle it.
