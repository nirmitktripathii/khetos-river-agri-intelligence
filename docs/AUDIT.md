# KhetOS audit — 27 September 2026

This audit checks the code against the project brief and records what was fixed and verified, plus what is still open.
Every page and every Ask-the-Map action was run end to end on live data. The offline tests (`pytest`) cover the pure logic.

## 1. Requirements traceability

| # | Requirement (from the brief) | Where | Status | Evidence |
|---|---|---|---|---|
| 1 | Free and open source; runs on weak hardware and Streamlit Community Cloud | `requirements.txt`, `runtime.txt`, `Dockerfile` | ✅ | Pure pip wheels: rasterio, pyproj and shapely bundle GDAL, PROJ and GEOS. No API keys and no Earth Engine. Analyses are scoped to an area of interest and capped at 1024 px a side (`eo.make_grid`). |
| 2 | Rohilkhand Overview | `app.py: page_overview` | ✅ | Five geoBoundaries districts are bundled. The Nakatiya and the Qila candidate are drawn on the map. |
| 3 | Change Radar | `analytics.change_signal` | ✅ | Robust z-score against the area's own earlier scene-to-scene changes, with a magnitude fallback when history is short. |
| 4 | Field Scanner and AI Field Brief | `analytics.field_scan`, `ai.field_brief` | ✅ | The brief is built only from measured values. It has no language model, so it cannot invent numbers. |
| 5 | Water / moisture signals | `analytics.water_stress_signal` | ✅ | NDMI canopy dryness is blended with the 14-day ET0 − rain balance from Open-Meteo. It is an inspection signal, not an irrigation prescription. |
| 6 | SAR + optical fusion | `analytics.fusion_assessment` | ✅ | Sentinel-2 NDVI and Sentinel-1 VH, compared only between passes on the same relative orbit. Results are AGREE, DISAGREE or INCOMPLETE. |
| 7 | Scouting queue (Isolation Forest) | `analytics.make_scouting_grid` | ✅ | Isolation Forest plus robust z-scores on a 400 m cell grid. Non-cropland cells are excluded using Impact Observatory. |
| 8 | Ask the Map (combined queries) | `ai.parse_question` → dispatch in `app.py` | ✅ | 14 actions: fields near the river, flood, course, alerts, riparian vegetation, buffer ladder, timeline, change, Qila, water, scouting, fusion, change radar and field. Every example question is covered by a test. |
| 9 | Nakatiya: encroachment over the years and water footprint | `river.river_land_change`, `river.river_timeline` | ✅ | Built-up change comes from WSF Evolution (1985–2015) and Impact Observatory (2017–2025). Water and vegetation come from harmonised Landsat, 1990–2026. |
| 10 | Buffers of 10/25/50/100 m | `river.river_buffer_ladder` | ✅ (with a caveat) | The 30 m reference line is added. Buffers under 45 m (1.5 Landsat pixels) get Landsat shares but no Landsat flags, because they sit inside typical OSM centreline error. |
| 11 | Built-up expansion | `river._assess` | ✅ | Validated products only. Landsat "never green" is too weak for this (see §3). |
| 12 | Riparian vegetation health | `river.riparian_health` | ✅ | Compares matched 1 Jan–31 Mar Sentinel-2 windows and flags a change of 10 pp or more. |
| 13 | River-side construction alerts | `river.construction_alerts` | ✅ | Flags a patch that was green a year earlier, is never green now and is not wet (a SWIR check). Labels: "BARE / BUILT CANDIDATE" and "NEW WATER / WET". |
| 14 | Time slider, 1984→2026 | River Observatory timeline | ✅ (from 1990) | No Collection-2 scene of any tier covers the 1985–89 rabi seasons over the Nakatiya. Earlier years are clamped, with a note saying so. |
| 15 | Flood and water-expansion watch | `river.corridor_water_watch` | ✅ | Sentinel-1 VV < −18 dB counts as water-like, compared with the same orbit about a cycle earlier. JRC Global Surface Water provides the history. |
| 16 | Wording: change flags, never legal determinations | `config.DISCLAIMER`, reports | ✅ | Appended to every report. Labels read "WATCH / HIGH / STABLE" and "candidate". |
| 17 | Downloadable report | `reports.build_markdown_report` | ✅ | Markdown with attributions and the disclaimer. |
| 18 | Map the Nakatiya from origin to the Ramganga | `river.nakatiya_course`, `scripts/river_probe.py` | ⚠ partial | See §4. |
| 19 | Qila / Kila river | `data/qila_candidate_osm.geojson`, the `qila` action | ⚠ candidate | Only a candidate OSM geometry exists. There is no confirmed, named Qila line in OSM for the reported 112 km course. |

## 2. Defects found and fixed

