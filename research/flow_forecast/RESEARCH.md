# Making the forecast better: data sources and model designs

Status 2026-10-06. What the tests so far established (see README.md):

- Soil moisture, recency-weighted rain and upstream flow add nothing on top of recent flow and rain.
- A real rain forecast (ECMWF, archived from 2024-03) lifts the 3-day score from 0.30 to 0.59 (Nash–Sutcliffe efficiency, NSE); little help at 7 days.
- The 10–90 % bands are too narrow once forecast rain is used (62–74 % of days inside). **Done:** conformal calibration (`run_calibrate.py`) widens them on 2024 and brings 2025–26 coverage to 81–86 %; the app shows the calibrated band.
- Everything is still scored against GEOGLOWS, a model, not the river.

So the two questions are: (1) what would make the *rain going in* better, and (2) what would make the *target* real. Model architecture comes third.

## 1. Data sources

| Source | What it adds | Cost / access | Verdict |
|---|---|---|---|
| **ECMWF rain forecasts** (Open-Meteo Previous Runs API) | Rain forecast at 1–7 days, archived from 2024-03-01 | Free, done (`pull_rain_forecast.py`) | In use. Rerun as the archive grows: each monsoon adds a real test |
| **ECMWF ensemble (51 members)** (Open-Meteo Ensemble API) | Spread of possible rain → honest uncertainty bands | Free; live only, no long archive checked | Use for the live forecast's band, when one exists |
| **IMD gridded rain**, 0.25°, 1901 onward, plus a provisional real-time product (`imdlib`) | Gauge-based rain; for the *real* river it is better than ERA5, which is a model | Free; about 2 GB for the full archive (**ask before downloading**) | High value once we have measured flow; little value while the target is GEOGLOWS (built on ERA5) |
| **GPM IMERG** (NASA satellite rain), Early run ~4 h, Late ~12 h latency | Near-real-time rain, so a live forecast need not wait a week for ERA5 | Free with a NASA Earthdata login (**the user registers**) | Needed for any live forecast |
| **GloFAS v4** (Copernicus flood model, 5 km, daily from 1979; also via Open-Meteo Flood API) | A second, calibrated modelled flow, and a 30-day forecast | Free | **Does not resolve the Nakatiya**: at 5 km the Nakatiya cells give 0.1–1.5 m³/s against GEOGLOWS's 2.6–3.6, and the mouth cell snaps to the Ramganga (213 m³/s). Useful for the Ramganga, not here |
| **CWC gauge, Ramganga at Chaubari** (site CW1RAM000145) | Real daily flow, since 1970 | Classified; request via cdrc.cwc.gov.in (**the user applies**) | The single most valuable dataset: a real target to prove the methods on |
| **Your float readings** (River Water Watch, tab C) | The only real Nakatiya flow | Free; field time | Even 20–30 readings across seasons let us measure the gap between GEOGLOWS and the river |
| **CGWB well levels** (India-WRIS, since 1969) | Groundwater depth near the river; the seepage part of the project's argument | Free; station-level, often four readings a year | Too sparse for daily forecasting; valuable for the long-term trend question |
| **GRACE / GRACE-FO** (JPL mascons RL06.3, monthly from 2002) | Total water storage change | Free (NASA Earthdata) | Cells ~300 km across: Ramganga-basin scale at best, not the Nakatiya |
| **Sentinel-1 radar** (10 m, every 6–12 days) | Water extent through monsoon cloud | Free | A width series in the monsoon, where Sentinel-2 fails; not a flow |

## 2. Model designs

| Design | Why | When |
|---|---|---|
| **Noise-trained perfect prognosis** | Train with the rain that fell *plus noise shaped like forecast error*, so the model learns that forecast rain is unreliable at long leads. Would fix the too-narrow bands at the source; conformal calibration already fixes them after the fact | Now: cheap. The noise size must come from outside the test period (published ECMWF skill, or the first forecast year held out) |
| **One model for all horizons** (horizon as an input) | Shares data across 1–7 days; smoother forecasts | Now: cheap |
| **Mean-targeted or bias-corrected output** | The median under-states volume; a mean target serves volume questions | Now: cheap |
| **LSTM** (long short-term memory network) | The standard deep-learning rainfall–runoff model (Kratzert et al. 2019). On one catchment it should only match the trees | After PyTorch is installed; `forecast/nn.py` is written |
| **Pretrain on many basins, fine-tune here** | How deep learning gets its skill: Caravan pools 6,830 catchments (Kratzert et al. 2023, *Scientific Data* 10); CAMELS-IND covers Indian basins (Mangukiya et al. 2025, *ESSD* 17, 461). Google's global model (Nearing et al. 2024, *Nature* 627, 559) works in ungauged basins this way | Once there is a real target (float readings or Chaubari) to fine-tune and test on |
| **Differentiable hybrid (δHBV)** | A conceptual water-balance model whose parameters a network learns. Feng et al. (2024, *Geoscientific Model Development*) found it competitive with LSTM over 3,753 basins and better in ungauged regions, but weaker in basins with heavy human impact, which the Nakatiya is | Research option; its water-balance stores could carry a seepage or urban-runoff term the project could test |
| **Transformers** | Mixed evidence: vanilla transformers trail LSTM on the CAMELS benchmark; the Temporal Fusion Transformer (recurrence plus attention) is reported to beat both (arXiv 2506.20831, 2025; not yet peer-reviewed) | Only after the LSTM, and only with many basins |

## 3. Recommended order

1. **Now, no new data:** noise-trained perfect prognosis, one model across horizons, mean target. Rerun the 2024–2026 test.
2. **Ask the user first:** IMD gridded rain (2 GB) and an IMERG login, to prepare for a live forecast and a real target.
3. **User actions that unlock real accuracy:** float readings; the CWC Chaubari request.
4. **Then:** LSTM, pretrained on CAMELS-IND/Caravan and fine-tuned on Chaubari, tested against measured flow; δHBV if a water-balance explanation is wanted.

## Sources

- GloFAS v4 reanalysis: https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical ; https://www.ecmwf.int/node/28201
- Open-Meteo Flood API (GloFAS), Previous Runs API and Ensemble API: https://open-meteo.com/en/docs
- IMD gridded rain and `imdlib`: https://imdlib.readthedocs.io/en/latest/Usage.html
- GPM IMERG latency: https://gpm.nasa.gov/data/IMERG
- JPL GRACE/GRACE-FO mascons RL06.3: https://podaac.jpl.nasa.gov/dataset/TELLUS_GRAC-GRFO_MASCON_GRID_RL06.3_V4
- CGWB groundwater levels on India-WRIS: https://indiawris.gov.in/wris/#/groundWater
- Caravan: https://research.google/pubs/caravan-a-global-community-dataset-for-large-sample-hydrology/
- δHBV global study: https://doaj.org/article/6f4c907569364399b4a8a656d9a9998e
- Temporal Fusion Transformer for runoff: https://arxiv.org/html/2506.20831v1
