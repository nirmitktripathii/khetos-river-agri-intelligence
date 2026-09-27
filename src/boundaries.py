"""Rohilkhand district boundaries, bundled with the app (geoBoundaries gbOpen IND ADM2, ODbL 1.0)."""
import json
from functools import lru_cache

from src.config import DATA_DIR

DISTRICTS_FILE = DATA_DIR / "rohilkhand_adm2.geojson"


@lru_cache(maxsize=1)
def load_districts():
    """FeatureCollection of the five Rohilkhand districts. Shared cached object: do not mutate."""
    with open(DISTRICTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def district(name):
    """One district feature by name (case-insensitive), or None."""
    return next((f for f in load_districts()["features"]
                 if f["properties"]["district"].lower() == name.lower()), None)
