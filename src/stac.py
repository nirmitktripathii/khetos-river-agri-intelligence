import time
from datetime import datetime, timezone
import pystac_client
import planetary_computer
import requests
from src.config import STAC_URL


def get_catalog():
    cat = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace, timeout=60)
    # Planetary Computer supports CQL2 filtering and sorting but does not advertise them.
    for ext in ("ITEM_SEARCH", "FILTER", "SORT"):
        cat.add_conforms_to(ext)
    return cat


def _iso(d):
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat().replace("+00:00", "Z")


def search_items(collection, bbox, start, end, cql_filter=None, max_items=50, newest_first=True, attempts=3):
    """STAC search returning items newest-first (or oldest-first) by acquisition time.

    Retries dropped connections: the API occasionally truncates large result pages.
    """
    for attempt in range(attempts):
        try:
            search = get_catalog().search(
                collections=[collection],
                bbox=list(bbox),
                datetime=f"{_iso(start)}/{_iso(end)}",
                filter=cql_filter,
                filter_lang="cql2-json" if cql_filter else None,
                sortby=[{"field": "properties.datetime", "direction": "desc" if newest_first else "asc"}],
                max_items=max_items,
                limit=min(max_items, 100),
            )
            items = list(search.items())
            break
        except (pystac_client.exceptions.APIError, requests.RequestException):
            if attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))
    items.sort(key=lambda x: x.datetime or datetime.min.replace(tzinfo=timezone.utc), reverse=newest_first)
    return items


def search_s2(bbox, start, end, max_tile_cloud=95, max_items=60):
    # Tile-level cloud cover is only a coarse pre-filter; the AOI itself is screened with SCL later.
    f = {"op": "<=", "args": [{"property": "eo:cloud_cover"}, max_tile_cloud]}
    return search_items("sentinel-2-l2a", bbox, start, end, f, max_items)


def search_s1(bbox, start, end, relative_orbit=None, max_items=40):
    f = {"op": "=", "args": [{"property": "sat:relative_orbit"}, int(relative_orbit)]} if relative_orbit else None
    return search_items("sentinel-1-rtc", bbox, start, end, f, max_items)


def search_landsat(bbox, start, end, max_cloud=60, max_items=40):
    # Tier 1 only: best geometric/radiometric quality for multi-decade comparisons.
    f = {"op": "and", "args": [
        {"op": "<=", "args": [{"property": "eo:cloud_cover"}, max_cloud]},
        {"op": "=", "args": [{"property": "landsat:collection_category"}, "T1"]},
    ]}
    return search_items("landsat-c2-l2", bbox, start, end, f, max_items)


def resolve_asset(item, candidates):
    keys = {k.lower(): k for k in item.assets}
    for candidate in candidates:
        if candidate.lower() in keys:
            return item.assets[keys[candidate.lower()]]
    # Loose search for standard Planetary Computer aliases.
    for key, original in keys.items():
        for candidate in candidates:
            if key.endswith(candidate.lower()) or candidate.lower() in key:
                return item.assets[original]
    raise KeyError(f"None of {candidates} present in {item.id}; assets={list(item.assets)}")
