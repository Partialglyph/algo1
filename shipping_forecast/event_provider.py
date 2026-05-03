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

# GDELT enforces a rate limit of roughly 1 request per 5-10 seconds from the same IP.
# We back off with jitter on 429 and cache aggressively to avoid hammering the endpoint.
_RATE_LIMIT_BACKOFF = [5.0, 15.0, 45.0]  # seconds between retries on 429


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


# Module-level cache shared across ALL provider instances in this process.
# Key: (identifier, timespan_days, max_records)
_GDELT_CACHE: dict[tuple, _CacheEntry] = {}


class GDELTEventProvider:
    """
    Fetches recent news articles from the GDELT 2.0 DOC API for a given trade lane.
    Retries on 429 with exponential backoff. Caches results aggressively to avoid
    rate-limit violations on repeated dashboard loads.

    Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
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
        medium_terms = cleaned[: min(5, len(cleaned))]
        medium = f"({' OR '.join(medium_terms)})"
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
                logger.info(
                    "GDELT 429 rate-limit on attempt %d, backing off %.0fs before retry",
                    attempt, backoff,
                )
                await asyncio.sleep(backoff)

            try:
                async with httpx.AsyncClient(timeout=18.0) as client:
                    resp = await client.get(GDELT_DOC_API, params=params)

                if resp.status_code == 429:
                    if attempt < len(_RATE_LIMIT_BACKOFF):
                        continue  # will sleep on next iteration
                    logger.warning(
                        "GDELT 429 exhausted retries for query '%s'", query
                    )
                    return [], "429 Too Many Requests (retries exhausted)"

                resp.raise_for_status()
                data = resp.json()

            except httpx.HTTPStatusError as exc:
                logger.warning("GDELT HTTP error for query '%s': %s", query, exc)
                return [], str(exc)
            except Exception as exc:
                logger.warning("GDELT fetch failed for query '%s': %s", query, exc)
                return [], str(exc)

            articles: list[EventArticle] = []
            for art in data.get("articles", []) or []:
                try:
                    published: Optional[datetime] = (
                        datetime.strptime(art["seendate"], "%Y%m%dT%H%M%SZ")
                        if art.get("seendate")
                        else None
                    )
                except (ValueError, KeyError):
                    published = None

                articles.append(
                    EventArticle(
                        title=art.get("title") or "",
                        url=art.get("url") or "",
                        source=art.get("domain") or "",
                        published=published,
                        tone=float(art.get("tone") or 0.0),
                    )
                )
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
        """
        Fetch articles for a lane or an explicit keyword list.

        Parameters
        ----------
        lane
            Trade lane name (used for keyword lookup when ``keywords`` is None).
        keywords
            Explicit keyword list (overrides lane-based lookup).
        timespan_hours
            Window in hours; 0 = use ``self.lookback_days``.
        max_articles
            Max records to request; 0 = use ``self.max_articles``.
        """
        kws = keywords if keywords is not None else get_keywords_for_lane(lane)
        timespan_days = (
            max(1, round(timespan_hours / 24)) if timespan_hours > 0
            else self.lookback_days
        )
        max_rec = max_articles if max_articles > 0 else self.max_articles

        identifier = ("kw", tuple(sorted(kws))) if keywords is not None else ("lane", lane)
        cache_key = (identifier, timespan_days, max_rec)

        now = datetime.utcnow()
        entry = _GDELT_CACHE.get(cache_key)
        if entry and entry.expires_at > now:
            logger.debug("GDELT cache hit for key %s", cache_key)
            return entry.feed

        queries = self._build_query_candidates(kws)

        last_error: Optional[str] = None
        for query in queries:
            articles, error = await self._request_articles(query, max_rec, timespan_days)
            if error:
                last_error = error
                # If rate-limited, stop trying fallback queries — they'll also get 429.
                if "429" in error:
                    logger.warning(
                        "GDELT rate-limited, skipping fallback queries for lane '%s'", lane
                    )
                    break
                continue
            if articles:
                logger.debug(
                    "GDELT returned %d articles for lane '%s' via: %s",
                    len(articles), lane, query,
                )
                feed = EventFeed(lane=lane, query=query, articles=articles)
                _GDELT_CACHE[cache_key] = _CacheEntry(
                    feed=feed,
                    expires_at=now + timedelta(minutes=20),
                )
                return feed
            logger.debug("No articles for query, trying fallback: %s", query)

        logger.warning(
            "All GDELT queries exhausted for lane '%s' (last error: %s).", lane, last_error
        )
        feed = EventFeed(
            lane=lane,
            query=queries[-1] if queries else "",
            articles=[],
            error=last_error,
        )
        # Use a longer TTL on rate-limit errors so we don't retry aggressively.
        error_ttl = 30 if last_error and "429" in (last_error or "") else 5
        _GDELT_CACHE[cache_key] = _CacheEntry(
            feed=feed,
            expires_at=now + timedelta(minutes=error_ttl),
        )
        return feed
