# Nakatiya flow forecast (scaffold)

Can we build an accurate ML / deep-learning / transformer model of the Nakatiya's flow? **The machinery, yes. The accuracy claim, not yet, and not from what we hold today.**

## The honest answer

1. **No one has measured the Nakatiya.** A forecast model learns from a target. Our only long target is GEOGLOWS (Group on Earth Observations Global Water Sustainability), itself a model driven by ERA5 rain and runoff. A network trained on it learns to *imitate GEOGLOWS*. Its scores measure how well it copies another model, never how well it predicts the river. The result can look excellent and mean nothing about the real stream.
2. **GEOGLOWS has no city, sewage, canals, pumping or aquifer**, which are exactly the things this project wants to study. A model trained on it cannot learn the construction effect, because the effect is not in its target.
3. **Accuracy comes from data we can get, in this order:**
   - *Real flow.* Your own float readings (River Water Watch, tab C) start a measured record. Even 20–30 readings over a year across seasons are worth more than 85 years of modelled flow for checking any model. The gauged neighbour is the **Ramganga at Chaubari** (Central Water Commission site CW1RAM000145); its daily flow is classified data, requested through the Central Water Commission's data desk (cdrc.cwc.gov.in). Ramganga models are a fair place to prove the method; the Nakatiya then needs its own readings.
   - *Real drivers.* India Meteorological Department gridded rain (`imdlib`, about 2 GB, **ask before downloading**), ERA5-Land soil moisture, Central Ground Water Board well levels, GRACE (Gravity Recovery and Climate Experiment) water storage, built-up fraction over time, and a flag for the 2024 sewage interception.
4. **Even then, expect modest skill.** One small catchment gives few independent floods. Published deep-learning successes pool hundreds of gauged basins (Kratzert et al. 2019, *Hydrology and Earth System Sciences* 23, 5089; CAMELS-IND, Mangukiya et al. 2025, *Earth System Science Data* 17, 461). Nearing et al. 2024 (*Nature* 627, 559) show skill holding up for extreme events in ungauged basins when trained that way. The route for the Nakatiya is therefore *pretrain on many gauged basins, then fine-tune on our readings*, not train on one river's modelled series.
5. **The forecast to beat** is GEOGLOWS's own 15-day ensemble forecast (fetched live on the River Water Watch page). A new model earns a place only if it beats that, plus persistence and climatology, on years it never saw.

## What is here

| Path | What |
|---|---|
| `forecast/data.py` | Loads the bundled GEOGLOWS flow (this point and the points upstream), ERA5 rain, evaporation and soil moisture (no network) |
| `pull_soil.py` | Pulls ERA5 soil moisture (three layers, three cells, from 1940; Open-Meteo, CC BY 4.0) into `inputs/era5_soil_daily.csv.gz` |
| `forecast/features.py` | Feature families (base, wetness, soil, upstream, and a labelled best-case "future rain") at forecast origin *t*; target is log(1 + flow) *h* days ahead. Tests prove the honest families see nothing after *t* |
| `forecast/splits.py` | Whole-year blocked splits with a gap, and rolling-origin walk-forward splits |
| `forecast/metrics.py` | NSE (Nash–Sutcliffe efficiency), log-NSE, KGE (Kling–Gupta efficiency), percent bias, pinball loss, band coverage |
| `forecast/baselines.py` | Persistence, day-of-year climatology, dry-weather recession |
| `forecast/gbm.py` | Gradient-boosted trees with 10/50/90 % quantiles |
| `forecast/nn.py` | LSTM (long short-term memory) and small transformer on 90-day windows, pinball loss. **Written, not yet run**: needs PyTorch |
| `run_compare.py` | Scores everything on test years 2016–2025 (validation 2011–2015, 90-day gaps) |
| `tests/` | Metric, split and no-leakage tests |

## Run

```bash
# from research/flow_forecast, using the app's environment (scikit-learn only)
../../.venv/Scripts/python.exe run_compare.py
../../.venv/Scripts/python.exe -m pytest -q tests

# neural models need PyTorch (a few hundred MB); separate environment, cache kept inside this folder
UV_CACHE_DIR=.uv-cache uv sync --extra nn --extra dev
UV_CACHE_DIR=.uv-cache uv run python run_compare.py --nn
```

## Results (Nakatiya at the Ramganga, modelled flow, test years 2016–2025)

These are *emulation* scores, for the reasons above. NSE (Nash–Sutcliffe efficiency): 1 is perfect, 0 is no better than the average.

| Horizon | Persistence | Climatology | Gradient boosting, base | + wetness | + soil moisture | + upstream flow | All honest | **Best case: known future rain** |
|---|---|---|---|---|---|---|---|---|
| 1 day | 0.56 | 0.14 | 0.66 | 0.65 | 0.66 | 0.65 | 0.66 | **0.85** |
| 3 days | −0.31 | 0.14 | 0.22 | 0.21 | 0.23 | 0.23 | 0.21 | **0.84** |
| 7 days | −0.46 | 0.14 | 0.16 | 0.15 | 0.16 | 0.16 | 0.15 | **0.86** |
| 14 days | −0.40 | 0.14 | 0.15 | 0.14 | 0.15 | 0.15 | 0.14 | **0.86** |

(Full table with log-NSE, KGE, bias, monsoon and dry-season scores and band coverage: `python run_compare.py`, written to `out/compare_mouth.csv`.)

What it says:

- **Soil moisture, recency-weighted rain and upstream flow add nothing** (every change is within ±0.02, which is noise). The flow of the last few days already carries what they know about how wet the catchment is, and the upstream points are too close to give warning: water reaches the mouth within the same day.
- **Knowing the rain to come lifts NSE from about 0.15 to 0.85 at 7–14 days.** Almost all the missing skill is future rain. Two cautions: GEOGLOWS is built from this very ERA5 rain, so the ceiling is higher than a real river would allow; and a real rain *forecast* is much less accurate than the rain that fell, so a real forecast will land well below it.
- Dry-season skill (log-NSE outside June–September) is already 0.89–0.98 without future rain; the gap is in the monsoon.
- The median forecast still carries about −30 % bias in flow terms beyond 3 days; a mean-targeted model would be needed for volumes.

**So the one lever left in public data is a rain forecast issued at the origin**: Open-Meteo's archive of past weather forecasts (about 2022 onward) lets us test that fairly.

## Rules for going further

- Never split at random; never scale inputs with statistics of the test years; keep future rain out of the features unless it comes from a *forecast* issued at the origin.
- Report every score for several splits (`splits.rolling_origin`), not one.
- Say "emulates GEOGLOWS" whenever the target is GEOGLOWS. Only measured flow supports "predicts the Nakatiya".
- Verify each paper against its source before citing it.
