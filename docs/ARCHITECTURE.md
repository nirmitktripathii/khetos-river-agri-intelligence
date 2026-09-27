# Architecture

```text
                   Streamlit UI
                        |
     +------------------+-------------------+
     |                  |                   |
  Field Scanner     River Observatory   Ask the Map
     |                  |                   |
     +------------------+-------------------+
                        |
                 KhetOS analytics
                        |
       +----------------+----------------+
       |                |                |
 Sentinel-2         Sentinel-1        Landsat
 optical             radar            history
       |                |                |
       +----------------+----------------+
                        |
                     Weather
                    Open-Meteo
                        |
                  feature engine
                        |
       +----------------+----------------+
       |                                 |
 robust/IsolationForest             river land-change
       anomalies                     metrics
       |                                 |
       +----------------+----------------+
                        |
                    map + report
```

The application is deliberately stateless apart from Streamlit caching. There is no database requirement for v0.1. Later, field observations can be stored as GeoParquet/Parquet and eventually PostGIS.

## Threading

All GDAL reads run on persistent daemon thread pools in `src/eo.py` (see `docs/SOLUTION_DESIGN.md`). On Windows a thread that exits holding GDAL state deadlocks later thread starts, and Streamlit's script thread exits on every rerun, so no analysis reads rasters on the script thread.
