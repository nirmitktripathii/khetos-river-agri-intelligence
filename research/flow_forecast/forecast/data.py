"""Load the repository's bundled daily series into one table. Nothing here touches the network."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
FLOW_FILE = ROOT / "data" / "geoglows_daily.csv.gz"
WEATHER_FILE = ROOT / "research" / "river_flow" / "inputs" / "era5_daily.csv.gz"
SOIL_FILE = Path(__file__).resolve().parents[1] / "inputs" / "era5_soil_daily.csv.gz"
SOIL_LAYERS = ("sm_0_7", "sm_7_28", "sm_28_100")

# GEOGLOWS segment ids (see src/flow.py SEGMENTS)
SEGMENT_IDS = {"above": "440988347", "entering": "441010366", "below": "441068162", "mouth": "441161738",
               "ramganga": "441266307"}
UPSTREAM = {"mouth": ("above", "below"), "below": ("above", "entering"), "entering": ("above",), "above": (),
            "ramganga": ()}
WARMUP_YEARS = (1940, 1941)  # left out, as everywhere else in the project


def load(segment="mouth", soil=True, upstream=True):
    """One row per day: `flow` (m³/s, modelled), `rain_mm` and `et0_mm` (area-weighted ERA5 catchment means),
    optionally the three ERA5 soil-moisture layers (m³/m³, `sm_*`) and the modelled flow of the Nakatiya points
    upstream (`up_<key>`, m³/s).

    Rain and evapotranspiration are only available from 1940-01-02, and the flow ends a day after the weather;
    the intersection is returned, with the two warm-up years dropped."""
    flows = pd.read_csv(FLOW_FILE, index_col=0, parse_dates=True)
    parts = [flows[SEGMENT_IDS[segment]].rename("flow"),
             pd.read_csv(WEATHER_FILE, index_col=0, parse_dates=True)[["rain_mm", "et0_mm"]]]
    if soil:
        parts.append(pd.read_csv(SOIL_FILE, index_col=0, parse_dates=True)[list(SOIL_LAYERS)])
    if upstream:
        parts += [flows[SEGMENT_IDS[k]].rename(f"up_{k}") for k in UPSTREAM[segment]]
    df = pd.concat(parts, axis=1, join="inner").astype("float64")
    df = df[~df.index.year.isin(WARMUP_YEARS)]
    if df.isna().any().any():
        raise ValueError("gaps in the input series")
    return df
