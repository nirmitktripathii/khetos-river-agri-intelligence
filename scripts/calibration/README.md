# Calibration scripts

These scripts measure how much each KhetOS signal moves when nothing on the ground has changed. The
flag thresholds in `src/river.py` are set just above that noise floor. They read live public data, so a
rerun gives slightly different numbers as the archives are reprocessed.

| Script | What it measures | Sets | Recorded result |
|---|---|---|---|
| `landsat_vs_landcover.py` | Landsat index rules against Impact Observatory 10 m land cover (2017, 2023) | Built-up change comes from WSF Evolution and Impact Observatory, not Landsat | "Never green" is precise for urban built-up land (0.83–0.95) but misses tree-shaded settlement (recall 0.48–0.60 urban, 0.01–0.20 rural; `results/landsat_vs_landcover.txt`) |
| `landsat_season_noise.py` | Water and vegetated shares for every rabi season 1990–2026, urban and upper reach, 250 m corridor | `VEGETATION_THRESHOLD_PP = 15`, 3-season medians, the ≥2 peak-scene rule | [`results/landsat_season_noise.txt`](results/landsat_season_noise.txt) |
| `landsat_noise_rules.py` | Neighbouring-season spread under each comparison rule, parsed from the file above (offline) | As above | Upper reach: 3-season medians (≥2 peak scenes) 3–5 years apart differ by ≤13.9 pp; single seasons by up to 48 pp |
| `riparian_window_noise.py` | Year-on-year vegetated share of matched 90-day Sentinel-2 windows, by season, 2024–2026 | `RIPARIAN_THRESHOLD_PP = 10`, Jan–Mar windows only | [`results/riparian_window_noise.txt`](results/riparian_window_noise.txt): Jan–Mar ≤4.1 pp, Apr–Jun up to 12.4 pp, Jul–Sep up to 18.3 pp |

Run from the repository root, for example `python scripts/calibration/landsat_season_noise.py`.
