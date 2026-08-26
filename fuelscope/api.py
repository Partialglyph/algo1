from __future__ import annotations

import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import overpass, settings
from .classify import build_report
from .compare import build_comparison
from .overpass import OverpassBusy
from .places import build_tiles, load_fixture, survey

log = logging.getLogger(__name__)

app = FastAPI(title="FuelScope concept test")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Source = Literal["fixture", "google", "osm"]


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "has_places_key": bool(settings.GOOGLE_MAPS_API_KEY),
        "has_browser_key": bool(settings.GOOGLE_MAPS_BROWSER_KEY),
        "sources": ["fixture", "osm"] + (["google"] if settings.GOOGLE_MAPS_API_KEY else []),
    }


@app.get("/v1/mapconfig")
async def mapconfig() -> dict:
    """Browser map settings. Deliberately never returns the server Places key."""
    return {
        "browser_key": settings.GOOGLE_MAPS_BROWSER_KEY,
        "map_id": settings.GOOGLE_MAPS_MAP_ID,
        "center": {"lat": settings.DEFAULT_CENTER_LAT, "lon": settings.DEFAULT_CENTER_LON},
    }


@app.get("/v1/plan")
async def plan(
    lat: float = settings.DEFAULT_CENTER_LAT,
    lon: float = settings.DEFAULT_CENTER_LON,
    radius_m: float = Query(settings.DEFAULT_SURVEY_RADIUS_M, gt=0),
    tile_radius_m: float = Query(settings.TILE_RADIUS_M, gt=0),
) -> dict:
    """Cost preview for the Google path. Every tile is one billable request."""
    tiles = build_tiles(lat, lon, radius_m, tile_radius_m)
    return {
        "tiles": len(tiles),
        "billable_requests": len(tiles),
        "over_guard": len(tiles) > settings.MAX_TILES_PER_SURVEY,
        "guard": settings.MAX_TILES_PER_SURVEY,
        "note": "Overpass covers the same area in one free request.",
    }


async def _run_survey(source: Source, lat: float, lon: float, radius_m: float, tile_radius_m: float):
    if source == "fixture":
        return load_fixture()
    if source == "osm":
        try:
            return await overpass.survey(lat, lon, radius_m)
        except OverpassBusy as exc:
            raise HTTPException(503, str(exc)) from exc
    if not settings.GOOGLE_MAPS_API_KEY:
        raise HTTPException(400, "GOOGLE_MAPS_API_KEY is not set; use source=osm or source=fixture")
    try:
        return await survey(lat, lon, radius_m, tile_radius_m)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/sites")
async def sites(
    source: Source = Query("fixture", description="fixture (synthetic), osm (free), google (billable)"),
    lat: float = settings.DEFAULT_CENTER_LAT,
    lon: float = settings.DEFAULT_CENTER_LON,
    radius_m: float = Query(settings.DEFAULT_SURVEY_RADIUS_M, gt=0),
    tile_radius_m: float = Query(settings.TILE_RADIUS_M, gt=0),
) -> dict:
    """Survey an area and score how well its type tags identify fuel + stores."""
    result = await _run_survey(source, lat, lon, radius_m, tile_radius_m)
    return build_report(result)


@app.get("/v1/compare")
async def compare(
    against: Source = Query("fixture", description="which source to compare OSM against"),
    lat: float = settings.DEFAULT_CENTER_LAT,
    lon: float = settings.DEFAULT_CENTER_LON,
    radius_m: float = Query(settings.DEFAULT_SURVEY_RADIUS_M, gt=0),
    tile_radius_m: float = Query(settings.TILE_RADIUS_M, gt=0),
) -> dict:
    """Cross-check one source against OpenStreetMap over the same area."""
    if against == "osm":
        raise HTTPException(400, "cannot compare osm against itself")

    a = await _run_survey(against, lat, lon, radius_m, tile_radius_m)
    b = await _run_survey("osm", lat, lon, radius_m, tile_radius_m)
    report = build_comparison(a, b)

    if against == "fixture":
        report["warning"] = (
            "The fixture is synthetic data with invented coordinates. It will not "
            "match real OSM features, so agreement figures here are meaningless. "
            "Set GOOGLE_MAPS_API_KEY and use against=google for a real comparison."
        )
    return report
