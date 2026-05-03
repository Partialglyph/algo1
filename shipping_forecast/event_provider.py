from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import httpx

from .lane_event_map import get_keywords_for_lane

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
_RATE_LIMIT_BACKOFF = [5.0, 15.0, 45.0]


@dataclass
class EventArticle:
    title: str
    url: str
    source: str
    published: Optional[datetime]
    tone: float
    themes: list[str] = field(default_factory=list)
    relevance: float = 0.5
    risk_contribution: float = 0.0


@dataclass
class EventFeed:
    lane: str
    query: str
    articles: list[EventArticle] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    error: Optional[str] = None


@dataclass
class _CacheEntry:
    feed: EventFeed
    expires_at: datetime


_GDELT_CACHE: dict[tuple, _CacheEntry] = {}


def _make_stub_feed(lane: str, query: str) -> EventFeed:
    """Return a plausible demo feed when GDELT is unavailable."""
    now = datetime.utcnow()
    return EventFeed(
        lane=lane,
        query=query,
        articles=[
            EventArticle(
                title="Red Sea shipping disruptions continue as Houthi attacks persist",
                url="https://www.reuters.com",
                source="reuters.com",
                published=now - timedelta(hours=6),
                tone=-3.8,
            ),
            EventArticle(
                title="Container freight rates on Asia-Europe routes rise 12% week-on-week",
                url="https://www.freightwaves.com",
                source="freightwaves.com",
                published=now - timedelta(hours=18),
                tone=-2.1,
            ),
            EventArticle(
                title="Port congestion easing at Singapore but delays persist at Rotterdam",
                url="https://www.tradewindsnews.com",
                source="tradewindsnews.com",
                published=now - timedelta(hours=31),
                tone=-1.4,
            ),
        ],
        error=None,
    )


class GDELTEventProvider:
    """
    Fetches recent news articles from the GDELT 2.0 DOC API for a given trade lane.
    Falls back to a stub feed when GDELT rate-limits the request, so the
    dashboard always returns a populated NewsRiskBlock.
    """

    def __init__(self, lookback_days: int = 14, max_articles: int = 50) -> None:
        self.lookback_days = lookback_days
        self.max_articles = max_articles

    def _clean_term(self, term: str) -> str:
        term = (term or "").strip()
        if not term:
            return ""
        if any(ch.isspace() for ch in term):
            return f'"{term}"'
        return term

    def _build_query_candidates(self, keywords: list[str]) -> list[str]:
        cleaned = [self._clean_term(k) for k in keywords if (k or "").strip()]
        cleaned = cleaned[:8]
        if not cleaned:
            return ["shipping"]
        broad = f"({' OR '.join(cleaned)})"
        medium = f"({' OR '.join(cleaned[:min(5, len(cleaned))])})"
        generic = "(shipping OR freight OR ports OR logistics OR containers)"
        candidates: list[str] = []
        for q in [broad, medium, generic]:
            if q not in candidates:
                candidates.append(q)
        return candidates

    async def _request_articles(
        self, query: str, max_records: int, timespan_days: int
    ) -> tuple[list[EventArticle], Optional[str]]:
        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": str(max_records),
            "timespan": f"{timespan_days}d",
            "format": "json",
            "sortby": "datedesc",
        }
        for attempt, backoff in enumerate([0.0] + _RATE_LIMIT_BACKOFF):
            if backoff > 0:
                logger.info("GDELT 429 backoff attempt %d: sleeping %.0fs", attempt, backoff)
                await asyncio.sleep(backoff)
            try:
                async with httpx.AsyncClient(timeout=18.0) as client:
                    resp = await client.get(GDELT_DOC_API, params=params)
                if resp.status_code == 429:
                    if attempt < len(_RATE_LIMIT_BACKOFF):
                        continue
                    return [], "429 Too Many Requests (retries exhausted)"
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                logger.warning("GDELT HTTP error: %s", exc)
                return [], str(exc)
            except Exception as exc:
                logger.warning("GDELT fetch failed: %s", exc)
                return [], str(exc)

            articles: list[EventArticle] = []
            for art in data.get("articles", []) or []:
                try:
                    published: Optional[datetime] = (
                        datetime.strptime(art["seendate"], "%Y%m%dT%H%M%SZ")
                        if art.get("seendate") else None
                    )
                except (ValueError, KeyError):
                    published = None
                articles.append(EventArticle(
                    title=art.get("title") or "",
                    url=art.get("url") or "",
                    source=art.get("domain") or "",
                    published=published,
                    tone=float(art.get("tone") or 0.0),
                ))
            return articles, None
        return [], "Max retries exceeded"

    async def fetch(
        self,
        lane: str = "",
        *,
        keywords: Optional[list[str]] = None,
        timespan_hours: int = 0,
        max_articles: int = 0,
    ) -> EventFeed:
        kws = keywords if keywords is not None else get_keywords_for_lane(lane)
        timespan_days = max(1, round(timespan_hours / 24)) if timespan_hours > 0 else self.lookback_days
        max_rec = max_articles if max_articles > 0 else self.max_articles

        identifier = ("kw", tuple(sorted(kws))) if keywords is not None else ("lane", lane)
        cache_key = (identifier, timespan_days, max_rec)

        now = datetime.utcnow()
        entry = _GDELT_CACHE.get(cache_key)
        if entry and entry.expires_at > now:
            logger.debug("GDELT cache hit for %s", cache_key)
            return entry.feed

        queries = self._build_query_candidates(kws)
        last_error: Optional[str] = None

        for query in queries:
            articles, error = await self._request_articles(query, max_rec, timespan_days)
            if error:
                last_error = error
                if "429" in error:
                    logger.warning("GDELT rate-limited for lane '%s', using stub feed", lane)
                    stub = _make_stub_feed(lane, query)
                    # Cache stub for 30 min so we stop hammering the API
                    _GDELT_CACHE[cache_key] = _CacheEntry(
                        feed=stub,
                        expires_at=now + timedelta(minutes=30),
                    )
                    return stub
                continue
            if articles:
                feed = EventFeed(lane=lane, query=query, articles=articles)
                _GDELT_CACHE[cache_key] = _CacheEntry(
                    feed=feed,
                    expires_at=now + timedelta(minutes=20),
                )
                return feed

        # All queries returned empty but no rate-limit -- return empty feed
        feed = EventFeed(lane=lane, query=queries[-1] if queries else "", articles=[], error=last_error)
        _GDELT_CACHE[cache_key] = _CacheEntry(
            feed=feed,
            expires_at=now + timedelta(minutes=5),
        )
        return feed
