"""Load the repository's bundled daily series into one table. Nothing here touches the network."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
FLOW_FILE = ROOT / "data" / "geoglows_daily.csv.gz"
WEATHER_FILE = ROOT / "research" / "river_flow" / "inputs" / "era5_daily.csv.gz"

# GEOGLOWS segment ids (see src/flow.py SEGMENTS)
SEGMENT_IDS = {"above": "440988347", "entering": "441010366", "below": "441068162", "mouth": "441161738",
               "ramganga": "441266307"}
WARMUP_YEARS = (1940, 1941)  # left out, as everywhere else in the project


def load(segment="mouth"):
    """One row per day: `flow` (m³/s, modelled), `rain_mm` and `et0_mm` (area-weighted ERA5 catchment means).

    Rain and evapotranspiration are only available from 1940-01-02, and the flow ends a day after the weather;
    the intersection is returned, with the two warm-up years dropped."""
    flow = pd.read_csv(FLOW_FILE, index_col=0, parse_dates=True)[SEGMENT_IDS[segment]].rename("flow")
    wx = pd.read_csv(WEATHER_FILE, index_col=0, parse_dates=True)[["rain_mm", "et0_mm"]]
    df = pd.concat([flow, wx], axis=1, join="inner").astype("float64")
    df = df[~df.index.year.isin(WARMUP_YEARS)]
    if df.isna().any().any():
        raise ValueError("gaps in the input series")
    return df
