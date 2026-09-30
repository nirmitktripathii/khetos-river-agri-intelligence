"""Does a real rain forecast help? A fair test on 2024-03 to 2026-09.

"Perfect prognosis": the model is trained on 1942-2023 with the rain that actually fell after each origin (the
best-case feature set), then tested with the rain that ECMWF *forecast* at the origin in its place. That is
exactly what the model would get in real use. Three rows per horizon, all on the same test days:

    honest      no future rain (the "gb all" model of run_compare.py)
    forecast    future rain from the archived ECMWF forecast (fair: known at the origin)
    best case   the rain that actually fell (a ceiling, not a skill)

Horizons stop at 7 days: the forecast archive holds leads of 1 to 7 days. The test period holds only two and a
half monsoons, so differences of a few hundredths are noise. The target is still GEOGLOWS, not the river.

    python run_rain_forecast.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forecast import data, features, gbm, metrics  # noqa: E402

HERE = Path(__file__).resolve().parent
FORECAST_FILE = HERE / "inputs" / "ecmwf_rain_forecast_daily.csv.gz"
APP_DATA = HERE.parents[1] / "data"
HINDCAST_FILE = APP_DATA / "nakatiya_rain_forecast_hindcast.csv"  # read by the River Water Watch page
SCORES_FILE = APP_DATA / "nakatiya_rain_forecast_scores.csv"
HONEST = ("base", "wetness", "soil", "upstream")
HORIZONS = (1, 3, 7)
TRAIN_END = "2023-11-30"  # 90-day gap before the first forecast day
TEST_START = "2024-03-01"


def forecast_future_rain(fc, horizon):
    """At each origin t: forecast rain on t+1..t+horizon (lead k for day t+k) and on its last three days,
    matching features.build's FUTURE_rain_sum and FUTURE_rain_last3."""
    parts = [fc[f"lead{k}_mm"].shift(-k) for k in range(1, horizon + 1)]
    total = sum(parts)
    last3 = sum(parts[-min(3, horizon):])
    return pd.DataFrame({"FUTURE_rain_sum": total, "FUTURE_rain_last3": last3})


def main():
    df = data.load("mouth")
    fc = pd.read_csv(FORECAST_FILE, index_col=0, parse_dates=True)
    rows, hind = [], []
    for h in HORIZONS:
        Xh, y = features.build(df, h, HONEST)
        Xb, yb = features.build(df, h, HONEST + ("future_rain",))
        fut = forecast_future_rain(fc, h)
        test = Xb.index[(Xb.index >= TEST_START)].intersection(fut.dropna().index)
        train_h, train_b = Xh.index[Xh.index <= TRAIN_END], Xb.index[Xb.index <= TRAIN_END]
        obs = np.expm1(yb.loc[test].to_numpy())
        monsoon = test.month.isin([6, 7, 8, 9])

        honest = gbm.predict(gbm.fit(Xh.loc[train_h], y.loc[train_h]), Xh.loc[test])
        m_best = gbm.fit(Xb.loc[train_b], yb.loc[train_b])
        best = gbm.predict(m_best, Xb.loc[test])
        Xf = Xb.loc[test].copy()
        Xf[fut.columns] = fut.loc[test]
        forecast = gbm.predict(m_best, Xf)

        hind.append(pd.DataFrame({
            "target_date": test.shift(h, freq="D"), "origin_date": test, "horizon_days": h,
            "geoglows_m3s": obs, "forecast_m3s": np.expm1(forecast[:, 1]), "forecast_low_m3s": np.expm1(forecast[:, 0]),
            "forecast_high_m3s": np.expm1(forecast[:, 2]), "no_rain_forecast_m3s": np.expm1(honest[:, 1])}))
        for name, band in (("honest (no future rain)", honest), ("ECMWF rain forecast", forecast),
                           ("BEST CASE (rain that fell)", best)):
            sim = np.expm1(band[:, 1])
            rows.append({"horizon_days": h, "model": name, "test_days": len(test), **metrics.report(obs, sim),
                         "NSE_monsoon": metrics.nse(obs[monsoon], sim[monsoon]),
                         "coverage_10_90": metrics.coverage(yb.loc[test], band[:, 0], band[:, 2])})
    table = pd.DataFrame(rows)
    (HERE / "out").mkdir(exist_ok=True)
    table.to_csv(HERE / "out" / "rain_forecast_mouth.csv", index=False)
    pd.concat(hind).to_csv(HINDCAST_FILE, index=False, float_format="%.4f", date_format="%Y-%m-%d",
                           lineterminator="\n")
    table.to_csv(SCORES_FILE, index=False, float_format="%.4f", lineterminator="\n")
    with pd.option_context("display.width", 160, "display.float_format", "{:.3f}".format):
        print(table.to_string(index=False))


if __name__ == "__main__":
    main()
