# KhetOS user guide

How every module in [KhetOS](https://khetos-river-agri-intelligence.streamlit.app/) works: where each number comes from, how it is calculated, and what a typical run looks like. Every formula and threshold here was checked against the code.

**Every output is a change flag or a signal for follow-up. None is a legal, ownership or agronomic ruling.**

---

## Part 0 — Basics every module relies on

### 0.1 How a satellite "sees"

- Sunlight hits the ground and part of it bounces back. A satellite camera measures **how much bounces back in each colour band**. That fraction (0 = nothing, 1 = everything) is the **reflectance**.
- Some bands are invisible to our eyes:
  - **NIR (Near-InfraRed)** is just beyond red.
  - **SWIR (Short-Wave InfraRed)** is further beyond.
- Different surfaces have a signature pattern across these bands:

| Surface | Red | NIR | SWIR | Why |
|---|---|---|---|---|
| Healthy green crop | low | **very high** | medium | Chlorophyll absorbs red for photosynthesis. The inner structure of a leaf scatters NIR strongly. |
| Bare soil / concrete | medium | medium | high | Nothing green absorbs the red. |
| Water | low | **very low** | **very low** | Water swallows infrared. |
| Water-rich leaves | – | high | lower | The water inside leaves absorbs SWIR. |

*Analogy:* think of shining different coloured torches on unknown fabrics. Each fabric glows differently under each torch, so the combination identifies it.

### 0.2 Indices: turning colours into a single "health number"

All three indices use the **normalized difference** `(A − B) / (A + B)`, which always lies between −1 and +1.

- Dividing by `(A + B)` cancels overall brightness. A hazy day and a sunny day give similar values, like comparing a student's *percentage* rather than raw marks from papers with different totals.

| Index | Full form | Formula | What it tells you |
|---|---|---|---|
| **NDVI** | Normalized Difference Vegetation Index | (NIR − Red) / (NIR + Red) | Greenness, i.e. the amount of living leaf |
| **NDMI** | Normalized Difference Moisture Index | (NIR − SWIR1) / (NIR + SWIR1) | Water held in the leaves |
| **MNDWI** | Modified Normalized Difference Water Index | (Green − SWIR1) / (Green + SWIR1) | Open water (it is positive over water) |

**Worked example (NDVI):**

- A wheat field in February has NIR = 0.40 and Red = 0.05. NDVI = (0.40 − 0.05) / (0.40 + 0.05) = **0.78**, a dense canopy.
- The same field after harvest has NIR = 0.25 and Red = 0.20. NDVI = 0.05 / 0.45 = **0.11**, bare.

How the app words NDVI in plain language:

<!-- ndvi-scale -->

| NDVI | Meaning |
|---|---|
| below 0.2 | bare soil, a harvested or fallow field, or a crop just sown |
| 0.2–0.4 | a sparse or early canopy, or a crop that is senescing (ageing) or stressed |
| 0.4–0.6 | a moderate green canopy |
| 0.6 and above | a dense green canopy |

For NDMI, the app calls a value below 0 "low", 0–0.2 "moderate" and above 0.2 "high" canopy water.

### 0.3 Pixels and resolution

- A picture is a grid of squares called **pixels**. **Resolution** is the ground size of one square:
  - **Sentinel-2:** 10 m, so 1 pixel = 100 m² and 100 pixels = 1 hectare (ha). Its SWIR band is 20 m.
  - **Landsat:** 30 m, so 1 pixel = 900 m² (about 0.09 ha).
- One acre (about 4,047 m²) is roughly **40 Sentinel-2 pixels**. Pixels on a field's edge mix in bunds, trees and roads.
- All images are placed on one common map grid, **UTM (Universal Transverse Mercator)** zone 44 North. Distances there are in metres, so "100 m from the river" is measured properly.

### 0.4 Clouds: the biggest enemy

- Optical satellites cannot see through cloud. Each Sentinel-2 image comes with an **SCL (Scene Classification Layer)**, a per-pixel label. KhetOS keeps only pixels labelled 4–7:
  - 4: vegetation
  - 5: bare/not vegetated
  - 6: water
  - 7: unclassified
- Clouds, cloud shadow, snow, saturated and missing pixels are thrown away.
- Landsat has a similar **QA (Quality Assessment)** band. KhetOS rejects any pixel flagged as fill, dilated cloud, cirrus, cloud, cloud shadow or snow.

### 0.5 Median, percentage points, and "unusual"

- **Median** is the middle value after sorting. For a field with pixel NDVIs of 0.60, 0.62, 0.65, 0.66 and 0.05 (the 0.05 is a road pixel), the median is **0.62**, while the average would be 0.52. The median is not fooled by a few odd pixels, so KhetOS uses it everywhere.
- **pp (percentage points):** if built-up land goes from 20% to 23% of a corridor, that is **+3 pp**, even though it is a 15% *relative* increase. All river changes are reported in pp.
- **Robust z-score** answers "how unusual is this value compared with its usual spread?":
  - Ordinary z = (value − average) / standard deviation.
  - KhetOS uses the outlier-resistant version: `z = (value − median) / (1.4826 × MAD)`.
  - **MAD (Median Absolute Deviation)** is the median distance of the values from their median. The factor 1.4826 makes it comparable to a standard deviation.
  - z = −1 is a mildly low value, and z = −3 is exceptional.
  - *Analogy:* if your commute normally takes 30 ± 3 minutes, 45 minutes gives z ≈ +5, which is very unusual. 32 minutes gives z ≈ +0.7, which is normal.

### 0.6 Radar: a satellite that brings its own torch

- **SAR (Synthetic Aperture Radar)** on **Sentinel-1** sends microwave pulses and listens for the echo. It works **through clouds and at night**, which matters in the monsoon.
- The echo strength is called **backscatter**. It is measured in **dB (decibels)**, a log scale: −3 dB is half the echo and −10 dB is one tenth.
- **VV / VH polarisation:**
  - **VV:** sent vertical, received vertical. It is strong from rough surfaces.
  - **VH:** sent vertical, received horizontal. It is strong where the wave bounces around inside a *volume* such as a leafy crop canopy, so VH rises as crops grow.
- **Calm water acts like a mirror.** The pulse glances away and almost nothing returns, so water is very dark: VV below **−18 dB**.
- **RVI (Radar Vegetation Index)** = 4 × VH / (VV + VH), using linear (not dB) values. It is about 0 for bare ground and approaches 1 for a dense canopy.
- KhetOS uses **RTC (Radiometric Terrain Corrected)** Sentinel-1 data, which is already corrected for slope and viewing angle.

### 0.7 Weather terms

- **ET0 (reference EvapoTranspiration)**, computed by the **FAO (Food and Agriculture Organization)-56** method, is the water (in mm) that a well-watered reference grass crop would lose to the air under that weather.
  - *Analogy:* ET0 is the crop's **water bill**, and rain is the **income**. Deficit = ET0 − rain; positive means the bill exceeded the income.
- **Soil moisture (m³/m³):** 0.30 means 30% of the soil's volume is water.
- All weather comes from **Open-Meteo**, a free weather API. Its values are **forecast-model estimates for a ~10 km grid cell**, not a rain gauge in your field.

### 0.8 The rabi season, and why it is used for history

- The **rabi** crop season (wheat, mustard) runs **December–March**. KhetOS labels it by the year it ends in, so "rabi 2026" = Dec 2025–Mar 2026. Its **peak** is **mid-January to March**, when crops are at full canopy.
- The sky is usually clear then, and every year the land is at the same point in its calendar. So long-term comparisons always use rabi images.
- *Analogy:* measuring a child's height on every birthday, not on random days.

### 0.9 River words

- **Centreline:** the river drawn as a single line in **OSM (OpenStreetMap)**, the free, volunteer-built world map. The Nakatiya is made of 8 OSM "ways" (line pieces).
- **Buffer / corridor / half-width:** a band of fixed width on **both sides** of the centreline, like dragging a thick marker along the river. "100 m" means 100 m each side, so the band is about 200 m wide.
  - Distances are measured from the drawn line, not from surveyed banks.
- **Reach:** the part of the river within a set radius of an anchor point.
  - Urban reach (Dohra Road–Bisalpur Road): 4 km radius.
  - Upper reach (Bhojipura side): 6 km.
  - Lower reach (Ramganga confluence): 6 km.
  - "Whole mapped river" uses all of it.

### 0.10 The sidebar controls, used by the farm modules

| Control | Meaning | Default |
|---|---|---|
| Jump to / Longitude / Latitude | Centre of your **AOI (Area Of Interest)** | Lower-reach farmland, about 13 km south of Bareilly |
| Half-width (km) | The AOI is a square this far either side of the centre | 1.5 km, so a 3 × 3 km square (900 ha) |
| History window (months) | How far back to look for images | 6 |
| Cloud tolerance (%) | The most cloud allowed **over your AOI** (not the whole image) | 35%, so an image needs ≥ 65% of AOI pixels clear. It also needs data over ≥ 90% of the AOI. |

### 0.11 Where the data comes from (all free, no login)

| Data | What it is | Provider and access | Years | Pixel |
|---|---|---|---|---|
| Sentinel-2 L2A | Optical multispectral images, atmosphere-corrected (Level-2A) | **ESA (European Space Agency)** Copernicus, via **Microsoft Planetary Computer** | 2017– | 10–20 m |
| Sentinel-1 RTC | Radar backscatter | ESA, via Planetary Computer | recent 45–60 days used | 10 m, averaged to 30 m |
| Landsat Collection 2 Level-2 | Optical, long archive | **USGS (United States Geological Survey)**, via Planetary Computer | 1990– | 30 m |
| **WSF (World Settlement Footprint) Evolution** | The year each pixel first became settled | **DLR (German Aerospace Center)** | 1985–2015 | 30 m |
| Impact Observatory **LULC (Land Use / Land Cover)** | Annual map: water, trees, flooded vegetation, crops, built, bare, rangeland | Impact Observatory / Microsoft / Esri | 2017–2025 | 10 m |
| **JRC (Joint Research Centre) GSW (Global Surface Water)** | Monthly water history from Landsat | European Commission JRC, via Planetary Computer | 1984–2020 | 30 m |
| Weather | Rain, ET0, temperature, soil moisture | Open-Meteo | last 14 days | ~10 km |
| River line | Nakatiya ways | OSM, via the Overpass API (a read-only OSM query service) | extract bundled; live refresh | vector |
| District boundaries | Rohilkhand's 5 districts | geoBoundaries | bundled | vector |

- **How images are fetched:** the app searches a **STAC (SpatioTemporal Asset Catalog)**, a standard index of satellite images, for images over your area and dates.
  - The images are **COGs (Cloud-Optimised GeoTIFFs)**, so the app downloads **only the small window it needs**, not whole images.
  - Results are cached for 6 hours, so a repeat run is instant.
- **Sentinel-2 correction:** images from 2022 onwards carry a −1000 offset in their raw numbers. KhetOS removes it, then divides by 10,000 to get reflectance. Without this step, recent NDVI would be biased.
- **Landsat correction:** each raw value becomes reflectance as `value × 0.0000275 − 0.2`. Older sensors are then mapped onto the newest one's scale using published conversion factors (Roy et al., 2016), so 1990 and 2026 are comparable:
  - **TM (Thematic Mapper):** Landsat 4 and 5.
  - **ETM+ (Enhanced Thematic Mapper Plus):** Landsat 7.
  - **OLI (Operational Land Imager):** Landsat 8 and 9, the newest.

### 0.12 Common to every result

- **Evidence report (Markdown):** all numbers, notes, a disclaimer and data credits.
- **CSV (Comma-Separated Values)** tables and **GeoJSON** map layers (a standard file format for map shapes) where relevant.
- **Every output is a change flag or signal for follow-up, never a legal, ownership or agronomic ruling.**

---

## 1. 🌍 Rohilkhand Overview

**Question:** Where is everything, and what is mapped?

**Data:** the bundled OSM river extract (or a live refresh), geoBoundaries districts and the reach definitions. **No satellite imagery**, so the page loads instantly.

**The numbers:**

| Metric | How it is calculated |
|---|---|
| **Main stem: 72.9 km** | Three specific OSM ways are joined into one line from the head to the mouth. Its length is measured along the Earth's curved surface (not a flat-map shortcut). |
| **All mapped ways: 85.1 km** | The same measurement over all 8 ways, including side channels. |
| **OSM ways: 8** | A count of the line pieces in the extract. |
| **Reaches: 3** | The named analysis reaches. |
| **Head and confluence markers** | The two ends of the main line. The end closer to the known Ramganga junction (79.48371 E, 28.13537 N) is the mouth; the other is the head, east of Bhojipura. |

**Map layers:**

- district outlines;
- the Nakatiya (blue);
- the Qila candidate (dashed purple, unverified);
- yellow circles for the three reaches;
- a purple box for your current AOI.

**The course note:** OSM has no Nakatiya north of the head. The reported source (Dehnagar/Deenagar, Baheri) therefore lies upstream of what is mapped, and that stretch is unverified.

**Other items:**

- **"Refresh the river from OpenStreetMap"** queries Overpass live. It uses mirror servers, with a 45 s timeout. "Use the bundled extract" switches back.
- **Expanders:**
  - press and plan reports, which are paraphrased and not verified;
  - FarmVibes.AI, an optional Microsoft agriculture toolkit (inactive unless configured).

**End-to-end run:** open the page. It draws the saved geometry, measures the lengths and places the markers. Click Refresh only if you want the newest OSM edits.

---

## 2. 🛰 Change Radar

**Question:** Is my area's greenness or leaf water moving **unusually** right now, compared with this same area's own past behaviour?

**Data:** Sentinel-2 (red, NIR and SWIR bands, plus SCL) and the Impact Observatory 2025 cropland map.

**End-to-end run:**

1. **Set the area:** in the sidebar, pick an AOI (e.g. the default 3 × 3 km square) and a history window (6 months). Click **Run change radar**.
2. **Find images:**
   - Search the catalogue for every Sentinel-2 image over the AOI in the last 6 months.
   - Keep **one image per day**: prefer an image tile that fully contains the AOI, then the least cloudy.
   - Spread the images evenly through time, **up to 24**.
3. **Screen for cloud:** for each image, read only the SCL layer on a 20 m grid. Accept the image if data covers ≥ 90% of the AOI and ≥ 65% of AOI pixels are clear.
4. **Compute the indices:** for accepted images, read the bands, correct them, and compute NDVI and NDMI for every pixel.
5. **Summarise each date:** take the **median over clear cropland pixels**.
   - "Cropland" = pixels labelled *crops* in the 2025 land-cover map, so a village, mango orchard or road does not drag the number.
   - If fewer than 50 clear cropland pixels exist, all clear pixels are used, and the table says so.
6. The result is a time series: one NDVI and one NDMI per clear date.
7. Compare the **latest two** dates and score the move (below).
8. For the map, the latest and previous clear scenes are read at full 10 m detail.

**The numbers:**

| Metric | How it is calculated |
|---|---|
| **NDVI (big number)** | The latest date's median NDVI over clear cropland. |
| **Small arrow under NDVI** | Latest minus previous clear date (e.g. −0.08). |
| **NDMI and its arrow** | The same, for leaf water. |
| **Clear scenes** | How many dates passed the cloud test. |
| **Change score (0–100)** | See below. 50 is an ordinary move; the higher it is, the more unusual the *decline*. |

**Change score, method A** (when there are **7 or more** clear scenes, i.e. at least 5 earlier scene-to-scene changes to learn from):

- Compute every earlier step change, e.g. +0.03, −0.02, +0.05, −0.01, +0.02.
- Measure the latest change's robust z against those steps: `z_NDVI` and `z_NDMI`.
- `Score = 50 − 15 × (0.55 × z_NDVI + 0.45 × z_NDMI)`, clipped to 0–100. Greenness carries 55% of the weight and leaf water 45%.
- *Example:* the usual steps are about ±0.03 and the latest NDVI change is −0.12, giving z ≈ −4. With z_NDMI ≈ −3: score = 50 − 15 × (−2.2 − 1.35) ≈ **100**, a very unusual decline.

**Change score, method B** (fewer than 7 scenes, so there is no history to judge against): a simple size rule.

- `Score = 50 + 12 × (−100 × ΔNDVI × 0.55 − 100 × ΔNDMI × 0.45)`, clipped to 0–100.
- *Example:* ΔNDVI = −0.02 and ΔNDMI = −0.01 give 50 + 12 × (1.1 + 0.45) ≈ **69**.
- The caption tells you which method was used.

**The badge:**

- **DECLINE** if NDVI or NDMI fell by 0.05 or more.
- **UNUSUAL DECLINE** if, in addition, the lower of the two z-scores is −2 or below.
- **GREENING** if NDVI rose by 0.05 or more.
- **STABLE** otherwise.

**Chart and map:**

- The chart shows the NDVI (green) and NDMI (blue) lines over time.
- The map has two layers:
  - NDVI of the latest scene (red = bare, green = dense);
  - NDVI change since the previous scene (brown = loss, teal = gain). Switch it on from the layer menu.

**How to read it:** a DECLINE in **March–April or October–November** is usually **harvest**, not trouble. The same drop in mid-January deserves a look.

---

## 3. 🌱 Field Scanner (with the AI Field Brief)

**Question:** What does the latest imagery say about **one field**, and what should I check?

**Data:** Sentinel-2, the Impact Observatory cropland map, and Open-Meteo weather at the field's centre.

**End-to-end run:**

1. **Draw your field** with the polygon or rectangle tool on the satellite map.
   - **Field area** = the polygon's area on the curved Earth, in ha (1 ha = 10,000 m² ≈ 2.47 acres).
   - Above 2,500 ha you are warned that the whole area gets one median.
2. Pick the **crop** you know is there. This is only quoted in the brief; it does **not** change any calculation.
3. Click **Scan field**. The app:
   - finds the **most recent** image in the history window where **your field's own pixels** are ≥ 65% clear;
   - finds the **previous** clear image, 4–75 days earlier;
   - reads both at 10 m, masks cloud, and computes NDVI and NDMI per pixel;
   - takes medians over clear cropland pixels inside the field (or all clear pixels if there are fewer than 50 cropland pixels);
   - fetches 14 days of weather and computes the water-stress signal (see module 4);
   - writes the brief.

**The numbers:**

| Metric | Calculation |
|---|---|
| **NDVI / NDMI with arrows** | Latest-scene medians and their change from the previous clear scene. |
| **Water-stress signal** | The module 4 formula, using this field's NDMI. |
| **Clear pixels** | How many pixels went into the medians. Hover to see whether they were cropland pixels or all clear pixels. |
| **Caption** | Scene date, % clear, previous scene date, and your crop as reported. |

**The Field Brief** ("AI" here means rule-based logic, not a chatbot, so it **cannot invent numbers**). Each sentence is built from a measured value:

- **What the data shows:**
  - the NDVI value with its plain-language meaning (table in 0.2);
  - NDMI as low, moderate or high;
  - how NDVI changed and over how many days: *fell* if −0.05 or more, *rose* if +0.05 or more, otherwise *held steady*;
  - 14-day rain versus ET0, and the surplus or deficit;
  - soil moisture at 9–27 cm depth;
  - the stress score.
- **What to check:**
  - If NDVI fell in March–April or October–November, it notes this is often harvest.
  - If NDVI fell at other times, it says to walk the paler patches and check for pests, disease, lodging, waterlogging or missed irrigation.
  - If stress is 60 or more, it says to check irrigation and soil by hand.
  - Otherwise it says to keep the normal scouting round.
- **Limits:** indices are not a diagnosis; the weather is a ~10 km model; edge pixels mix.

**Map:** the NDVI of the latest scene, plus the change layer and your field outline.

**Example:** "On 2026-03-10 the median NDVI was 0.71 over 1,850 clear cropland pixels: a dense green canopy. NDVI fell by −0.09 since 2026-02-23 (15 days earlier)." Because this is March, the brief suggests confirming whether harvest has begun.

---

## 4. 💧 Water / Moisture Signals

**Question:** Is the canopy dry, and has recent weather made up for it?

**Data:** Sentinel-2 NDMI (the latest clear scene over the whole AOI) and Open-Meteo (last 14 complete days, India time; today is excluded because it is still partly forecast).

**End-to-end run:** click **Check water signals**. The app scans the AOI (as in module 3, without a drawn field), fetches the weather for the AOI centre, then combines the two.

**The numbers:**

| Metric | Calculation |
|---|---|
| **Rain, last 14 days** | Sum of the daily rain totals. |
| **Reference ET0, 14 days** | Sum of the daily FAO-56 ET0. |
| **Deficit (ET0 − rain)** | Positive means the crop's demand exceeded rainfall. |
| **Water-stress signal (0–100)** | See below. |
| **Soil-moisture chart** | Current modelled soil moisture at 0–1, 3–9, 9–27 and 27–81 cm. |
| **Rain/ET0 chart** | Daily bars (rain) and a line (ET0). |
| **NDMI map** | Brown = dry, teal = wet. Bare soil also reads dry. |

**The water-stress formula:**

- **Canopy dryness** = (0.5 − NDMI) / 0.7, kept between 0 and 1. NDMI 0.5 (very moist) gives 0, and NDMI −0.2 (very dry) gives 1.
- **Water-balance term** = (ET0 − rain) / 60 mm, kept between 0 and 1. A deficit of 60 mm or more counts as fully dry.
- **Signal** = 100 × (0.6 × canopy dryness + 0.4 × water-balance term). Leaves count for 60% and weather for 40%.

**Example:**

- NDMI = 0.15 gives canopy dryness = 0.35 / 0.7 = **0.50**.
- Rain 5 mm and ET0 45 mm give a deficit of 40 mm, so the balance term = 40 / 60 = **0.67**.
- Signal = 100 × (0.30 + 0.27) = **57**. That is just below the **60** at which the brief asks for a hand check.

**If the weather fetch fails,** the stress signal shows "n/a" rather than assuming zero rain.

**Limits:** this is an inspection signal, not an irrigation prescription. Freshly irrigated fields read wet regardless of the weather.

---

## 5. 📡 SAR + Optical Fusion

**Question:** Do two very different sensors **agree** about the latest change? If they do, the change is more likely real.

**Data:** Sentinel-2 (latest and previous clear scenes) and Sentinel-1 RTC (latest pass and the previous pass on the **same orbit**).

**Why "same orbit":** radar brightness depends on the viewing angle. Comparing passes from different orbits is like judging whether someone lost weight from one photo taken from the left and one from the right. KhetOS compares only passes on the same relative orbit.

**End-to-end run:**

1. **Optical side:** the same scan as module 4, giving ΔNDVI between the two clear scenes.
2. **Radar side:**
   - Find the newest Sentinel-1 pass (last 45 days) that covers at least 90% of the AOI.
   - Find the previous pass on the same relative orbit, 5–60 days earlier.
   - Read VV and VH, **averaging 10 m pixels into 30 m** to reduce "speckle" (radar's natural grainy noise).
   - Take medians over cropland pixels (at least 30 needed).
   - Convert to dB and compute ΔVH and ΔRVI.
3. **Classify each sensor's direction:**
   - Optical: *down* if ΔNDVI ≤ −0.05, *up* if ≥ +0.05, otherwise *flat*.
   - Radar: *down* if ΔVH ≤ −1 dB, *up* if ≥ +1 dB, otherwise *flat*.
4. **Verdict:**
   - both down: **AGREE · vegetation decline**;
   - both up: **AGREE · vegetation growth**;
   - both flat: **AGREE · stable**;
   - different directions: **DISAGREE · verify on the ground**;
   - a scene is missing: **INCOMPLETE**.

**The numbers:**

| Metric | Meaning |
|---|---|
| **Optical: NDVI change** | Latest minus previous clear Sentinel-2 median. |
| **Radar: VH change (dB)** | Latest minus previous same-orbit VH median. |
| **Radar vegetation index change** | ΔRVI, where RVI = 4 × VH / (VV + VH). |
| **Optical-radar gap (days)** | Days between the latest optical and latest radar dates. A bigger gap means a weaker comparison. |
| **Table** | Every date, value, orbit and pixel count used. |

**Examples:**

- **Harvest:** NDVI −0.30 and VH −2.5 dB, so AGREE · decline. Believable.
- **Rain on the leaves:** NDVI flat, but VH +1.5 dB because wet leaves echo more strongly. This gives DISAGREE, and a field visit would explain it.

---

## 6. ⚠ Scouting Queue (Isolation Forest)

**Question:** Which parts of the area should I visit **first**?

**Data:** Sentinel-2 (latest and previous clear scenes, 10 m) and the Impact Observatory cropland map.

**End-to-end run (scope: "Area of interest"):**

1. Get the latest and previous clear scenes (as in module 4).
2. **Cut the AOI into square cells** of 400 m (16 ha). The cell size grows if needed to keep at most 20 × 20 cells.
   - *Example:* a 3 km AOI becomes about 8 × 8 = 64 cells.
3. **For each cell compute:**
   - cropland %;
   - clear % in each scene;
   - median NDVI and NDMI;
   - ΔNDVI and ΔNDMI since the previous scene.

   Medians use clear cropland pixels when there are at least 10.
4. **Screen out cells:**
   - under 30% cropland: **NOT CROPLAND**;
   - under 50% clear in either scene: **INSUFFICIENT DATA**.
5. **Score each remaining feature** (NDVI, NDMI, ΔNDVI, ΔNDMI) as a robust z-score **against the other cells on the same day**.
   - *Why:* weather, season and sowing date affect all neighbouring cells alike. Comparing cells with each other cancels that out, so what remains is "this cell is behaving differently from its neighbours".
6. **With 12 or more eligible cells, run an Isolation Forest** (200 trees) on those z-scores.
   - *How it works:* it repeatedly splits the cells with random yes/no cuts ("Is z_ΔNDVI below −0.7?"). An odd cell gets separated from the crowd after very few cuts; ordinary cells take many.
   - *Analogy:* in a game of "20 questions" over a class photo, a student wearing a red hat among blue uniforms is found in one question.
   - Cells isolated unusually fast are **outliers**. The forest also gives an **anomaly strength**; higher means odder.
7. **Stress direction:** `stress_z` = the lower of z(ΔNDVI) and z(ΔNDMI). If there is no previous scene, the NDVI and NDMI levels are used instead. Negative means worse than the neighbours.
8. **Priority:**
   - **With 12 or more cells:**
     - **HIGH** = outlier **and** stress_z ≤ −1.5. It is odd *and* declining.
     - **WATCH** = outlier **or** stress_z ≤ −1.5.
   - **With fewer cells** (no forest): **HIGH** if stress_z ≤ −2.5, **WATCH** if ≤ −1.5.
   - All other eligible cells are **LOW**.
9. **Sort:** HIGH first, then by worst stress, then by anomaly strength.

**Display:**

- **Counts** of HIGH, WATCH, LOW, INSUFFICIENT DATA and NOT CROPLAND.
- A **coloured cell map**.
- A **table** whose "Why" column spells out the evidence, e.g. "NDVI 0.41 (z −2.3); ΔNDVI −0.18 since 2026-02-20 (z −3.1); Isolation-Forest outlier".
- Downloads: CSV and GeoJSON (for phone navigation).

**Scope "Near the Nakatiya":**

- Choose a reach and a distance (100, 250, 500 or 1000 m).
- The analysis runs only inside that river corridor, with **250 m cells** (up to 60 per side).
- Each cell also gets its distance to the centreline, and the table shows only the HIGH and WATCH cells.

**Meaning:** "look here first", not "something is wrong here". An outlier can be an early-sown or freshly harvested field.

---

## 7. 🤖 Ask the Map

**Question:** Answer a plain-language question by running the right analysis.

**How it works:** a **rule-based keyword parser**. It is not a chatbot, it works offline and needs no API (Application Programming Interface) key.

1. It lower-cases the question and extracts:
   - **distances** ("500 m", "0.5 km", "100m");
   - **years** (1984 up to this year);
   - a **reach** (words such as *urban/city/Dohra/Bisalpur*, *Bhojipura/upstream*, *downstream/near the confluence*).
2. It routes by keywords, checking in this order:

| If the question mentions… | Action | Default width |
|---|---|---|
| Qila / Kila | Shows the unverified Qila candidate map | – |
| river words **and** field/farm/crop | Scouting cells near the river (module 6, near scope) | 500 m |
| flood / waterlog / overflow / water expansion | Flood watch (8B) | 500 m |
| river + course/origin/source/confluence/length | Course summary (as on the Overview) | – |
| river + construction/alert/clearing/kiln/dumping/recent | Construction alerts (8D), 90-day window | 250 m |
| river + vegetation/riparian/green/tree | Riparian health (8C) | 100 m |
| river + two or more distances | Buffer ladder (9A) | the distances given |
| river + over the years/timeline/history/trend, or 3 or more years | Timeline (9B) | 100 m |
| any other river question | Change flags (8A) | 250 m |
| water/moisture/irrigation/dry/rain | Water signals (module 4) | your AOI |
| scout/queue/priority/visit | Scouting queue (module 6) | your AOI |
| radar/SAR/fusion/optical | Fusion (module 5) | your AOI |
| change/anomaly/NDVI/decline/stress | Change radar (module 2) | your AOI |
| field/scan/crop/farm | Field scan (module 3) | your AOI |
| anything else | Help with examples; **it never guesses** | – |

3. **Distance limits:** kept between 10 m and 2,000 m.
4. **Year rules:**
   - Years are clamped to the Landsat record (1990 onwards) and to the latest complete rabi season.
   - One year means "from that year to now".
   - No year means 2017 to now.
   - The blue box tells you exactly what was adjusted.

**Example:** "Show built-up change along the Nakatiya since 2016 within 100 m" becomes **Change flags, 2016→2026, 100 m, whole river**. The result appears exactly as in module 8A.

---

## 8. 🌊 Nakatiya River Observatory

**Top controls:** a **Reach** (or the whole river) and a **corridor half-width**: 10, 25, 30, 50, 100, 250, 500 or 1000 m.

- **30 m is the reported Master Plan green belt.** It is not confirmed in the plan document, so treat it as a reference line only.

**How the corridor is built:** take the centreline within the reach radius of the anchor (the anchor is snapped onto the river first), then buffer it by the half-width.

### Built-up slider (1984 → today)

**Question:** How much of this corridor was built up in year X?

**How it works:**

1. For the chosen year, fetch the matching validated map on a 10 m grid:
   - 1985–2015: WSF Evolution, where a pixel counts if **settled by that year**;
   - 2017 onwards: Impact Observatory, pixels labelled *built*.
2. **Built-up %** = built pixels inside the corridor ÷ all pixels inside the corridor × 100.
3. Special years:
   - 1984 shows the 1985 map (whose "1985" means 1985 or earlier);
   - 2016 has no map, so it shows 2015;
   - years after 2025 show the 2025 map.

   The label under the number always says which map is being shown.

**Map:** built pixels in red over satellite imagery.

**Caution:** the two maps define "built" differently. WSF says "settled", which includes gardens between houses; Impact Observatory says "built area". Compare years **within** one product, not across the 2015→2017 jump.

### Tab A: Change flags (two-season comparison)

**Question:** Between rabi season A and rabi season B, did built-up land grow, and did water or vegetation shrink?

**End-to-end run:** choose "From" (e.g. 2017) and "To" (e.g. 2026), then click Run. Several jobs run in parallel:

**1. Landsat water and vegetation.** For each end, the seasons *year − 1, year, year + 1* are composited, each kept on its own side of the midpoint (so 2017 uses 2016–2018, and 2026 uses 2025 and 2026).

For each season:

- **Search:** Landsat images from November to April.
- **Screen:** a scene must have at least 30% of the corridor clear.
- **Choose:** December–March images first. November/April images and Landsat 7 "SLC-off" scenes are added only if there are too few. **SLC (Scan Line Corrector)** failed in 2003, so those images have striped gaps. At most 8 images are kept, spread through the season.
- **Per pixel, two values:**
  - the **greenest NDVI** of the season: did it get green at *any* point? This is insensitive to exact sowing and harvest dates;
  - the **median MNDWI**: water in at least half the clear looks?
- **Shares of the corridor:**
  - **water %** = median MNDWI > 0.05;
  - **vegetated %** = not water, and greenest NDVI ≥ 0.50;
  - **non-green %** = not water, and greenest NDVI < 0.40. This covers buildings, roads, kilns, sand and fallow alike, so it is *indicative only*.
- **Quality grade:**
  - **poor:** no clear mid-January–March scene (vegetation would be under-counted), or under 60% of the corridor observed;
  - **fair:** SLC-off scenes only, fewer than 3 scenes, a season widened to November–April, or 60–80% observed;
  - **good:** otherwise.

**Each end** = the **median** of its **usable** seasons: not poor, and at least 2 peak-season scenes.

- *Why not just one season?* KhetOS measured that single seasons swing by a median of 8–10 pp, and up to 48 pp, between neighbouring years (sowing dates, cloud). Medians of 3 seasons swing at most about 14 pp.

**2. Built-up change:**

- **Settled area (pp):** WSF settled % at the end of the overlap with 1985–2015, minus the % at its start.
- **Built area (pp):** Impact Observatory built % over the overlap with 2017–2025.
- Periods that neither map covers (2015→2017, 2025→now) are listed as caveats.

**3. JRC water history (1984–2020), inside the corridor:**

- **Ever water:** % of pixels ever seen as water.
- **Water most of the time:** % with water in at least 50% of observations.
- **Water lost:** % whose permanent or seasonal water disappeared. The hover text gives this as a share of the ever-water area.
- **New water:** % that became water.

**Flags:**

| Flag | Rule |
|---|---|
| SETTLEMENT GROWTH | Settled area rose by more than 3 pp |
| BUILT-UP GROWTH | Built area rose by more than 3 pp |
| VEGETATION LOSS | Vegetated share fell by 15 pp or more* |
| WATER FOOTPRINT DECLINE | Water share fell by 3 pp or more* |

\* Only when **both ends rest on 2 or more usable seasons**, and the corridor is at least **45 m** wide (1.5 Landsat pixels; narrower bands fall inside the typical error of the drawn river line).

**Overall badge:**

- **HIGH:** two or more *kinds* of change. Settlement and built-up growth count as one kind, "built-up".
- **WATCH:** one kind.
- **STABLE:** none.
- **NO DATA:** nothing could be measured.

**Why these thresholds:** each sits just above the random wobble measured when nothing really changed. That keeps false alarms down.

**Example:** 100 m corridor, urban reach, 2017→2026: built area +6.2 pp, vegetated −4 pp, water −1 pp. The flag is **WATCH (BUILT-UP GROWTH)**. The evidence tables show which seasons and images were used.

### Tab B: Flood / water watch

**Question:** Is open water spreading in the corridor right now, even under monsoon cloud?

**Run:**

1. Choose a half-width (250–2000 m).
2. The app takes the latest Sentinel-1 pass (last 45 days) and the previous pass on the same orbit.
3. VV is averaged to 30 m.
4. **Water-like %** = pixels with VV below −18 dB ÷ corridor pixels.

**Numbers:** water-like % for the latest and previous passes, and the **change** in pp.

**Badge:**

- **WATER EXPANSION** at +3 pp or more;
- **WATER RECESSION** at −3 pp or less;
- otherwise **NO SIGNIFICANT CHANGE**;
- **SINGLE PASS ONLY** if there is no earlier pass to compare.

**Caution:** flooded fields count as water. Very smooth tarmac or dry sand can also look dark.

### Tab C: Riparian health

**Question:** Is the vegetation along the banks greener or thinner than a year ago?

**Run:**

1. Choose the year's **1 January–31 March** window. It is compared with the same window a year earlier.
2. In each window, up to 8 Sentinel-2 looks are used. Cloud is masked pixel by pixel, so partly cloudy images still contribute their clear parts.
3. Per pixel, take the **greenest NDVI**.
4. **Vegetated** = greenest NDVI ≥ 0.50, counted only on pixels seen clear in **both** windows.

**Numbers:**

| Metric | Meaning |
|---|---|
| Vegetated, this year (with arrow) | % of compared pixels; the arrow is the change in pp |
| Vegetated, a year ago | the same for the earlier window |
| Corridor compared | % of corridor pixels clear in both windows |
| Clear looks | images used this year / last year |
| Caption | tree cover % (Impact Observatory 2025) and the median change in greenest NDVI |

**Badge:**

- **GREENNESS DECLINE** at −10 pp or less;
- **GREENING** at +10 pp or more;
- **STABLE** in between;
- **TOO FEW CLEAR LOOKS** if under half the corridor could be compared.

**Why only January–March:** KhetOS measured that matched January–March windows move at most about 4 pp year on year. Summer and monsoon windows move 7–18 pp just from sowing dates and cloud, which would bury any real signal.

### Tab D: Construction alerts

**Question:** Where did land that was green a year ago **stay bare** through the recent period?

**Run:**

1. Choose a window of 60, 90 or 120 days ending today. It is compared with the same dates last year.
2. **Candidate pixel** = greenest NDVI ≥ 0.50 a year ago **and** < 0.30 throughout the recent window, with at least 2 clear recent looks.
3. **Wet check:** if the candidate's median SWIR reflectance is below 0.08, it is water or waterlogged soil, labelled **NEW WATER / WET** (blue). Otherwise it is **BARE / BUILT CANDIDATE** (red).
4. Touching candidate pixels are joined into **patches**. Patches under 0.1 ha (10 pixels) are dropped. The 60 largest are kept, numbered P01, P02 and so on, each with its distance to the centreline.

**Numbers:**

| Metric | Meaning |
|---|---|
| Bare / built candidates (ha) | pixels × 0.01 ha |
| New water / wet (ha) | the same, for the wet patches |
| Corridor seen | % with enough clear looks in both windows |
| Clear looks | images used in each window |

The badge reads "**N CANDIDATE PATCH(ES)**" or "NO CANDIDATES".

**Caution:** construction, clearing, earthworks, brick kilns, dumping and fallow fields all look the same from orbit. Each patch needs an imagery or field check.

- In a **monsoon** window (any window that touches July–September) a warning is shown: flooded or late-sown fields can stay below the green threshold.

**Example:** "P01 · BARE / BUILT CANDIDATE · 0.84 ha · 35 m from the centreline". Download the GeoJSON, open it on a phone, and go and look.

---

## 9. 🏘 Land Change / Riparian Change

This page uses the same machinery as tab A, arranged to answer two further questions.

### Tab A: Buffer ladder

**Question:** Is the change concentrated close to the river?

**How it works:**

- The same comparison as Change flags is run for several nested widths at once. The default is 10, 25, 30, 50 and 100 m.
- Images are **read once** for the widest band, and each narrower band is summarised from the same pixels. This is faster and keeps the bands consistent.

**Table columns:**

- half-width and area in ha;
- Landsat pixels available;
- settled pp (WSF);
- built-up pp (Impact Observatory);
- vegetation pp, water pp and non-green pp (indicative);
- the flag and the reason.

**How to read it:**

- If built-up growth is +9 pp at 10–30 m but +3 pp at 100 m, construction is hugging the bank.
- Bands under 45 m show Landsat numbers **without flags**, because they are narrower than 1.5 Landsat pixels. Trust the 10 m land-cover column there.

### Tab B: Timeline since 1990

**Question:** What is the long-run story, and is vegetation genuinely trending?

**Run:**

1. Choose a width (default 100 m) and the seasons. The default is 1991, 1994, 1999, 2003, 2009, 2014, 2020 and the latest complete rabi season (2026 at the time of writing). These years were picked because each has enough clear peak-season Landsat images.
2. The app computes:
   - the Landsat season shares, each with its quality grade;
   - WSF settled % for every year 1985–2015;
   - Impact Observatory class shares for every year 2017–2025.

**Charts:**

- Landsat water, vegetated and non-green lines. Crosses mark seasons left out as unusable.
- Built-up share: two separate lines, one per product.
- Land-cover classes by year.

**The trend numbers:**

- **Theil–Sen slope:**
  - Take every pair of usable seasons and compute the slope between them, i.e. (change in vegetated %) ÷ (years apart).
  - The trend is the **median** of all those slopes.
  - *Analogy:* asking every pair of witnesses and taking the middle answer, so one bad season cannot skew it.
  - It is shown per decade, e.g. **−5.0 pp/decade**.
- **Fitted change:** the slope × the years spanned, e.g. 1991→2026 gives −17.5 pp.
- **Mann–Kendall p-value:**
  - For every pair of seasons, note whether the later one is higher (+1) or lower (−1).
  - If the ups and downs roughly cancel, there is no trend.
  - The p-value is the chance of seeing an imbalance this large by pure luck. **p < 0.05** (under 5%) counts as significant.
- **Usable seasons:** at least 5 are needed.

**Badge:** **SIGNIFICANT DECLINE**, **SIGNIFICANT INCREASE**, **NO SIGNIFICANT TREND** or **TOO FEW SEASONS**.

**Caveats shown in the app:**

- Sensor generations changed in 1999 and 2013. The bands are harmonised, but a few points of difference could remain.
- Do not join the WSF and Impact Observatory lines.

---

## 10. 📈 River Water Watch

**Question:** How much water does the Nakatiya carry, through the year and over the decades, and what can we *measure* ourselves?

**Read this first.** No gauge has ever measured the Nakatiya. Every flow on this page comes from a global computer model, **GEOGLOWS** (Group on Earth Observations Global Water Sustainability). It turns ERA5 rain and runoff (the European Centre for Medium-Range Weather Forecasts' weather re-analysis) into river flow along a mapped river network, every day since 1940. It knows about weather and the lie of the land. It does **not** know about the city, sewage, irrigation, canals, dams or groundwater pumping. The page says so in a banner at the top.

### Tab A: Modelled flow

1. Pick a point on the river. There are four model stream segments: above the city (km 10–21 of the mapped river), entering the city (km 22–26), below the city (km 45–52) and at the Ramganga (the whole river, 371.5 km²).
2. **Four numbers** for 1991–2020, the current 30-year climate normal:
   - **Yearly water**: the mean volume in million cubic metres, and the same volume as a depth of water over the catchment (in the tooltip).
   - **Mean flow** in m³/s. The middle day is far lower than the mean, because a few flood days carry much of the water.
   - **Dry-year water (90% dependable)**: the volume matched or beaten in 9 years out of 10. *Analogy:* the water you can count on even in a poor year.
   - **Share in June–September**: how much of the year's water comes in the monsoon.
3. **Water carried each year**: one bar per complete calendar year. The model's first two years (1940–41) are left out as a precaution, because GEOGLOWS does not say how its 1940 run was started.
4. **The average year**: the mean flow of each month over 1991–2020, with a band from the 10th to the 90th percentile (a dry year to a wet year).
5. **Is the river changing?** For the yearly flow, each season, the lowest 7-day flow and the highest day, from 1985 (the start of the satellite record) and from 1950:
   - the **Theil–Sen slope** as a percentage change per decade (the median of the slopes between every pair of years, so one odd year cannot skew it);
   - the **Mann–Kendall p-value**, adjusted for persistence (a wet year tends to follow a wet year, which makes plain tests find trends too easily);
   - a plain reading: **clear** (p under 0.01), **likely** (under 0.05), **weak sign** (under 0.1) or **no trend detected**.
6. **Next 15 days**: a button loads GEOGLOWS's own ensemble forecast (51 weather runs) for the chosen point. It is fetched live, so it needs the GEOGLOWS service to be up, and an error is shown if it is not.
7. **Our rain-forecast model (experimental)**, whole river only: a machine-learning model (gradient-boosted trees) that forecasts the flow 1, 3 or 7 days ahead from recent flow, rain, evaporation and soil moisture plus the ECMWF rain forecast. It learned from 1942-2023 and is shown on March 2024 onward, days it never saw, with the rain forecasts as they were really issued. At 3 days its score rises from 0.30 without a rain forecast to 0.59 with one (Nash-Sutcliffe efficiency, 1 is perfect). It is judged against GEOGLOWS, not the river, and is not run live because its inputs arrive about a week late.
8. **Downloads**: the full **historical workbook** (Excel, 21 sheets: annual, water-year, seasonal, monthly and daily tables, average year, dependable flows, flow-duration curves, trends, decades, rain and runoff, a land-cover scenario, satellite widths, checks, calculators and limits) and the yearly table as CSV.

**Typical run (whole river):** about 113 million m³ in an average year, 72% of it in June–September, and about 51 million m³ in a dry year (9 in 10 dependable).

**Reading the trends honestly.** Since 1985 the *modelled* dry-season flow shows a clear rise. That goes with falling ERA5 evaporative demand, not with more rain. Because the model cannot see built-up land, sewage or pumping, it can neither confirm nor rule out the construction effect this project is studying.

### Tab B: Satellite width

**Question:** What does the satellite say about the channel itself?

- For three reaches (upper 30.1 km, urban 14.5 km, lower 14.6 km) and clear Sentinel-2 dates in 2018–2025, each 10 m pixel is split into water and land by **sub-pixel unmixing**, and the water fractions add up to an **open-water width** in metres.
- Dates whose water signature was borrowed from another date, or contaminated, are marked unreliable. The toggle hides them by default.
- **Width is not flow.** It says when the channel is wide or narrow; depth and speed are unknown. Noise is about ±0.5 m, so read the pattern between seasons, not one date.

### Tab C: Field readings

**Question:** What is the river really carrying today?

This is the only real measurement of the Nakatiya's flow the project can gather. It uses the **float method**:

1. Measure the **water width**.
2. Measure **depths** at equal spacing across the river (leave out the two banks).
3. Drop a float (an orange or a stick) and time it over a known **distance**, several times.
4. The form works out **area × surface speed × a coefficient** (0.85 by default; 0.8 for a rough, shallow bed, 0.9 for a smooth, deep one), and shows the flow in m³/s and million litres a day, with a range for coefficients 0.8 to 0.9.

The cross-section uses the trapezoid rule: the depths joined to zero depth at both banks. **Nothing is stored on a server.** Readings live in the browser session: download the field-log CSV to keep them, and upload it later to add the rows back. An uploaded row is recomputed from its raw readings, so an edited file cannot carry numbers that do not follow from them.

*Worked example:* width 6 m, depths 0.3, 0.5, 0.6 and 0.4 m, float distance 10 m, times 14, 15 and 13 s. Area 2.16 m², surface speed 0.714 m/s, flow 1.31 m³/s (1.23 to 1.39 for the coefficient range).

### Tab D: Check and limits

- **Check against the gauged neighbour.** The Ramganga at Chaubari (Bareilly) has a Central Water Commission gauge, and WWF-India and INRM (the Institute of Natural Resources Management) built a SWAT (Soil and Water Assessment Tool) model calibrated to it. The table compares GEOGLOWS with that model for 1973–2011. GEOGLOWS runs about twice the gauge-calibrated river in the monsoon and up to three times in the pre-monsoon, and the gap differs by season and by dependability: **never scale the Nakatiya's flows by one factor**.
- **What the page cannot tell you**: no gauge data; no city, sewage, canals or aquifer in the model (so no construction effect); the model stream starts about 25–30 km below the mapped head; the flows are not bias-corrected; and a machine-learning forecast trained on them would copy the model, not the river.

---

## Summary: which data answers which question

| Question | Best source in KhetOS |
|---|---|
| Is my crop green or thirsty **now**? | Sentinel-2 NDVI and NDMI (modules 2–4) |
| Is the change real, even under cloud? | Sentinel-1 radar compared with optical (module 5) |
| Where do I walk first? | Scouting queue, Isolation Forest (module 6) |
| Did building spread near the river? | WSF Evolution (1985–2015) and Impact Observatory (2017–2025), **not** Landsat |
| Did water or vegetation shrink over decades? | Landsat rabi composites, 1990–2026, with Theil–Sen and Mann–Kendall trends |
| Is it flooding right now? | Sentinel-1 VV below −18 dB |
| What changed in the last 90 days? | Sentinel-2 construction alerts |
| How much water does the river carry, and how has that changed? | GEOGLOWS modelled flow, 1940 onward (module 10), checked against your own float readings |

Every number traces back to a measurable rule plus a caveat, and every river output is a signal to verify, never a legal finding. See [AUDIT.md](AUDIT.md) for the calibration evidence behind the thresholds and [DATA_PROVENANCE.md](DATA_PROVENANCE.md) for licences.
