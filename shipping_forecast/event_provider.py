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
        """
        Quote multi-word phrases so GDELT treats them as exact phrases.
        Single words are left unquoted so they match anywhere in the article.
        """
        term = (term or "").strip()
        if not term:
            return ""
        if any(ch.isspace() for ch in term):
            return f'"{term}"'
        return term

    def _build_query_candidates(self, keywords: list[str]) -> list[str]:
        """
        Return a list of queries in decreasing specificity.  The caller tries
        each in turn and stops at the first one that returns articles.

        - broad:   all lane keywords wrapped in (a OR b OR c OR ...)
        - medium:  first 5 lane keywords only
        - generic: a hardcoded broad shipping fallback that almost always hits
        """
        cleaned = [self._clean_term(k) for k in keywords if (k or "").strip()]
        cleaned = cleaned[:8]
        if not cleaned:
            return ["shipping"]

        broad = f"({' OR '.join(cleaned)})"
        medium_terms = cleaned[: min(5, len(cleaned))]
        medium = f"({' OR '.join(medium_terms)})"
        generic = "(shipping OR freight OR ports OR logistics OR containers)"

        # deduplicate while preserving order
        candidates: list[str] = []
        for q in [broad, medium, generic]:
            if q not in candidates:
                candidates.append(q)
        return candidates

    async def _request_articles(
        self, query: str
    ) -> tuple[list[EventArticle], Optional[str]]:
        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": str(self.max_articles),
            "timespan": f"{self.lookback_days}d",
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

    async def fetch(self, lane: str) -> EventFeed:
        keywords = get_keywords_for_lane(lane)
        queries = self._build_query_candidates(keywords)

        last_error: Optional[str] = None
        for query in queries:
            articles, error = await self._request_articles(query)
            if error:
                last_error = error
                continue
            if articles:
                logger.debug(
                    "GDELT returned %d articles for lane '%s' using query: %s",
                    len(articles),
                    lane,
                    query,
                )
                return EventFeed(lane=lane, query=query, articles=articles)
            logger.debug("GDELT query returned no articles, trying next fallback: %s", query)

        logger.warning(
            "All GDELT query candidates exhausted for lane '%s' with no articles.", lane
        )
        return EventFeed(lane=lane, query=queries[-1], articles=[], error=last_error)
