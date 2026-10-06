"""The Nakatiya's watershed (catchment) boundary: the observatory's natural outer edge.

Every drop of rain that falls inside the line drains to the Nakatiya; rain outside it drains elsewhere. Two
polygons are written to data/nakatiya_watershed.geojson:

    whole_river   drains to the main stem 1.5 km above the Ramganga confluence (km 71.4 of 72.9)
    khajuria      drains to the river at Saidpur Khajuria (km 34), the reach just before the city

Delineated on MERIT-Hydro (90 m, Yamazaki et al. 2019) by the Global Watersheds API (Heberger,
https://mghydro.com/watersheds/), free and open. The outlet for the whole river sits 1.5 km above the
confluence because a point nearer the Ramganga snaps to the Ramganga itself (18,800 km2).

Caveats: on flat plains the divide is uncertain. Roads, canals, rail embankments and city drains move water
across it, and a 90 m terrain model cannot see them, so treat the line as good to a few hundred metres and the
area to about 15 %. GEOGLOWS's own river network (TDX-Hydro) gives 371.5 km2 for the whole river against
MERIT's 444 km2.

    .venv/Scripts/python.exe research/nakatiya_observatory/watershed.py
"""
import json
import sys
import time
from pathlib import Path

import requests
from pyproj import Geod
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src import river  # noqa: E402

OUT = ROOT / "data" / "nakatiya_watershed.geojson"
API = "https://mghydro.com/app/watershed_api"
OUTLETS = {  # name: (lon, lat, river km from the mapped head, description)
    "whole_river": (79.4873, 28.1469, 71.4, "Whole Nakatiya, 1.5 km above the Ramganga confluence"),
    "khajuria": (79.4711, 28.3380, 34.0, "Nakatiya at Saidpur Khajuria, just before the city"),
}
GEOD = Geod(ellps="WGS84")


def delineate(lon, lat, tries=3):
    for k in range(tries):
        try:
            r = requests.get(API, params={"lat": lat, "lng": lon, "precision": "high", "simplify": "false"},
                             timeout=300)
            r.raise_for_status()
            g = r.json()
            return shape((g["features"][0] if "features" in g else g)["geometry"])
        except Exception as exc:  # the free API is occasionally slow
            if k == tries - 1:
                raise
            print(f"  retry: {type(exc).__name__}", flush=True)
            time.sleep(15 * (k + 1))


def main():
    stem = river.river_lines(river.load_nakatiya(), river.MAIN_STEM_WAYS)
    feats = []
    for name, (lon, lat, km, desc) in OUTLETS.items():
        poly = delineate(lon, lat)
        area = abs(GEOD.geometry_area_perimeter(poly)[0]) / 1e6
        inside = stem.intersection(poly)
        stem_share = GEOD.geometry_length(inside) / GEOD.geometry_length(stem)
        print(f"{name}: {area:.1f} km2, bounds {tuple(round(b, 3) for b in poly.bounds)}, "
              f"{100 * stem_share:.0f}% of the mapped main stem inside", flush=True)
        feats.append({"type": "Feature", "geometry": mapping(poly), "properties": {
            "name": name, "description": desc, "outlet_lon": lon, "outlet_lat": lat, "outlet_river_km": km,
            "area_km2": round(area, 1), "source": "MERIT-Hydro 90 m via the Global Watersheds API (mghydro.com)",
            "retrieved": time.strftime("%Y-%m-%d")}})
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
