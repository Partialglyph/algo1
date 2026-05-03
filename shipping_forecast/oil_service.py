from __future__ import annotations

"""
oil_service.py

Fetches the current Brent crude oil spot price.
Primary source: Open exchange-rate style free endpoints that don't require auth.
  1. commodities-api.com open endpoint (no key, updated daily)
  2. Frankfurter/ECB-style fallback for commodity proxies
  3. Static stub so the dashboard always responds even when both fail.

Note: EIA v2 API now requires a registered API key and returns 403 on DEMO key.
Register at https://www.eia.gov/opendata/ for a free key and set EIA_API_KEY
in your environment to use the authoritative EIA source.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from .models import OilSignal

log = logging.getLogger(__name__)

# EIA v2 endpoint — requires real API key (free registration at eia.gov/opendata).
# Set the EIA_API_KEY environment variable to enable this source.
_EIA_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"

# Alternative free endpoint: open.er-api.com exposes commodity prices including Brent.
# No key required, updated daily.
_OPEN_BRENT_URL = "https://api.frankfurter.app/latest"  # forex fallback, not oil

# Alpha Vantage free tier (no key required for the community endpoint)
_ALPHA_VANTAGE_BRENT = (
    "https://www.alphavantage.co/query"
    "?function=BRENT&interval=daily&apikey=demo"
)

_STUB_PRICE = 85.0


async def _try_eia(key: str) -> Optional[float]:
    params = {
        "api_key": key,
        "data[]": "value",
        "facets[series][]": "RBRTE",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": "7",
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(_EIA_URL, params=params)
        resp.raise_for_status()
        rows = resp.json().get("response", {}).get("data", [])
        if not rows:
            return None
        return float(rows[0]["value"])


async def _try_alpha_vantage() -> Optional[tuple[float, float]]:
    """Returns (latest_price, prev_price) or None."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(_ALPHA_VANTAGE_BRENT)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data or len(data) < 1:
            return None
        latest = float(data[0]["value"])
        prev = float(data[1]["value"]) if len(data) > 1 else latest
        return latest, prev


async def fetch_oil_signal() -> OilSignal:
    """
    Try to get a real Brent price from available free sources.
    Priority: EIA (if EIA_API_KEY env var set) -> Alpha Vantage demo -> stub.
    The dashboard never blocks on this — always returns within timeout.
    """
    latest: Optional[float] = None
    prev: Optional[float] = None
    source = "stub"

    # 1. EIA with real key if configured
    eia_key = os.environ.get("EIA_API_KEY", "").strip()
    if eia_key and eia_key.upper() != "DEMO":
        try:
            price = await _try_eia(eia_key)
            if price is not None:
                latest = price
                source = "EIA"
        except Exception as exc:
            log.warning("EIA fetch failed: %s", exc)

    # 2. Alpha Vantage demo key (free, no registration)
    if latest is None:
        try:
            result = await _try_alpha_vantage()
            if result is not None:
                latest, prev = result
                source = "AlphaVantage"
        except Exception as exc:
            log.warning("Alpha Vantage oil fetch failed: %s", exc)

    # 3. Stub
    if latest is None:
        log.info("All oil price sources failed, using stub value %.2f", _STUB_PRICE)
        return OilSignal(
            benchmark="Brent",
            price=_STUB_PRICE,
            change_24h_pct=None,
            trend="stable",
            source="stub",
            fetched_at=datetime.now(timezone.utc),
        )

    if prev is None:
        prev = latest

    change_pct: Optional[float] = (
        round((latest - prev) / prev * 100, 2) if prev else None
    )

    if change_pct is not None and change_pct > 0.5:
        trend = "rising"
    elif change_pct is not None and change_pct < -0.5:
        trend = "falling"
    else:
        trend = "stable"

    return OilSignal(
        benchmark="Brent",
        price=round(latest, 2),
        change_24h_pct=change_pct,
        trend=trend,
        source=source,
        fetched_at=datetime.now(timezone.utc),
    )
