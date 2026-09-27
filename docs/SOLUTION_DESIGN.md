# KhetOS solution design

## Principles

1. **Signals, not verdicts.** Every river output is a change flag or an encroachment-risk candidate, to be checked against revenue records and field visits.
2. **Use the best product for each question.** Built-up change comes from validated settlement and land-cover maps. Water and vegetation come from harmonised Landsat, and recent detail from Sentinel-2 and Sentinel-1.
3. **Thresholds are measured, not guessed.** Each flag threshold sits above the noise measured in `scripts/calibration/`.
4. **Free, keyless, small.** Planetary Computer STAC, DLR, Esri Living Atlas, Open-Meteo and Overpass. Reads are windowed Cloud-Optimised GeoTIFF reads onto a grid of at most 1024 px.

## Data per question

| Question | Source | Period | Resolution |
|---|---|---|---|
| Built-up growth near the river | WSF Evolution / Impact Observatory LULC | 1985–2015 / 2017–2025 | 30 m / 10 m |
| Water footprint, vegetation, long-run trend | Landsat 5/7/8/9 C2 L2, Roy 2016 harmonised, rabi composites | 1990–2026 | 30 m |
| Riparian health now | Sentinel-2 L2A, greenest pixel in Jan–Mar | 2017– | 10 m |
| New construction alerts | Sentinel-2 L2A: this window vs the same window a year earlier | last 90 days | 10 m |
| Flood / wet expansion | Sentinel-1 RTC VV, same orbit | last ~45 days | 10 m |
| Historic water | JRC Global Surface Water transitions | 1984–2021 | 30 m |
| Field scouting | Sentinel-2 + Open-Meteo + Isolation Forest | last 6 months | 10 m |

## Runtime architecture

```text
Streamlit script thread ── eo.run_analysis ──► _ANALYSIS_POOL (4)
                                                  │ run_jobs ──► JOB_POOL (6)
                                                  │ _pmap(io=False) ──► _TASK_POOL (8)
                                                  └ _pmap / _on_io ──► _IO_POOL (16)  ← all GDAL reads
```

The pools are persistent daemon threads, so no thread that has touched GDAL ever exits. That prevents the Windows deadlock. Results are cached by Streamlit per area, buffer and year.

## Roadmap (in priority order)

1. **Close the origin gap.** Trace the channel from Dehnagar to the mapped head:
   - Copernicus DEM 30 m (on Planetary Computer) flow accumulation, masked by a Sentinel-2 wet-season MNDWI;
   - check the result against unnamed OSM way 488077884;
   - collect field GPS points.
2. **Surveyed banks.** Replace centreline buffers with a bank polygon: the JRC maximum water extent together with the Sentinel-2 wet-season water mask.
3. **Master Plan overlay.** Once the BDA 2031 plan's river-buffer clause is confirmed, georeference its green-belt layer and compare it with built-up change directly.
4. **Qila.** Build a named geometry for the reported 112 km course (Uttarakhand → Baheri → Deorania → Bhojipura → Ramganga), then add it as a second river with the same workflows.
5. **Ground truth loop.** Store field-visit outcomes (GeoParquet) to measure alert precision and tune thresholds per reach.
6. **Built-up after 2025.** Add GHS-BUILT-S 2025 or a later Impact Observatory release when one is published, to close the 2025→now gap.
