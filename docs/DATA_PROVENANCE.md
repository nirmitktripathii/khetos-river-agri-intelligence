# Data provenance

## Sentinel-2 L2A
Microsoft Planetary Computer STAC collection `sentinel-2-l2a`. Use cloud metadata for scene selection; the application signs remote assets with the Planetary Computer SDK.

## Sentinel-1 RTC
Microsoft Planetary Computer STAC collection `sentinel-1-rtc`.

## Landsat Collection 2 Level-2
Microsoft Planetary Computer STAC collection `landsat-c2-l2`.

## Weather
Open-Meteo API. The application uses current conditions and historical daily/hourly variables appropriate for the selected coordinate.

## River geometry
OpenStreetMap / Overpass API. The app requests waterway features whose names match Nakatiya/Nakatia/Naktiya inside a Bareilly-Rohilkhand bounding box. © OpenStreetMap contributors.

## Historical water
JRC Global Surface Water (Planetary Computer `jrc-gsw`): water transitions inside the corridor. No Google Earth Engine.

## Built-up change
- World Settlement Footprint Evolution v1 (DLR, CC BY 4.0), 1985-2015, 30 m: https://geoservice.dlr.de/web/datasets/wsf_evo
- Impact Observatory / Esri 10 m annual land cover (CC BY 4.0), 2017-2025: Planetary Computer `io-lulc-annual-v02` to 2023, Esri Living Atlas image service after.

## Modelled river flow
GEOGLOWS v2 (https://geoglows.ecmwf.int/api/v2/, CC BY 4.0): ECMWF ERA5 runoff routed on the TDX-Hydro river network, daily from 1940-01-01. The bundled snapshot is `data/geoglows_daily.csv.gz` with `data/geoglows_daily.json` (retrieval date, first and last day) for six segments (five on the Nakatiya, one on the Ramganga at Chaubari). The Khajuria ghat segment (441006241) was added on 2026-10-06 from the same model run, pulled separately; its contributing area is estimated. The public flows are not bias-corrected, and the model has no city, sewage, irrigation, canals, dams or aquifer. The first two years (1940-41) are left out of statistics as a precaution. The 15-day forecast is fetched live. `data/nakatiya_flow_history.xlsx` and `data/nakatiya_open_water_width.csv` are built by `research/river_flow/build_flow_history.py`.

## Nakatiya observatory: watershed and yearly record
- **Watershed** `data/nakatiya_watershed.geojson`: MERIT-Hydro 90 m flow directions (Yamazaki et al. 2019; CC BY-NC 4.0 / ODbL), delineated with the Global Watersheds API (https://mghydro.com/app/watershed_api) from the Ramganga confluence (444 km²) and from Khajuria ghat (235 km²). Built by `research/nakatiya_observatory/watershed.py`.
- **Yearly record** `data/nakatiya_yearly_observations.csv` and `.xlsx`, built by `research/nakatiya_observatory/build_table.py` from:
  - GEOGLOWS daily flow at Khajuria ghat (segment 441006241), 1940 onward;
  - India Meteorological Department 0.25° gridded daily rainfall (Pai et al. 2014, https://imdpune.gov.in), 1901 onward, for the 3 × 3 cells around Baheri and the three cells the watershed falls in (`pull_imd.py`); free for research and education;
  - ERA5 daily rain for the Baheri cell via Open-Meteo (CC BY 4.0), 1940 onward (`pull_inputs.py`);
  - Landsat Collection 2 Level-2 (USGS, public domain) May and November NDVI medians inside the watershed, searched from 1985 (usable from 1994), and ESA WorldCover 2020/2021 (CC BY 4.0) as a tree-cover check (`may_vegetation.py`, output `data/nakatiya_may_vegetation.csv`).

## Seasonal flow of the Ramganga at Chaubari (check only)
WWF-India / INRM, "Hydrological Modelling of the Ramganga River Basin", Appendix 1: SWAT model calibrated to the Central Water Commission gauge, 1973-2011. Entered by hand into `src/flow.py` (`CHAUBARI_SWAT`) and used only to compare with GEOGLOWS.

## Regional administrative boundaries
geoBoundaries gbOpen IND ADM2 (ODbL 1.0), bundled as `data/rohilkhand_adm2.geojson`.

## Local context references
- Bareilly district official site: `https://bareilly.nic.in/map-of-district/`
- CPCB drain monitoring references for Nakatiya / Nakkatiya and Ramganga confluence.
- Wikimedia Commons Nakatiya River photograph under CC BY-SA 4.0.
