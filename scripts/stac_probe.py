"""Live check of the Planetary Computer STAC searches KhetOS depends on (needs network access).

    python scripts/stac_probe.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import DEFAULT_AOI  # noqa: E402
from src.stac import search_landsat, search_s1, search_s2  # noqa: E402

lon, lat = DEFAULT_AOI
bbox = (lon - 0.015, lat - 0.015, lon + 0.015, lat + 0.015)
end = datetime.now(timezone.utc)
start = end - timedelta(days=90)

searches = {
    "Sentinel-2 L2A": lambda: search_s2(bbox, start, end, max_tile_cloud=50, max_items=5),
    "Sentinel-1 RTC": lambda: search_s1(bbox, start, end, max_items=5),
    "Landsat C2 L2 (last rabi season)": lambda: search_landsat(bbox, datetime(end.year - 1, 12, 1, tzinfo=timezone.utc),
                                                               datetime(end.year, 4, 1, tzinfo=timezone.utc),
                                                               max_items=5),
}
for name, search in searches.items():
    items = search()
    print(f"{name}: {len(items)} item(s)")
    for x in items:
        print(f"  {x.id}  {x.datetime:%Y-%m-%d}  cloud={x.properties.get('eo:cloud_cover')}  "
              f"assets={sorted(x.assets)[:8]}")
