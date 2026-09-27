"""Markdown evidence reports built from analysis result dictionaries."""
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.config import ATTRIBUTIONS, DISCLAIMER

# Geometry and raster payloads are map content, not report content.
SKIP_KEYS = {"corridor", "corridors", "geojson", "scene", "daily", "grid"}


def _label(key):
    return str(key).replace("_", " ")


def _fmt(v):
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    if isinstance(v, (float, np.floating)):
        if not math.isfinite(v):
            return "n/a"
        return f"{v:.3f}" if abs(v) < 10 else f"{v:,.1f}"
    if isinstance(v, (list, tuple)):
        return "; ".join(_fmt(x) for x in v) if v else "none"
    return str(v)


def _skip(value):
    return value is None or isinstance(value, np.ndarray) or hasattr(value, "__geo_interface__")


def build_markdown_report(title, data, notes=None):
    """Scalars become a bullet list, dicts become sub-sections, DataFrames become tables."""
    lines = [f"# KhetOS · {title}", "",
             f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by the KhetOS public-data proof of concept._",
             ""]
    bullets, sections = [], []
    for key, value in data.items():
        if key in SKIP_KEYS or _skip(value):
            continue
        if isinstance(value, pd.DataFrame):
            if not value.empty:
                sections += [f"## {_label(key).capitalize()}", "", value.to_markdown(index=False, floatfmt=".3f"), ""]
        elif isinstance(value, dict):
            items = [f"- **{_label(k)}**: {_fmt(v)}" for k, v in value.items()
                     if k not in SKIP_KEYS and not _skip(v) and not isinstance(v, (dict, pd.DataFrame))]
            if items:
                sections += [f"## {_label(key).capitalize()}", "", *items, ""]
        else:
            bullets.append(f"- **{_label(key)}**: {_fmt(value)}")
    if bullets:
        lines += ["## Summary", "", *bullets, ""]
    lines += sections
    if notes:
        lines += ["## Notes", "", *[f"- {n}" for n in notes], ""]
    lines += ["## Interpretation note", "", DISCLAIMER, "", "## Data sources", "",
              *[f"- **{k}**: {v}" for k, v in ATTRIBUTIONS.items()], ""]
    return "\n".join(lines)
