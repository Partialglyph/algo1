from __future__ import annotations

import logging
import time

import httpx

from . import settings
from .places import Place, SurveyResult

log = logging.getLogger(__name__)

# OSM tags that mean "you can buy fuel here" / "you can buy snacks here".
FUEL_TAG = ("amenity", "fuel")
STORE_TAGS = [("shop", "convenience"), ("shop", "kiosk")]

_QUERY = """
[out:json][timeout:{timeout}];
(
  node["amenity"="fuel"](around:{radius},{lat},{lon});
  way["amenity"="fuel"](around:{radius},{lat},{lon});
  node["shop"="convenience"](around:{radius},{lat},{lon});
  way["shop"="convenience"](around:{radius},{lat},{lon});
  node["shop"="kiosk"](around:{radius},{lat},{lon});
);
out center tags;
"""


def _to_place(element: dict) -> Place | None:
    """Normalise an OSM element into the same shape as a Google place.

    Reusing Place means classify.py works on OSM data unchanged - the whole
    point is to run one classifier over two independent sources.
    """
    tags = element.get("tags") or {}
    if element["type"] == "node":
        lat, lon = element.get("lat"), element.get("lon")
    else:
        center = element.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None

    # Translate OSM tags into the Google type vocabulary so one classifier
    # can score both sources. These are synthesised, not Google's own tags.
    types: list[str] = []
    if tags.get(FUEL_TAG[0]) == FUEL_TAG[1]:
        types.append("gas_station")
    if any(tags.get(k) == v for k, v in STORE_TAGS):
        types.append("convenience_store")

    name = tags.get("name") or tags.get("brand") or tags.get("operator") or ""
    address = " ".join(filter(None, [
        tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:city"),
    ])) or None

    return Place(
        place_id=f"osm:{element['type']}/{element['id']}",
        name=name,
        address=address,
        lat=lat,
        lon=lon,
        types=types,
        primary_type=types[0] if types else None,
        business_status="OPERATIONAL",
    )


class OverpassBusy(RuntimeError):
    """The public endpoint rate-limited or timed out us."""


# The public Overpass instance is donated capacity shared by everyone, and it
# will 429 a tight loop quickly. Cache aggressively: OSM data for one suburb
# changes on a timescale of weeks, not seconds.
_cache: dict[tuple, tuple[float, SurveyResult]] = {}


def _cache_key(lat: float, lon: float, radius: float) -> tuple:
    return (round(lat, 4), round(lon, 4), int(radius))


async def survey(
    center_lat: float = settings.DEFAULT_CENTER_LAT,
    center_lon: float = settings.DEFAULT_CENTER_LON,
    survey_radius_m: float = settings.DEFAULT_SURVEY_RADIUS_M,
    force: bool = False,
) -> SurveyResult:
    """One Overpass call covers the whole radius - no tiling, no result cap.

    This is the structural advantage over Nearby Search: OSM answers a real
    spatial query instead of returning the 20 nearest things.
    """
    key = _cache_key(center_lat, center_lon, survey_radius_m)
    hit = _cache.get(key)
    if hit and not force and (time.monotonic() - hit[0]) < settings.OVERPASS_CACHE_TTL_S:
        log.info("overpass cache hit for %s", key)
        return hit[1]

    query = _QUERY.format(
        timeout=settings.OVERPASS_TIMEOUT_S,
        radius=int(survey_radius_m),
        lat=center_lat,
        lon=center_lon,
    )
    async with httpx.AsyncClient(timeout=settings.OVERPASS_TIMEOUT_S + 10) as client:
        resp = await client.post(
            settings.OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": settings.OVERPASS_USER_AGENT},
        )

    if resp.status_code in (429, 504):
        # Serve stale rather than fail: week-old OSM data beats no data.
        if hit:
            log.warning("overpass %s, serving stale cache", resp.status_code)
            return hit[1]
        raise OverpassBusy(
            f"Overpass returned {resp.status_code}. The public endpoint is shared and "
            "throttles bursts - wait a minute, or point OVERPASS_URL at another mirror."
        )
    resp.raise_for_status()
    payload = resp.json()

    places: list[Place] = []
    for element in payload.get("elements", []):
        place = _to_place(element)
        if place and place.types:
            places.append(place)

    log.info("overpass returned %d usable features", len(places))
    result = SurveyResult(places=places, tiles_requested=1, source="osm")
    _cache[key] = (time.monotonic(), result)
    return result
