# KhetOS River + Agriculture Intelligence PoC

A Streamlit-first, free/open-source proof of concept for Bareilly and the wider Rohilkhand landscape. The application combines real public Earth-observation and weather data with lightweight machine-learning and rule-based geospatial analytics.

## Modules

* 🌍 Rohilkhand Overview
* 🛰 Change Radar
* 🌱 Field Scanner
* 💧 Water / Moisture Signals
* 📡 SAR + Optical Fusion
* ⚠ Scouting Queue
* 🤖 Ask the Map
* 🌊 Nakatiya River Observatory
* 🏘 Land Change / Riparian Change
* 📄 Evidence report export

## Live data sources

1. **Microsoft Planetary Computer STAC**

   * Sentinel-2 L2A: optical multispectral imagery
   * Sentinel-1 RTC: radar backscatter
   * Landsat Collection 2 Level-2: longer historical context
   * STAC endpoint: `https://planetarycomputer.microsoft.com/api/stac/v1/`
2. **Open-Meteo**

   * current and historical weather context, including precipitation, temperature, ET0 and soil moisture variables.
3. **OpenStreetMap Overpass API**

   * default Nakatiya/Naktia river geometry retrieval.
4. **JRC Global Surface Water**: historic water transitions (Planetary Computer).
5. **World Settlement Footprint Evolution (DLR)** and **Impact Observatory 10 m land cover**: validated built-up change, 1985-2015 and 2017-2025.

See `docs/AUDIT.md` for the requirements audit and calibration evidence, and `docs/SOLUTION_DESIGN.md` for the design and roadmap.

## Important scientific limitation

This PoC detects **remote-sensing change signals**. It does not determine legal encroachment, ownership, pollution compliance, irrigation requirement, or final harvest yield without local validation data. River-side outputs are deliberately phrased as `change flags` or `encroachment-risk signals` that should be checked against cadastral records and field evidence.

## Why the first version avoids a full FarmVibes cluster

FarmVibes.AI is used here as the architectural/R\&D reference. The first public app runs a smaller direct-STAC pipeline so it can fit free Streamlit infrastructure. A FarmVibes adapter can be added later for more complex fused workflows, training and inference.

## Local run

Windows (creates `.venv` with Python 3.12, installs once, starts the app):

```powershell
.\run.ps1
```

Any OS:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt   # Windows: .venv\Scripts\python.exe
.venv/bin/python -m streamlit run app.py
```

Docker: `docker compose up --build`, then open http://localhost:8501.

Tests: `pip install -r requirements-dev.txt`, then `python -m pytest -q` (offline) and `python scripts/smoke_test.py`.
Live probes: `python scripts/stac_probe.py`, `python scripts/river_probe.py --live`.

## Streamlit Community Cloud

1. Push this repository to GitHub.
2. Go to `https://share.streamlit.io/`, create an app from the repository and select `app.py`.
3. Under **Advanced settings**, choose **Python 3.12**. Community Cloud sets the Python version there; `runtime.txt` is for other hosts.
4. No secret is required.

## Performance notes

* Analyses are AOI-scoped. The app intentionally avoids downloading district-wide raw imagery.
* Change Radar and Scouting Queue work on small analysis windows.
* Sentinel-2 uses a 10 m analysis target for fields and a coarser target where appropriate.
* River analysis is performed on a selected corridor/reach, not the entire 70+ km river at native resolution in one request.
* Results are cached by Streamlit.

## Nakatiya local context

Public sources identify the Nakatiya/Nakkatiya river/drain in Bareilly and its confluence with the Ramganga. The PoC fetches current geometry from OpenStreetMap where available and shows a CPCB-reported confluence/monitoring point as a reference marker. A Wikimedia image also records a photographed river location near Sadar Cantt-Thiriya Road.

## Model layer

The anomaly engine uses unsupervised `IsolationForest` plus robust z-score signals. This avoids pretending that a model trained elsewhere is a Bareilly yield model. A future Bareilly yield module should use locally measured fields and grouped spatial/temporal validation.

## License

Code in this repository is intended to be MIT-licensed. Third-party datasets remain under their own terms/licenses. See `docs/DATA\_PROVENANCE.md`.

