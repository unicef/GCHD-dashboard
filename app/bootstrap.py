"""GEE initialisation and static data shared across sections."""
import os
from core.ai import initialize_ai
from config.hazards import HAZARD_TOPICS, HAZARDS
from core.gee import build_core_images, get_country_names, initialize_gee


# ---------------------------------------------------------------------------
# GEE init (once at server start)
# ---------------------------------------------------------------------------
initialize_gee()
build_core_images()

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------
COUNTRY_NAMES  = get_country_names()
initialize_ai(COUNTRY_NAMES)
UN_CLEARMAP    = "https://geoservices.un.org/arcgis/rest/services/ClearMap_WebTopo/MapServer/tile/{z}/{y}/{x}"
with open(os.path.join(os.path.dirname(__file__), "credentials", "carto_api_key.txt")) as _f:
    CARTO_API_KEY = _f.read().strip()
CARTO_FALLBACK = f"https://basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png?key={CARTO_API_KEY}"
ESRI_SAT       = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
GEE_ATTR       = "Google Earth Engine / UNICEF"

# The map's opening view, and the view every section returns to when the user
# leaves it. Defined once so the initial render and the reset cannot drift.
GLOBAL_CENTER  = [10, 20]
GLOBAL_ZOOM    = 3
GLOBAL_VIEWPORT = {"center": GLOBAL_CENTER, "zoom": GLOBAL_ZOOM,
                   "transition": "flyTo"}
UN_ATTR        = "© United Nations Geospatial"
CARTO_ATTR     = "© OpenStreetMap contributors © CARTO"
ESRI_ATTR      = "© Esri, Maxar, Earthstar Geographics"
TOPIC_LIST     = list(HAZARD_TOPICS.keys())

# Ordered layers for the hazard list
MH_LAYERS  = ["Multi Hazard Count", "Multi Hazard Intensity"]
HAZ_LAYERS = [h["name"] for h in HAZARDS if h["name"] != "Pixel Based Hazard Score"]
ALL_LAYERS = MH_LAYERS + HAZ_LAYERS   # order matches pattern-match order in layout

# Analysis tab: one map slot per selectable hazard topic. Slots are declared
# once at layout time and filled lazily (only when a topic's legend eye is
# switched on), so a Compute never fans out into a tile request per topic.
ANALYSIS_TOPIC_SLOTS = len(HAZARD_TOPICS)
