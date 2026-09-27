"""Summarise the bundled Nakatiya geometry and, with --live, compare it with a fresh Overpass extract.

    python scripts/river_probe.py [--live]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import river as rv  # noqa: E402


def show(label, fc):
    c = rv.nakatiya_course(fc)
    print(f"{label}: {c['ways']} OSM ways, {c['total_km']} km in all, retrieved {c['retrieved']} from {c['source']}")
    if "main_stem_km" in c:
        print(f"  main stem {c['main_stem_km']} km from head {c['head']} to the Ramganga confluence "
              f"{c['confluence']}; side channels {c['branches_km']} km")
    else:
        print("  main stem ways are not one continuous line in this extract")
    return c


show("Bundled extract", rv.load_nakatiya())
print(f"  reported source: {rv.REPORTED_SOURCE} (upstream of the mapped head, unverified)")
if "--live" in sys.argv:
    live = rv.fetch_nakatiya_osm()
    show("Live Overpass", live)
    bundled = {f["properties"]["osm_way"] for f in rv.load_nakatiya()["features"]}
    fresh = {f["properties"]["osm_way"] for f in live["features"]}
    print(f"  ways only in the live extract: {sorted(fresh - bundled) or 'none'}; "
          f"only in the bundled one: {sorted(bundled - fresh) or 'none'}")
