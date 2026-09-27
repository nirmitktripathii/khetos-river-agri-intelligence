"""Folium map helpers. Add every layer first, then call `add_layer_control` once, last."""
import copy

import branca.colormap as bcm
import folium
import numpy as np

from src.eo import to_web_mercator

ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ESRI_ATTR = "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community"
PRIORITY_COLORS = {"HIGH": "#C62828", "WATCH": "#EF6C00", "LOW": "#2E7D32",
                   "INSUFFICIENT DATA": "#9E9E9E", "NOT CROPLAND": "#CFD8DC"}
PATCH_COLORS = {"BARE / BUILT CANDIDATE": "#C62828", "NEW WATER / WET": "#1565C0"}
RDYLGN = ["#a50026", "#d73027", "#f46d43", "#fdae61", "#fee08b", "#ffffbf",
          "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"]  # ColorBrewer RdYlGn
BRBG = ["#8c510a", "#bf812d", "#dfc27d", "#f6e8c3", "#f5f5f5",
        "#c7eae5", "#80cdc1", "#35978f", "#01665e"]  # ColorBrewer BrBG: dry / loss brown, wet / gain teal


def base_map(bbox, satellite=False):
    """Map fitted to a lon/lat bbox with OpenStreetMap and Esri imagery base layers."""
    minx, miny, maxx, maxy = bbox
    m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=12, tiles=None,
                   control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=not satellite).add_to(m)
    folium.TileLayer(tiles=ESRI_TILES, attr=ESRI_ATTR, name="Esri satellite", show=satellite).add_to(m)
    m.fit_bounds([[miny, minx], [maxy, maxx]])
    return m


def _empty(geojson):
    """An empty FeatureCollection: nothing to draw, and folium's tooltips fail on it (they read the first feature)."""
    return geojson.get("type") == "FeatureCollection" and not geojson.get("features")


def add_geojson(m, geojson, name, color="#1565C0", weight=3, fill_opacity=0.05, show=True, dash=None,
                tooltip_fields=None, tooltip_aliases=None):
    if _empty(geojson):
        return
    style = {"color": color, "weight": weight, "fillColor": color, "fillOpacity": fill_opacity}
    if dash:
        style["dashArray"] = dash
    tooltip = (folium.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases or tooltip_fields)
               if tooltip_fields else name)
    folium.GeoJson(copy.deepcopy(geojson), name=name, show=show, style_function=lambda _: style,
                   tooltip=tooltip).add_to(m)


def add_categorized(m, fc, name, prop, colors, tooltip_fields, tooltip_aliases=None, weight=1, fill_opacity=0.45,
                    show=True):
    """Features coloured by a categorical property (grey when the value has no colour)."""
    if _empty(fc):
        return

    def style(feature):
        c = colors.get(feature["properties"].get(prop), "#9E9E9E")
        return {"color": c, "weight": weight, "fillColor": c, "fillOpacity": fill_opacity}

    folium.GeoJson(copy.deepcopy(fc), name=name, show=show, style_function=style,
                   tooltip=folium.GeoJsonTooltip(fields=tooltip_fields,
                                                 aliases=tooltip_aliases or tooltip_fields)).add_to(m)


def add_scouting_cells(m, fc, name="Scouting cells"):
    """Cells coloured by priority, with the reason in the tooltip."""
    add_categorized(m, fc, name, "priority", PRIORITY_COLORS, ["cell", "priority", "reason"],
                    ["Cell", "Priority", "Why"])


def colorize(array, vmin, vmax, colors=RDYLGN):
    """RGBA image of `array` on a linear colour ramp; NaN pixels are transparent."""
    a = np.asarray(array, dtype="float32")
    finite = np.isfinite(a)
    t = np.clip((np.where(finite, a, vmin) - vmin) / (vmax - vmin), 0, 1)
    ramp = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in colors], dtype=float)
    pos = np.linspace(0, 1, len(colors))
    rgba = np.zeros(a.shape + (4,), dtype=np.uint8)
    for k in range(3):
        rgba[..., k] = np.interp(t, pos, ramp[:, k]).round().astype(np.uint8)
    rgba[..., 3] = np.where(finite, 255, 0)
    return rgba


def add_index_overlay(m, array, grid, name, vmin=-0.1, vmax=0.9, colors=RDYLGN, opacity=0.75, show=True,
                      legend=True):
    """Overlay a gridded index (e.g. NDVI) reprojected to Web Mercator, with a colour legend."""
    merc, bounds = to_web_mercator(array, grid)
    folium.raster_layers.ImageOverlay(colorize(merc, vmin, vmax, colors), bounds=[list(bounds[0]), list(bounds[1])],
                                      name=name, opacity=opacity, show=show, pixelated=True).add_to(m)
    if legend:
        bcm.LinearColormap(colors, vmin=vmin, vmax=vmax, caption=name).add_to(m)


def add_marker(m, lat, lon, label, popup=None, color="blue", icon="info-sign", group=None):
    folium.Marker([lat, lon], tooltip=label, popup=popup or label,
                  icon=folium.Icon(color=color, icon=icon)).add_to(group or m)


def add_bbox(m, bbox, name="Area of interest", color="#6A1B9A"):
    minx, miny, maxx, maxy = bbox
    fg = folium.FeatureGroup(name=name)
    folium.Rectangle([[miny, minx], [maxy, maxx]], color=color, weight=2, fill=False, tooltip=name).add_to(fg)
    fg.add_to(m)


def add_layer_control(m):
    folium.LayerControl(collapsed=True).add_to(m)
