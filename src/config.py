from pathlib import Path

APP_TITLE = "KhetOS · River + Agriculture Intelligence"
NAV_ITEMS = [
    "🌍 Rohilkhand Overview",
    "🛰 Change Radar",
    "🌱 Field Scanner",
    "💧 Water / Moisture Signals",
    "📡 SAR + Optical Fusion",
    "⚠ Scouting Queue",
    "🤖 Ask the Map",
    "🌊 Nakatiya River Observatory",
    "🏘 Land Change / Riparian Change",
]
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Farmland on the Nakatiya's lower reach, ~13 km south of Bareilly city
# (79% cropland in ESA WorldCover 2021 inside the default 0.015° half-width box).
DEFAULT_AOI = (79.436491, 28.250489)  # lon, lat
REGION_BBOX = (78.95, 28.00, 79.80, 28.95)

# Reach anchors snapped to the OSM "Nakatia Nadi" centreline: (lon, lat), default radius km.
# The urban reach is where the 2026 encroachment reports cluster (Dohra Road, Bisalpur Road).
NAKATIYA_REACHES = {
    "Urban reach · Dohra Rd–Bisalpur Rd": ((79.46913, 28.37341), 4),
    "Upper reach · Bhojipura side": ((79.50512, 28.46892), 6),
    "Lower reach · Ramganga confluence": ((79.46740, 28.16052), 6),
}
NAKATIYA_POINT = NAKATIYA_REACHES["Urban reach · Dohra Rd–Bisalpur Rd"][0]
NAKATIYA_CONFLUENCE = (79.48371, 28.13537)  # OSM junction with the Ramganga

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Attribution lines shown in the app and appended to every exported report.
ATTRIBUTIONS = {
    "Sentinel-2 L2A / Sentinel-1 RTC": "Contains modified Copernicus Sentinel data, processed by ESA; "
                                       "accessed through Microsoft Planetary Computer",
    "Landsat Collection 2 Level-2": "Landsat imagery courtesy of the U.S. Geological Survey",
    "Impact Observatory land cover": "Impact Observatory, Microsoft and Esri 10 m annual land use/land cover "
                                     "(2017-2023 via Planetary Computer, later years via Esri Living Atlas), CC BY 4.0",
    "World Settlement Footprint Evolution": "Copyright DLR; WSF Evolution v1, Marconcini et al. (2021), CC BY 4.0",
    "JRC Global Surface Water": "EC JRC / Google, Pekel et al. (2016), Nature 540, 418-422",
    "River geometry": "(c) OpenStreetMap contributors, ODbL 1.0",
    "District boundaries": "geoBoundaries gbOpen IND ADM2, ODbL 1.0",
    "Weather": "Weather data by Open-Meteo.com, CC BY 4.0",
}
DISCLAIMER = ("Remote-sensing outputs are change signals for follow-up. They are not legal, cadastral, "
              "encroachment or agronomic determinations; verify with field and revenue-record evidence before acting.")

# Reported context for the Nakatiya and the Qila (Kila): (date, outlet, paraphrase, url). Press reports and a plan
# listing, paraphrased; KhetOS has not verified any of them.
NEWS_CONTEXT = [
    ("21 Jun 2026", "ETV Bharat",
     "Feature on the Qila: encroachment, factory effluent and city sewage are described as shrinking it into a drain.",
     "https://www.etvbharat.com/en/bharat/qila-river-in-bareilly-gasps-for-survival-enn26062104423"),
    ("30 Aug 2026", "Amar Ujala",
     "Building plans in the Ramganga floodplain are reported to need a no-objection certificate before approval.",
     "https://www.amarujala.com/uttar-pradesh/bareilly/building-plans-in-the-ramganga-floodplain-will-not-be-"
     "approved-without-an-noc-in-bareilly-2026-08-30"),
    ("31 Aug 2026", "Amar Ujala",
     "About 6,000 houses are reported on encroached banks of the Nakatiya and the Kila; the report places the "
     "Nakatiya's source at Dehnagar (Deenagar) village in the Baheri area.",
     "https://www.amarujala.com/uttar-pradesh/bareilly/six-thousand-houses-have-been-built-by-encroaching-upon-the-"
     "banks-of-the-nakatia-and-kila-rivers-bareilly-news-c-4-bly1015-956598-2026-08-31"),
    ("11 Sep 2026", "Amar Ujala",
     "Encroachment is reported to have narrowed the Kila into a drain, with water entering several colonies.",
     "https://www.amarujala.com/uttar-pradesh/bareilly/encroachment-has-turned-the-kila-river-into-a-drain-water-"
     "has-entered-several-colonies-bareilly-news-c-4-1-agr1043-964200-2026-09-11"),
    ("26 Sep 2026", "Amar Ujala",
     "The municipal corporation is reported to be checking building plans along the riverbanks and identifying "
     "owners who have not submitted records.",
     "https://www.amarujala.com/uttar-pradesh/bareilly/municipal-corporation-scrutinizing-blueprints-of-buildings-"
     "along-the-riverbank-those-failing-to-submit-records-are-being-identified-bareilly-news-c-4-bly1015-974777-"
     "2026-09-26"),
    ("undated", "Amrit Vichar",
     "A campaign to revive the Nakatiya, launched by the state forest minister, with saplings planned along its "
     "banks.",
     "https://www.amritvichar.com/article/591582/bareilly-news--campaign-to-revive-bareilly-s-nakatia-river"),
    ("undated", "Amrit Vichar",
     "A plan to develop the Nakatiya under a new identity as the 'Nath Ganga'.",
     "https://www.amritvichar.com/article/593097/bareilly-news--nakatia-river-to-get-a-new-identity--to-be-"
     "developed-as--nath-ganga"),
    ("listing", "OpenCity (Bareilly Development Authority)",
     "Revised Master Plan 2031. The 30 m green belt along the Nakatiya and Kila reported in the press is not yet "
     "checked against the plan document, so the 30 m buffer here is a reference line only.",
     "https://data.opencity.in/dataset/bda-revised-master-plan-2031"),
]
