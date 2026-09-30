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

The River Water Watch page reads a bundled GEOGLOWS snapshot through `src/flow.py` (tables, trends, float-method discharge and the field-log reader are pure functions, unit-tested in `tests/test_flow.py`); only the 15-day forecast is fetched live. Field readings stay in the Streamlit session.

The application is deliberately stateless apart from Streamlit caching. There is no database requirement for v0.1. Later, field observations can be stored as GeoParquet/Parquet and eventually PostGIS.

## Threading

All GDAL reads run on persistent daemon thread pools in `src/eo.py` (see `docs/SOLUTION_DESIGN.md`). On Windows a thread that exits holding GDAL state deadlocks later thread starts, and Streamlit's script thread exits on every rerun, so no analysis reads rasters on the script thread.

Remote reads are also capped per process by `eo.MAX_REMOTE_READS` (default 8, set with `KHETOS_MAX_REMOTE_READS`). A read that fails is retried as a whole, with backoff, a fresh URL and a re-signed token when needed. A scene that still fails is left out and recorded in the result's `skipped` list; the UI and reports show that list, and such results are not cached.
