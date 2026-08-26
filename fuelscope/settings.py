import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; rely on shell env

_PACKAGE_DIR = Path(__file__).resolve().parent

GOOGLE_MAPS_API_KEY: str | None = os.getenv("GOOGLE_MAPS_API_KEY", None)

PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"

# Keep the mask as narrow as the classifier allows: the field mask determines
# which billing SKU tier the request lands in.
PLACES_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.types",
    "places.primaryType",
    "places.businessStatus",
])

# One request may ask for several types at once; Places treats includedTypes
# as a union, so this costs one call per tile rather than two.
SEARCH_TYPES = ["gas_station", "convenience_store"]

# Nearby Search caps results at 20 per request, so area coverage comes from
# tiling small circles rather than one big one.
MAX_RESULTS_PER_TILE = 20
TILE_RADIUS_M = 800.0

# Approximate Coquitlam Centre. Override per request.
DEFAULT_CENTER_LAT = 49.2781
DEFAULT_CENTER_LON = -122.7932
DEFAULT_SURVEY_RADIUS_M = 4000.0

# Guard against an accidental 500-call survey.
MAX_TILES_PER_SURVEY = 100

FIXTURE_PATH = str(_PACKAGE_DIR / "fixtures" / "tricities_sample.json")

# --- Map rendering -------------------------------------------------------
# A SEPARATE key from GOOGLE_MAPS_API_KEY. This one is served to the browser,
# so it must be HTTP-referrer restricted and scoped to Maps JavaScript API only.
# The Places key above stays server-side and is never sent to a client.
GOOGLE_MAPS_BROWSER_KEY: str | None = os.getenv("GOOGLE_MAPS_BROWSER_KEY", None)

# Advanced markers refuse to load without a map ID.
GOOGLE_MAPS_MAP_ID: str | None = os.getenv("GOOGLE_MAPS_MAP_ID", None)

# --- OpenStreetMap / Overpass -------------------------------------------
# Free, keyless, ODbL-licensed. The public endpoint is a donated community
# service: keep volume modest and identify yourself in the User-Agent.
OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OVERPASS_TIMEOUT_S = 45
OVERPASS_USER_AGENT = os.getenv(
    "OVERPASS_USER_AGENT",
    "FuelScope/0.1 (concept test; set OVERPASS_USER_AGENT with a contact address)",
)

# Two independent surveys call the same site the same place if they land
# within this distance of each other.
CROSS_MATCH_RADIUS_M = 90.0

# OSM data for one suburb changes over weeks; cache hard to stay a good citizen.
OVERPASS_CACHE_TTL_S = 3600
