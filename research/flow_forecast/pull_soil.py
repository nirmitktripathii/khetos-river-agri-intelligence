"""Pull daily ERA5 soil moisture for the Nakatiya catchment (Open-Meteo archive, CC BY 4.0).

Same three ERA5 cells and catchment weights as research/river_flow/pull_rain.py, and `models=era5` for the same
reason (no step in 2017). Soil moisture is the volume of water per volume of soil (m³/m³) in three layers:
0-7 cm, 7-28 cm and 28-100 cm. Writes inputs/era5_soil_daily.csv.gz. Run from the repository root:

    python research/flow_forecast/pull_soil.py
"""
import gzip
import io
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "river_flow"))
from pull_rain import CELLS, SHARE, URL  # noqa: E402

LAYERS = {"sm_0_7": "soil_moisture_0_to_7cm_mean", "sm_7_28": "soil_moisture_7_to_28cm_mean",
          "sm_28_100": "soil_moisture_28_to_100cm_mean"}
OUT = Path(__file__).resolve().parent / "inputs" / "era5_soil_daily.csv.gz"


def pull(lat, lon, end, tries=4):
    params = {"latitude": lat, "longitude": lon, "start_date": "1940-01-01", "end_date": end, "models": "era5",
              "daily": ",".join(LAYERS.values()), "timezone": "Asia/Kolkata"}
    for k in range(tries):
        try:
            r = requests.get(URL, params=params, timeout=300)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if k == tries - 1:
                raise
            print(f"  retry {lat},{lon}: {type(exc).__name__} {str(exc)[:100]}", flush=True)
            time.sleep(30 * (k + 1))


def main():
    end = (date.today() - timedelta(days=7)).isoformat()
    cols = {}
    for name, (lat, lon) in CELLS.items():
        j = pull(lat, lon, end)
        day = pd.to_datetime(j["daily"]["time"])
        for short, var in LAYERS.items():
            cols[f"{short}_{name}"] = pd.Series(j["daily"][var], index=day, dtype="float64")
        print(f"{name:6s} {len(day)} days, first non-empty {cols[f'sm_0_7_{name}'].first_valid_index().date()}",
              flush=True)
        time.sleep(5)
    d = pd.DataFrame(cols).dropna()
    d.index.name = "date"
    for short in LAYERS:
        d[short] = sum(SHARE[n] * d[f"{short}_{n}"] for n in CELLS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    d.to_csv(buf, lineterminator="\n", float_format="%.4f", date_format="%Y-%m-%d")
    with open(OUT, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(buf.getvalue().encode())
    print(f"wrote {OUT.name} ({OUT.stat().st_size / 1e3:.0f} kB), {d.index[0].date()} to {d.index[-1].date()}",
          flush=True)


if __name__ == "__main__":
    main()
