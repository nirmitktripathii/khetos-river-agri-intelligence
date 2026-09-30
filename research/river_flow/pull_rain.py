"""Pull daily ERA5 rain and reference evapotranspiration for the Nakatiya catchment (Open-Meteo archive, CC BY 4.0).

The catchment lies in three 0.25-degree ERA5 cells stacked north to south along 79.5 E, the same grid whose runoff
drives GEOGLOWS. `models=era5` is set on purpose: the default "best match" switches to a finer weather model in
2017, which would put a step in a long series. Writes inputs/era5_daily.csv.gz: one column per cell, and the
catchment mean weighted by the share of the catchment in each cell. Days are India Standard Time days. Run from
the repository root:

    python research/river_flow/pull_rain.py
"""
import gzip
import io
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

URL = "https://archive-api.open-meteo.com/v1/archive"
CELLS = {"north": (28.75, 79.5), "middle": (28.5, 79.5), "south": (28.25, 79.5)}  # (lat, lon) of the cell centres
# Rough share of the 371.5 km² catchment in each cell, read from the GEOGLOWS contributing area where the river
# crosses a cell edge (about 140 km² lie north of 28.375 N). No catchment polygon was used: good to about 0.05.
SHARE = {"north": 0.10, "middle": 0.30, "south": 0.60}
OUT = Path(__file__).resolve().parent / "inputs" / "era5_daily.csv.gz"


def pull(lat, lon, end, tries=4):
    params = {"latitude": lat, "longitude": lon, "start_date": "1940-01-01", "end_date": end, "models": "era5",
              "daily": "precipitation_sum,et0_fao_evapotranspiration", "timezone": "Asia/Kolkata"}
    for k in range(tries):
        try:
            r = requests.get(URL, params=params, timeout=300)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if k == tries - 1:
                raise
            print(f"  retry {lat},{lon}: {type(exc).__name__} {str(exc)[:100]}", flush=True)
            time.sleep(10 * (k + 1))


def main():
    end = (date.today() - timedelta(days=7)).isoformat()  # ERA5 is final about five days behind
    cols = {}
    for name, (lat, lon) in CELLS.items():
        j = pull(lat, lon, end)
        day = pd.to_datetime(j["daily"]["time"])
        cols[f"rain_{name}_mm"] = pd.Series(j["daily"]["precipitation_sum"], index=day, dtype="float64")
        cols[f"et0_{name}_mm"] = pd.Series(j["daily"]["et0_fao_evapotranspiration"], index=day, dtype="float64")
        print(f"{name:6s} asked {lat},{lon}  got {j['latitude']:.3f},{j['longitude']:.3f}  "
              f"{len(day)} days  mean {cols[f'rain_{name}_mm'].mean() * 365.25:.0f} mm/yr", flush=True)
    d = pd.DataFrame(cols).dropna()
    d.index.name = "date"
    d["rain_mm"] = sum(SHARE[n] * d[f"rain_{n}_mm"] for n in CELLS)
    d["et0_mm"] = sum(SHARE[n] * d[f"et0_{n}_mm"] for n in CELLS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    d.to_csv(buf, lineterminator="\n", float_format="%.2f", date_format="%Y-%m-%d")
    with open(OUT, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(buf.getvalue().encode())
    print(f"wrote {OUT.name} ({OUT.stat().st_size / 1e3:.0f} kB), {d.index[0].date()} to {d.index[-1].date()}",
          flush=True)


if __name__ == "__main__":
    main()
