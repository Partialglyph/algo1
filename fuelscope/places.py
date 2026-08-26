from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Iterable

import httpx

from . import settings

log = logging.getLogger(__name__)

_METERS_PER_DEG_LAT = 111_320.0


@dataclass
class Place:
    """One place as Google returned it, normalised but not yet judged."""
    place_id: str
    name: str
    address: str | None
    lat: float
    lon: float
    types: list[str]
    primary_type: str | None
    business_status: str | None

    @classmethod
    def from_api(cls, raw: dict) -> "Place":
        loc = raw.get("location") or {}
        return cls(
            place_id=raw["id"],
            name=(raw.get("displayName") or {}).get("text", ""),
            address=raw.get("formattedAddress"),
            lat=loc.get("latitude", 0.0),
            lon=loc.get("longitude", 0.0),
            types=list(raw.get("types") or []),
            primary_type=raw.get("primaryType"),
            business_status=raw.get("businessStatus"),
        )


@dataclass
class SurveyResult:
    places: list[Place]
    tiles_requested: int
    saturated_tiles: list[tuple[float, float]] = field(default_factory=list)
    source: str = "google"

    @property
    def coverage_is_suspect(self) -> bool:
        """A tile that returned a full page probably hid results behind the cap."""
        return bool(self.saturated_tiles)


def build_tiles(
    center_lat: float,
    center_lon: float,
    survey_radius_m: float,
    tile_radius_m: float = settings.TILE_RADIUS_M,
) -> list[tuple[float, float]]:
    """Cover a circular area with a square grid of smaller circle centres.

    A circle of radius r fully covers a square of side r*sqrt(2), so spacing the
    centres that far apart leaves no gaps between tiles.
    """
    spacing = tile_radius_m * math.sqrt(2)
    steps = int(math.ceil(survey_radius_m / spacing))

    dlat = spacing / _METERS_PER_DEG_LAT
    dlon = spacing / (_METERS_PER_DEG_LAT * math.cos(math.radians(center_lat)))

    tiles: list[tuple[float, float]] = []
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            # Keep the grid roughly circular so corner tiles aren't wasted calls.
            if math.hypot(i * spacing, j * spacing) > survey_radius_m + tile_radius_m:
                continue
            tiles.append((center_lat + i * dlat, center_lon + j * dlon))
    return tiles


async def _search_tile(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    tile_radius_m: float,
) -> list[dict]:
    body = {
        "includedTypes": settings.SEARCH_TYPES,
        "maxResultCount": settings.MAX_RESULTS_PER_TILE,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": tile_radius_m,
            }
        },
    }
    resp = await client.post(
        settings.PLACES_NEARBY_URL,
        json=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY or "",
            "X-Goog-FieldMask": settings.PLACES_FIELD_MASK,
        },
    )
    resp.raise_for_status()
    return resp.json().get("places", [])


async def survey(
    center_lat: float = settings.DEFAULT_CENTER_LAT,
    center_lon: float = settings.DEFAULT_CENTER_LON,
    survey_radius_m: float = settings.DEFAULT_SURVEY_RADIUS_M,
    tile_radius_m: float = settings.TILE_RADIUS_M,
) -> SurveyResult:
    """Walk a tile grid, dedupe by place id, and report where the cap bit."""
    if not settings.GOOGLE_MAPS_API_KEY:
        raise RuntimeError(
            "GOOGLE_MAPS_API_KEY is not set. Use load_fixture() for an offline run."
        )

    tiles = build_tiles(center_lat, center_lon, survey_radius_m, tile_radius_m)
    if len(tiles) > settings.MAX_TILES_PER_SURVEY:
        raise ValueError(
            f"survey would issue {len(tiles)} requests, above the "
            f"MAX_TILES_PER_SURVEY guard of {settings.MAX_TILES_PER_SURVEY}. "
            "Shrink the radius or enlarge the tiles."
        )

    seen: dict[str, Place] = {}
    saturated: list[tuple[float, float]] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for lat, lon in tiles:
            raw = await _search_tile(client, lat, lon, tile_radius_m)
            if len(raw) >= settings.MAX_RESULTS_PER_TILE:
                saturated.append((lat, lon))
            for item in raw:
                place = Place.from_api(item)
                seen.setdefault(place.place_id, place)

    log.info("surveyed %d tiles, found %d unique places", len(tiles), len(seen))
    return SurveyResult(
        places=list(seen.values()),
        tiles_requested=len(tiles),
        saturated_tiles=saturated,
    )


def load_fixture(path: str = settings.FIXTURE_PATH) -> SurveyResult:
    """Offline sample so the concept test runs with no key and no billing."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    places = [Place.from_api(item) for item in raw["places"]]
    return SurveyResult(
        places=places,
        tiles_requested=raw.get("tiles_requested", 0),
        source="fixture",
    )
