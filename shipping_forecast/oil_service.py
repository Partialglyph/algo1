from __future__ import annotations

"""
oil_service.py

Fetches the current Brent crude oil spot price.

Source priority:
  1. EIA v2 API        (free key — set EIA_API_KEY env var)
  2. Alpha Vantage     (free key — set ALPHA_VANTAGE_KEY env var)
  3. Alpha Vantage     (demo key, heavily throttled, last live resort)
  4. Static stub       (dashboard always responds even if all live sources fail)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from .models import OilSignal

log = logging.getLogger(__name__)

_EIA_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
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


async def _try_alpha_vantage(api_key: str) -> Optional[tuple[float, float]]:
    """Returns (latest_price, prev_price) or None."""
    params = {
        "function": "BRENT",
        "interval": "daily",
        "apikey": api_key,
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(_ALPHA_VANTAGE_URL, params=params)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data or len(data) < 1:
            return None
        latest = float(data[0]["value"])
        prev = float(data[1]["value"]) if len(data) > 1 else latest
        return latest, prev


async def fetch_oil_signal() -> OilSignal:
    """
    Try to get a real Brent price from available sources.
    Priority: EIA -> Alpha Vantage (real key) -> Alpha Vantage (demo) -> stub.
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

    # 2. Alpha Vantage with real registered key
    if latest is None:
        av_key = os.environ.get("ALPHA_VANTAGE_KEY", "").strip()
        if av_key and av_key.upper() != "DEMO":
            try:
                result = await _try_alpha_vantage(av_key)
                if result is not None:
                    latest, prev = result
                    source = "AlphaVantage"
                    log.info("Oil price fetched from Alpha Vantage (real key): %.2f", latest)
            except Exception as exc:
                log.warning("Alpha Vantage (real key) fetch failed: %s", exc)

    # 3. Alpha Vantage demo key — throttled, but try anyway
    if latest is None:
        try:
            result = await _try_alpha_vantage("demo")
            if result is not None:
                latest, prev = result
                source = "AlphaVantage-demo"
        except Exception as exc:
            log.warning("Alpha Vantage demo fetch failed: %s", exc)

    # 4. Stub
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
        round((latest - prev) / prev * 100, 2) if prev and prev != latest else None
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
