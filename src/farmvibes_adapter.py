"""Optional integration seam for FarmVibes.AI.

The core Streamlit PoC intentionally does not require a FarmVibes cluster. When a
FarmVibes deployment exists, wrap its REST/Python client behind this module and
return the same normalized metric dictionaries consumed by the UI.
"""

import os


def farmvibes_enabled() -> bool:
    return bool(os.getenv("FARMVIBES_BASE_URL"))


def configuration():
    return {
        "enabled": farmvibes_enabled(),
        "base_url": os.getenv("FARMVIBES_BASE_URL", ""),
    }


def explain_extension_points():
    return [
        "NDVI / temporal aggregation workflow",
        "Sentinel-1 + Sentinel-2 fusion workflow",
        "crop/field segmentation workflow",
        "custom model training + inference workflow",
    ]
