from __future__ import annotations

"""
oil_service.py

Fetches the current Brent crude spot price and 90-day history.

Source priority:
  1. yfinance  BZ=F  (free, unlimited, no key — PRIMARY)
  2. EIA v2 API      (free key — set EIA_API_KEY env var)
  3. Alpha Vantage   (rate-limited — set ALPHA_VANTAGE_KEY env var AND
                      ALPHA_VANTAGE_CONFIRMED=true to opt in, because
                      each call burns one of your 25 daily free requests)
  4. Static stub     (always returns so the dashboard never blocks)

Why yfinance first:
  BZ=F is the CME Brent crude futures front-month contract.
  yfinance downloads it from Yahoo Finance with no authentication,
  no rate limits in normal usage, and returns daily OHLCV going back years.
  It is synchronous, so we run it in a thread pool to avoid blocking
  the async event loop.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from .models import OilHistoryPoint, OilSignal

log = logging.getLogger(__name__)

_EIA_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
_STUB_PRICE = 85.0
_HISTORY_DAYS = 90


# ---------------------------------------------------------------------------
# yfinance (primary — free, unlimited)
# ---------------------------------------------------------------------------

def _yfinance_fetch_sync() -> Optional[tuple[float, float, list[OilHistoryPoint]]]:
    """
    Synchronous yfinance call, intended to run in a thread pool.
    Returns (latest_close, prev_close, history_points) or None on failure.
    BZ=F = CME Brent crude futures front-month.
    """
    try:
        import yfinance as yf
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=_HISTORY_DAYS + 5)  # small buffer for weekends
        ticker = yf.Ticker("BZ=F")
        df = ticker.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        if df is None or df.empty:
            return None
        closes = df["Close"].dropna()
        if len(closes) < 1:
            return None
        latest = float(closes.iloc[-1])
        prev   = float(closes.iloc[-2]) if len(closes) > 1 else latest
        history = [
            OilHistoryPoint(date=str(idx.date()), price=round(float(val), 2))
            for idx, val in closes.items()
        ]
        return latest, prev, history
    except Exception as exc:
        log.warning("yfinance Brent fetch failed: %s", exc)
        return None


async def _try_yfinance() -> Optional[tuple[float, float, list[OilHistoryPoint]]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _yfinance_fetch_sync)


# ---------------------------------------------------------------------------
# EIA (requires free registered key)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Alpha Vantage (rate-limited — opt-in only)
# ---------------------------------------------------------------------------

async def _try_alpha_vantage(api_key: str) -> Optional[tuple[float, float]]:
    """
    Only called when both ALPHA_VANTAGE_KEY and ALPHA_VANTAGE_CONFIRMED=true
    are set in env. Each call burns one of the 25 daily free requests.
    Returns (latest_price, prev_price) or None.
    """
    params = {
        "function": "BRENT",
        "interval": "daily",
        "apikey": api_key,
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(_ALPHA_VANTAGE_URL, params=params)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        latest = float(data[0]["value"])
        prev   = float(data[1]["value"]) if len(data) > 1 else latest
        return latest, prev


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def fetch_oil_signal() -> tuple[OilSignal, list[OilHistoryPoint]]:
    """
    Returns (OilSignal, history_points).
    history_points is a list of up to 90 daily Brent closes for the chart.
    Falls back gracefully through the source chain so the dashboard always
    responds within the httpx timeout window.
    """
    latest:  Optional[float]                  = None
    prev:    Optional[float]                  = None
    history: list[OilHistoryPoint]            = []
    source   = "stub"

    # 1. yfinance — free, unlimited, gives us history too
    try:
        result = await _try_yfinance()
        if result is not None:
            latest, prev, history = result
            source = "yfinance"
            log.info("Oil fetched via yfinance: %.2f", latest)
    except Exception as exc:
        log.warning("yfinance wrapper failed: %s", exc)

    # 2. EIA with registered key (no history, just latest)
    if latest is None:
        eia_key = os.environ.get("EIA_API_KEY", "").strip()
        if eia_key and eia_key.upper() != "DEMO":
            try:
                price = await _try_eia(eia_key)
                if price is not None:
                    latest = price
                    source = "EIA"
            except Exception as exc:
                log.warning("EIA fetch failed: %s", exc)

    # 3. Alpha Vantage — only if user has explicitly opted in
    #    Requires BOTH env vars: ALPHA_VANTAGE_KEY and ALPHA_VANTAGE_CONFIRMED=true
    #    The frontend "are you sure" dialog sets a session flag; the backend
    #    gate here is the server-side confirmation so accidental calls never burn quota.
    if latest is None:
        av_key       = os.environ.get("ALPHA_VANTAGE_KEY", "").strip()
        av_confirmed = os.environ.get("ALPHA_VANTAGE_CONFIRMED", "").lower() == "true"
        if av_key and av_confirmed:
            try:
                result_av = await _try_alpha_vantage(av_key)
                if result_av is not None:
                    latest, prev = result_av
                    source = "AlphaVantage"
                    log.info("Oil fetched via Alpha Vantage (quota used): %.2f", latest)
            except Exception as exc:
                log.warning("Alpha Vantage fetch failed: %s", exc)
        elif av_key and not av_confirmed:
            log.info(
                "Alpha Vantage key is set but ALPHA_VANTAGE_CONFIRMED is not 'true'. "
                "Set ALPHA_VANTAGE_CONFIRMED=true to allow calls (burns daily quota)."
            )

    # 4. Stub
    if latest is None:
        log.info("All oil sources failed, using stub %.2f", _STUB_PRICE)
        return OilSignal(
            benchmark="Brent",
            price=_STUB_PRICE,
            change_24h_pct=None,
            trend="stable",
            source="stub",
            fetched_at=datetime.now(timezone.utc),
        ), []

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

    signal = OilSignal(
        benchmark="Brent",
        price=round(latest, 2),
        change_24h_pct=change_pct,
        trend=trend,
        source=source,
        fetched_at=datetime.now(timezone.utc),
    )
    return signal, history
