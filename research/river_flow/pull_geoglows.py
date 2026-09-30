"""Refresh the GEOGLOWS v2 daily flow snapshot that the app and the flow-history workbook read.

For each segment in src.flow.SEGMENTS this pulls the whole retrospective series (1940 to about a week ago) and
writes data/geoglows_daily.csv.gz (date + one column per river ID, m³/s) and data/geoglows_daily.json (source,
licence, dates). Run from the repository root:

    python research/river_flow/pull_geoglows.py
"""
import gzip
import json
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import flow  # noqa: E402


def pull(river_id, tries=4):
    for k in range(tries):
        try:
            return flow.fetch_daily(river_id, timeout=180)
        except Exception as exc:
            if k == tries - 1:
                raise
            print(f"  retry {river_id}: {type(exc).__name__} {str(exc)[:100]}", flush=True)
            time.sleep(5 * (k + 1))


def main():
    series = {}
    for key, seg in flow.SEGMENTS.items():
        s = pull(seg["id"])
        series[str(seg["id"])] = s
        print(f"{key:9s} {seg['id']}  {s.index[0].date()} to {s.index[-1].date()}  {len(s)} days", flush=True)
    d = pd.DataFrame(series).dropna()  # keep the days every segment has
    d.index.name = "date"
    days = pd.date_range(d.index[0], d.index[-1], freq="D")
    if len(d) != len(days) or (d < 0).any().any():
        raise SystemExit(f"Refusing to write: {len(days) - len(d)} missing days or negative flows.")

    text = d.to_csv(lineterminator="\n", float_format="%.2f", date_format="%Y-%m-%d")
    with open(flow.DAILY_FILE, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(text.encode())  # mtime=0: the same data always gives the same bytes
    info = {"source": "GEOGLOWS v2 retrospective simulation: ECMWF ERA5 runoff routed on the TDX-Hydro river "
                      "network (daily mean flow, m3/s)",
            "api": flow.API, "licence": "CC BY 4.0", "retrieved": date.today().isoformat(),
            "first": d.index[0].date().isoformat(), "last": d.index[-1].date().isoformat(), "days": len(d),
            "segments": {str(seg["id"]): seg["name"] for seg in flow.SEGMENTS.values()}}
    flow.DAILY_META_FILE.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {flow.DAILY_FILE.name} ({flow.DAILY_FILE.stat().st_size / 1e3:.0f} kB), "
          f"{info['first']} to {info['last']}", flush=True)
    print(d.describe().T[["mean", "50%", "max"]].round(2).to_string(), flush=True)


if __name__ == "__main__":
    main()
