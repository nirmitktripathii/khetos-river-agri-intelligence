# Bhoonidhi plan: what to get, in what order, and how

Bhoonidhi is the Indian Space Research Organisation's National Remote Sensing Centre (NRSC) data portal. The aim here is to find out, once you are logged in, which free Indian-satellite products help the river question: **has construction along the Nakatiya grown, and has less water followed?**

Status: waiting for you to register and log in. The access request to bhoonidhi@nrsc.gov.in is a **Gmail draft, not sent**; one placeholder remains (`[YOUR BHOONIDHI USER ID]`).

## Ground rules

- **You** register, log in and accept the terms. I never type passwords or tokens into a page. If we use the API, you put the token in a git-ignored `.env` in the repository root (never committed, never pasted into chat).
- I ask before **every download** (file name, source, size) and before accepting any licence text.
- Only products marked `Online = "Y"` can be fetched by the API; anything else is an order the portal processes, which you place.
- Limits to respect: 20 authentications an hour, 3 searches a second, 3 downloads at once.
- Area of interest: the Nakatiya corridor, box west 79.434, south 28.1354, east 79.5483, north 28.4924. Outputs are "change flags", never legal findings.

## What I will look for (and why)

| Priority | Product | Why it matters | Check at login |
|---|---|---|---|
| 1 | **LISS-IV** (Linear Imaging Self-Scanner, 5.8 m, from 2003) | Twice as sharp as Sentinel-2. Separates narrow riverside houses from fields, and gives water width to about a pixel. Three bands only (green, red, near-infrared): no shortwave infrared | Free for all users? Dates over the box, cloud cover, `Online` flag |
| 2 | **IRS-1C / 1D panchromatic** (black-and-white, 5.8 m, from 1995) | The only sharp picture of the river *before* most construction. Our Sentinel-2 record starts in 2015–2018 | Scenes over the box in 1995–2002; file size |
| 3 | **LISS-III** (23.5 m, from 2011 with shortwave infrared) and **AWiFS** (Advanced Wide Field Sensor, 56 m) | Long, regular record for moisture and wetness; LISS-III has the shortwave band LISS-IV lacks | Coverage; is it free |
| 4 | **Elevation** (CartoDEM) and any free terrain product | Flow direction, flood-prone strip, slope of the banks; the 30 m green-belt check needs a real ground model | Resolution, licence, whether free or government-only |
| 5 | **Synthetic-aperture radar** (RISAT) if offered | Sees water through cloud in the monsoon, when optical images fail | Availability and licence; may be restricted |
| Skip | **Cartosat** very high resolution | Free only to government bodies; do not order | Confirm it is listed as restricted |

Everything above is from earlier reading of the portal's public pages. **Confirm each row against the live catalogue**; I will report what I find, including rows that turn out wrong.

## Steps once you have logged in

1. You register and log in; tell me the catalogue is open. I read the collection list (`/data/collections`) or, if you prefer to stay in the browser, the catalogue screens, and fill the "Check at login" column.
2. **Catalogue survey (no downloads):** for each priority product, search the box for 1995–2026 and record count, dates, cloud cover, `Online` flag and size. Write it to `research/bhoonidhi/catalogue_survey.csv`.
3. **Pick the scenes** with you: one dry-season and one monsoon LISS-IV scene per year where clear, plus the earliest clear IRS-1C/1D scene. Total size goes to you first.
4. **Download** only what you approve, into `research/bhoonidhi/raw/` (git-ignored; large files never committed).
5. **Process:** cut each scene to the box; match its ground grid to the Sentinel-2 composites; re-run the sub-pixel water unmixing (`research/river_flow/unmix_probe.py`) with only the green, red and near-infrared bands to learn how much the sharper 5.8 m pixel helps; flag riverside built-up change between the 1995–2002 and current scenes.
6. **Put results where they count:** widths into `data/nakatiya_open_water_width.csv` style tables (with a `source` column), change flags into the Land Change page's method notes, and every product named in `docs/DATA_PROVENANCE.md` with its licence.

## API notes (if used)

Base address `https://bhoonidhi-api.nrsc.gov.in`; routes `/auth/token`, `/data/collections`, `/data/search` (a STAC, SpatioTemporal Asset Catalog, search) and `/download`. The token is requested with your Bhoonidhi user id and password, so **you** run that step or give me a ready token in `.env`. The script will read `BHOONIDHI_TOKEN` from the environment and never echo it.

## Questions to settle at login

- Which LISS-IV and IRS-1C/1D products are free to a private researcher, and under what attribution?
- Is the download volume capped per user or per month?
- Is there an order form for archive scenes that are not `Online`, and what is its turnaround?
- What licence covers the sharing of results built from the scenes in a public app?
