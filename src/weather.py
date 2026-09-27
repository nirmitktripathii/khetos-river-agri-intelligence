"""Weather context from Open-Meteo (model data, CC BY 4.0): rainfall, reference evapotranspiration and
modelled soil moisture. These are forecast-model analyses for a ~10 km grid cell, not gauge readings."""
import pandas as pd
import requests

from src.config import OPEN_METEO_URL

SOIL_LAYERS = ("0_to_1cm", "3_to_9cm", "9_to_27cm", "27_to_81cm")


def get_weather_context(lat, lon, days=14):
    """Rain and FAO-56 reference evapotranspiration (ET0) summed over the last `days` complete days
    (Asia/Kolkata), and the current modelled volumetric soil moisture (m³/m³) by depth."""
    params = {
        "latitude": lat, "longitude": lon,
        "daily": "precipitation_sum,et0_fao_evapotranspiration,temperature_2m_max,temperature_2m_min",
        "current": ",".join([f"soil_moisture_{d}" for d in SOIL_LAYERS] + ["temperature_2m"]),
        "past_days": days, "forecast_days": 1, "timezone": "Asia/Kolkata",
    }
    r = requests.get(OPEN_METEO_URL, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()
    current = data.get("current", {})
    daily = pd.DataFrame(data["daily"])
    daily["time"] = pd.to_datetime(daily["time"])
    today = pd.Timestamp(current.get("time") or pd.Timestamp.now()).normalize()
    past = daily[daily["time"] < today].tail(days).reset_index(drop=True)  # today is still partly forecast
    rain = float(past["precipitation_sum"].fillna(0).sum())
    et0 = float(past["et0_fao_evapotranspiration"].fillna(0).sum())
    out = {
        "rain_14d_mm": round(rain, 1), "et0_14d_mm": round(et0, 1), "water_deficit_14d_mm": round(et0 - rain, 1),
        "wettest_day_mm": round(float(past["precipitation_sum"].max()), 1) if len(past) else None,
        "temperature_now_c": current.get("temperature_2m"),
        "period": f"{past['time'].min():%d %b} – {past['time'].max():%d %b %Y}" if len(past) else "n/a",
        "observed_at": current.get("time"),
        "model_cell": (data.get("latitude"), data.get("longitude")), "elevation_m": data.get("elevation"),
        "daily": past.rename(columns={"precipitation_sum": "rain_mm", "et0_fao_evapotranspiration": "et0_mm",
                                      "temperature_2m_max": "tmax_c", "temperature_2m_min": "tmin_c"}),
    }
    out.update({f"soil_moisture_{d}": current.get(f"soil_moisture_{d}") for d in SOIL_LAYERS})
    return out
