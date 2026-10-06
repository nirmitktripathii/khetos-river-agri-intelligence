"""Fix the too-narrow bands with conformalised quantile regression (Romano et al. 2019).

The rain-forecast model's 10-90 % band was trained on exact rain, so it is too narrow when fed forecast rain. CQR
widens (or narrows) the band by one constant, in log-flow terms, chosen on a calibration period so that 80 % of
calibration days fall inside it, with a separate constant for the monsoon (June-September) and the rest of the
year, because one constant over-covers the dry season and under-covers the monsoon. Calibration: 2024-03 to 2024-12. Test: 2025-01 onward, never used to choose
anything. Reads the hindcast written by run_rain_forecast.py.

    python run_calibrate.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forecast import metrics  # noqa: E402
from run_rain_forecast import APP_DATA, HINDCAST_FILE  # noqa: E402

CALIBRATION_FILE = APP_DATA / "nakatiya_rain_forecast_calibration.csv"  # read by the River Water Watch page

CAL_END = "2024-12-31"
TARGET = 0.80


def cqr_margin(obs, low, high, target=TARGET):
    """The smallest margin m (log1p flow) so that [low - m, high + m] holds `target` of the calibration days."""
    lo, hi, y = np.log1p(low), np.log1p(high), np.log1p(obs)
    score = np.maximum(lo - y, y - hi)
    n = len(score)
    return float(np.quantile(score, min(1.0, np.ceil((n + 1) * target) / n), method="higher"))


def widen(low, high, margin):
    low, high = np.asarray(low, float), np.asarray(high, float)
    return np.expm1(np.log1p(low) - margin).clip(min=0), np.expm1(np.log1p(high) + margin)


def main():
    h = pd.read_csv(HINDCAST_FILE, parse_dates=["origin_date", "target_date"])
    rows = []
    h["forecast_low_cal_m3s"], h["forecast_high_cal_m3s"] = np.nan, np.nan
    for hz, d in h.groupby("horizon_days"):
        cal, test = d[d["origin_date"] <= CAL_END], d[d["origin_date"] > CAL_END]
        mon_cal = cal["target_date"].dt.month.isin([6, 7, 8, 9]).to_numpy()
        mon_test = test["target_date"].dt.month.isin([6, 7, 8, 9]).to_numpy()
        margins = {w: cqr_margin(cal["geoglows_m3s"][mon_cal == w], cal["forecast_low_m3s"][mon_cal == w],
                                 cal["forecast_high_m3s"][mon_cal == w]) for w in (True, False)}
        m = np.where(mon_test, margins[True], margins[False])
        lo, hi = widen(test["forecast_low_m3s"], test["forecast_high_m3s"], m)
        # widened band for every day (calibration days included) so the page can draw it
        mon_all = d["target_date"].dt.month.isin([6, 7, 8, 9]).to_numpy()
        h.loc[d.index, "forecast_low_cal_m3s"], h.loc[d.index, "forecast_high_cal_m3s"] = widen(
            d["forecast_low_m3s"], d["forecast_high_m3s"], np.where(mon_all, margins[True], margins[False]))
        obs = test["geoglows_m3s"].to_numpy()
        mon = test["target_date"].dt.month.isin([6, 7, 8, 9]).to_numpy()
        rows.append({"horizon_days": hz, "cal_days": len(cal), "test_days": len(test), "margin_monsoon": margins[True], "margin_dry": margins[False],
                     "coverage_before": metrics.coverage(obs, test["forecast_low_m3s"], test["forecast_high_m3s"]),
                     "coverage_after": metrics.coverage(obs, lo, hi),
                     "coverage_after_monsoon": metrics.coverage(obs[mon], lo[mon], hi[mon]),
                     "coverage_after_dry": metrics.coverage(obs[~mon], lo[~mon], hi[~mon]),
                     "median_width_before": float(np.median(test["forecast_high_m3s"] - test["forecast_low_m3s"])),
                     "median_width_after": float(np.median(hi - lo))})
    t = pd.DataFrame(rows)
    t.to_csv(CALIBRATION_FILE, index=False, float_format="%.4f", lineterminator="\n")
    h.to_csv(HINDCAST_FILE, index=False, float_format="%.4f", date_format="%Y-%m-%d", lineterminator="\n")
    with pd.option_context("display.width", 160, "display.float_format", "{:.3f}".format):
        print(t.to_string(index=False))


if __name__ == "__main__":
    main()
