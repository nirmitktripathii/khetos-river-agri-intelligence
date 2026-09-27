# FarmVibes + God's Eye View bridge

## FarmVibes.AI

Use FarmVibes behind a service boundary rather than coupling the public Streamlit app to its cluster internals.

Suggested future interface:

```text
Streamlit UI
    |
    +--> khetos analytics API
             |
             +--> direct STAC path (default PoC)
             |
             +--> FarmVibes adapter (advanced workflows)
```

Candidate FarmVibes workflows for later integration include Sentinel-2 preprocessing, NDVI summaries, temporal aggregation, change/outlier detection, segmentation and trained-model inference.

## God's Eye View

The current app uses Folium/Leaflet because it is lightweight enough for Streamlit Community Cloud. The UI concepts to preserve when upgrading are:

- layer stack
- focus/track a target
- spatial annotation
- time navigation
- scene context
- AI command -> spatial action

A future MapLibre/Cesium client can replace only the presentation layer while keeping the KhetOS analytics APIs intact.
