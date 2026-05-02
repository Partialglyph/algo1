from __future__ import annotations

"""
oil_service.py

Fetches the current Brent crude oil spot price.
Primary source: EIA open data API (no key required for basic endpoint).
Falls back to a stub value if the fetch fails so the dashboard always responds.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from .models import OilSignal

log = logging.getLogger(__name__)

_EIA_URL = (
    "https://api.eia.gov/v2/petroleum/pri/spt/data/"
    "?api_key=DEMO"
    "&data[]=value"
    "&facets[series][]=RBRTE"
    "&sort[0][column]=period"
    "&sort[0][direction]=desc"
    "&length=7"
)

_STUB_PRICE = 85.0


async def fetch_oil_signal() -> OilSignal:
    """
    Try to get a real Brent price from EIA open API.
    Returns a stub OilSignal if any step fails — the dashboard never blocks on this.
    """
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(_EIA_URL)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("response", {}).get("data", [])
            if not rows:
                raise ValueError("EIA returned empty data")

            prices = [float(r["value"]) for r in rows if r.get("value") is not None]
            latest = prices[0]
            prev = prices[1] if len(prices) > 1 else latest
            change_pct = round((latest - prev) / prev * 100, 2) if prev else None

            if change_pct and change_pct > 0.5:
                trend = "rising"
            elif change_pct and change_pct < -0.5:
                trend = "falling"
            else:
                trend = "stable"

            return OilSignal(
                benchmark="Brent",
                price=round(latest, 2),
                change_24h_pct=change_pct,
                trend=trend,
                source="EIA",
                fetched_at=datetime.now(timezone.utc),
            )
    except Exception as exc:
        log.warning("Oil fetch failed, using stub: %s", exc)
        return OilSignal(
            benchmark="Brent",
            price=_STUB_PRICE,
            change_24h_pct=None,
            trend="stable",
            source="stub",
            fetched_at=datetime.now(timezone.utc),
        )