- **Windows GDAL deadlock.** On Windows, a thread that exits while holding GDAL state deadlocks later thread starts, and Streamlit's script thread exits on every rerun.
  - Fix: all raster I/O now runs on persistent daemon pools (`eo._IO_POOL`, `_TASK_POOL`, `JOB_POOL`, `_ANALYSIS_POOL`), and every analysis goes through `eo.run_analysis`.
  - Nested fan-out runs inline when the caller already owns the pool, so pools never wait on themselves (tested).
- **Sentinel-2 offset.** Processing baseline 04.00 and later carries a −1000 DN offset. Without the fix, NDVI and NDMI after January 2022 were biased. It is now applied per item (`s2_boa_offset`).
- **Landsat sensor mixing.** TM and ETM+ are now mapped onto OLI with the Roy et al. (2016) coefficients, and Landsat 7 SLC-off scenes are ranked last.
- **One bad scene sank a whole analysis.** Per-scene readers are now wrapped in `eo._safe`. A transient `RasterioIOError` was reproduced during calibration.
- **Single-season Landsat noise was raising false flags.** Comparisons now use the median of up to 3 seasons at each end, need at least 2 peak scenes, and flag vegetation only at 15 pp or more (§3).
- **UI:**
  - the page radio did not honour query parameters;
  - the built-up overlay was invisible;
  - metrics were truncated;
  - a timeline label appeared conditionally;
  - a CORS warning appeared.

  All five are fixed.
- **Packaging:**
  - requirements are pinned to verified ranges (Streamlit ≥ 1.64 is needed for the widget APIs used);
  - the Dockerfile needs no apt GDAL and runs as a non-root user with a healthcheck;
  - `.dockerignore` is added;
  - `run.ps1` is idempotent and avoids uv's `.exe` trampolines;
  - the probe scripts are rewritten against the current API.

## 3. Calibration evidence (`scripts/calibration/`)

| Question | Result | Consequence |
|---|---|---|
| How much does a single Landsat season move year to year? | Median 8–10 pp, up to 48 pp | Compare 3-season medians instead. |
| And 3-season medians with ≥2 peak scenes, 3–5 years apart? | ≤ 13.9 pp on the upper reach | Vegetation threshold set at 15 pp. |
| Can Landsat "never green" stand in for built-up land? Checked against Impact Observatory, rerun 2026-09-27 | Urban: precision 0.83–0.95, recall 0.48–0.60. Rural reaches: recall 0.01–0.20 | No. Built-up comes from WSF Evolution and Impact Observatory. |
| Sentinel-2 riparian windows, year on year | Jan–Mar ≤ 4.1 pp; Apr–Jun 7–12 pp; Jul–Sep up to 18.3 pp | Use Jan–Mar only, flagged at 10 pp. |

Built-up and water flags trigger at 3 pp or more. The long-run vegetation trend uses Theil–Sen with a Mann–Kendall p-value, so a single outlying season barely moves it.

## 4. Nakatiya course: status

- **Mapped:** 8 OSM ways, 85.1 km in total. The main stem is 72.9 km, from its head east of Bhojipura (79.5204 E, 28.4921 N) to the Ramganga confluence (79.4837 E, 28.1354 N). Side channels add 12.2 km.
  - A live Overpass run on 27 Sep 2026 matched the bundled extract exactly.
- **Reported source:** Dehnagar (Deenagar) village, Baheri area (Amar Ujala, 31 Aug 2026). This is upstream of the mapped head and not verified.
  - OSM maps no Nakatiya north of the head. The nearest lead is unnamed way 488077884, about 0.5 km north.
  - Closing the gap needs field GPS, or tracing on Sentinel-2 / SRTM flow accumulation. The latter is proposed in SOLUTION_DESIGN.md.
- **30 m green belt:** reported in the press for the Nakatiya and the Kila. It is not yet confirmed in the BDA Revised Master Plan 2031; a search on 27 Sep 2026 found the plan listing but not the clause. The line is shown only as a reference buffer.

## 5. Known limits and benign warnings

- Buffers are measured from the OSM centreline, not from surveyed banks.
- Impact Observatory stops at 2025 and WSF at 2015, so 2015→2017 and 2025→now are reported as uncovered gaps.
- GDAL "Request … failed with response_code=206" is a short multi-range read that GDAL retries. It is benign.
- The "'Memory' driver is deprecated since GDAL 3.11" message comes from inside `rasterio.features.shapes`. It is benign.

## 6. Dead code (flagged, not deleted)

- `src/data.py`: a 7-line `overview_cards` stub that nothing imports.
- `app/`: an empty directory.

## 7. Verification

- `python scripts/smoke_test.py`: 22 files parse and 12 modules import.
- `python -m pytest -q`: 53 passed, offline.
- `python scripts/stac_probe.py` and `python scripts/river_probe.py --live`: OK against live services.
- A headless AppTest rendered all 9 pages with no exceptions. Every Ask action was run on live data.
