"""Daily inputs for the yearly observatory table, as far back as each free source goes.

    flow   GEOGLOWS v2 retrospective, river segment 441105311 (Nakatiya at Khajuriya ghat, 28.3621 N 79.4754 E,
           near the Pilibhit bypass; river km 29.4, 186 m from the mapped channel), 1940 onward. Modelled, not
           measured: ERA5 runoff routed on TDX-Hydro, no city, canals or pumping. Segment chosen by querying the
           API every 0.5 km along the OSM main stem: km 22-26 -> 441010366 ("entering"), km 26.5-29.5 ->
           441105311, km 30-32.5 -> 440651232 (off the main stem), km 33-34 -> 441006241 (Saidpur Khajuria, 4.6 km
           downstream), km 34.5 on -> 441007617. The ghat lies in 441105311 near its lower end.
    rain   ERA5 daily rain at Baheri (28.774 N, 79.498 E; the ERA5 cell centred 28.75 N 79.5 E), 1940 onward,
           Open-Meteo archive with models=era5 so the series has no model switch. Days are IST days.

Writes inputs/khajuria_flow_daily.csv.gz and inputs/baheri_rain_daily.csv.gz.

    .venv/Scripts/python.exe research/nakatiya_observatory/pull_inputs.py
"""
import io
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
KHAJURIA_ID = 441105311
BAHERI = (28.774, 79.498)
GEOGLOWS = "https://geoglows.ecmwf.int/api/v2/"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"


def _get(url, params, timeout=300, tries=4):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as exc:
            if k == tries - 1:
                raise
            print(f"  retry: {type(exc).__name__} {str(exc)[:100]}", flush=True)
            time.sleep(10 * (k + 1))


def pull_flow():
    r = _get(GEOGLOWS + f"retrospectivedaily/{KHAJURIA_ID}", {"format": "csv"})
    d = pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True)
    s = d.iloc[:, 0].rename("flow_m3s")
    s.index = s.index.tz_localize(None).normalize()
    s.index.name = "date"
    return s


def pull_rain():
    end = (date.today() - timedelta(days=6)).isoformat()  # ERA5 lags about five days
    lat, lon = BAHERI
    j = _get(OPEN_METEO, {"latitude": lat, "longitude": lon, "start_date": "1940-01-01", "end_date": end,
                          "models": "era5", "daily": "precipitation_sum", "timezone": "Asia/Kolkata"}).json()
    s = pd.Series(j["daily"]["precipitation_sum"], index=pd.to_datetime(j["daily"]["time"]), name="rain_mm")
    s.index.name = "date"
    print(f"  ERA5 cell used: {j['latitude']:.3f} N {j['longitude']:.3f} E", flush=True)
    return s


def main():
    INPUTS.mkdir(exist_ok=True)
    flow = pull_flow()
    flow.to_csv(INPUTS / "khajuria_flow_daily.csv.gz", float_format="%.4f")
    print(f"flow: {flow.index.min().date()} to {flow.index.max().date()}, mean {flow.mean():.2f} m3/s", flush=True)
    rain = pull_rain()
    rain.to_csv(INPUTS / "baheri_rain_daily.csv.gz", float_format="%.2f")
    print(f"rain: {rain.index.min().date()} to {rain.dropna().index.max().date()}, "
          f"mean {rain.mean() * 365.25:.0f} mm/yr", flush=True)


if __name__ == "__main__":
    main()
