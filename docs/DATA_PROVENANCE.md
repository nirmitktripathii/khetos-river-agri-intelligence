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

## Regional administrative boundaries
geoBoundaries gbOpen IND ADM2 (ODbL 1.0), bundled as `data/rohilkhand_adm2.geojson`.

## Local context references
- Bareilly district official site: `https://bareilly.nic.in/map-of-district/`
- CPCB drain monitoring references for Nakatiya / Nakkatiya and Ramganga confluence.
- Wikimedia Commons Nakatiya River photograph under CC BY-SA 4.0.
