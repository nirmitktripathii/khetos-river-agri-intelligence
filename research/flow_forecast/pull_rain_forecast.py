"""Pull archived ECMWF rain forecasts for the Nakatiya catchment (Open-Meteo Previous Runs API, CC BY 4.0).

For every hour, `precipitation_previous_dayN` is what the ECMWF IFS 0.25-degree forecast issued N days earlier said
that hour's rain would be (N = 1..7). The archive for this model starts on 2024-03-01. Hours are summed into India
Standard Time days and the three ERA5 cells are weighted as in research/river_flow/pull_rain.py. Writes
inputs/ecmwf_rain_forecast_daily.csv.gz with columns `lead1_mm` ... `lead7_mm`: row d, column leadN is the rain
forecast for day d made N days before. Run from the repository root:

    python research/flow_forecast/pull_rain_forecast.py
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
from pull_rain import CELLS, SHARE  # noqa: E402

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODEL = "ecmwf_ifs025"
START = "2024-03-01"
LEADS = range(1, 8)
OUT = Path(__file__).resolve().parent / "inputs" / "ecmwf_rain_forecast_daily.csv.gz"


def pull(lat, lon, end, tries=4):
    params = {"latitude": lat, "longitude": lon, "start_date": START, "end_date": end, "models": MODEL,
              "hourly": ",".join(f"precipitation_previous_day{n}" for n in LEADS), "timezone": "Asia/Kolkata"}
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
    end = (date.today() - timedelta(days=1)).isoformat()
    cells = {}
    for name, (lat, lon) in CELLS.items():
        h = pull(lat, lon, end)["hourly"]
        hourly = pd.DataFrame({f"lead{n}_mm": h[f"precipitation_previous_day{n}"] for n in LEADS},
                              index=pd.to_datetime(h["time"]), dtype="float64")
        # a day counts only if all 24 hours are present
        daily = hourly.resample("D").sum(min_count=24)
        cells[name] = daily
        print(f"{name:6s} {daily.dropna().shape[0]} complete days", flush=True)
        time.sleep(5)
    d = sum(SHARE[n] * cells[n] for n in CELLS).dropna(how="all")
    d.index.name = "date"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    d.to_csv(buf, lineterminator="\n", float_format="%.2f", date_format="%Y-%m-%d")
    with open(OUT, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(buf.getvalue().encode())
    print(f"wrote {OUT.name} ({OUT.stat().st_size / 1e3:.0f} kB), {d.index[0].date()} to {d.index[-1].date()}",
          flush=True)


if __name__ == "__main__":
    main()
