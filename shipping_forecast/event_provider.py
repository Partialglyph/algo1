from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx

from .lane_event_map import get_keywords_for_lane

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


@dataclass
class EventArticle:
    title: str
    url: str
    source: str
    published: Optional[datetime]
    tone: float
    # Extended fields — populated with safe defaults when not available
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


class GDELTEventProvider:
    """
    Fetches recent news articles from the GDELT 2.0 DOC API for a given trade lane.
    Uses documented boolean OR blocks in parentheses and retries with broader
    fallbacks when the first query returns no articles.
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
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(GDELT_DOC_API, params=params)
                resp.raise_for_status()
                data = resp.json()
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
        lane            : trade lane name (used for keyword lookup when keywords=None)
        keywords        : explicit keyword list (overrides lane-based lookup)
        timespan_hours  : window in hours; 0 = use self.lookback_days
        max_articles    : max records to request; 0 = use self.max_articles
        """
        kws = keywords if keywords is not None else get_keywords_for_lane(lane)
        max_rec = max_articles if max_articles > 0 else self.max_articles
        timespan_days = (
            max(1, round(timespan_hours / 24)) if timespan_hours > 0
            else self.lookback_days
        )

        queries = self._build_query_candidates(kws)

        last_error: Optional[str] = None
        for query in queries:
            articles, error = await self._request_articles(query, max_rec, timespan_days)
            if error:
                last_error = error
                continue
            if articles:
                logger.debug(
                    "GDELT returned %d articles for lane '%s' using query: %s",
                    len(articles), lane, query,
                )
                return EventFeed(lane=lane, query=query, articles=articles)
            logger.debug("GDELT query returned no articles, trying next fallback: %s", query)

        logger.warning(
            "All GDELT query candidates exhausted for lane '%s' with no articles.", lane
        )
        return EventFeed(lane=lane, query=queries[-1], articles=[], error=last_error)
