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
    tone: float  # GDELT tone: negative value = more negative coverage


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
    Requires no API key — GDELT is free and open.
    Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
    """

    def __init__(self, lookback_days: int = 14, max_articles: int = 50) -> None:
        self.lookback_days = lookback_days
        self.max_articles = max_articles

    def _build_query(self, keywords: list[str]) -> str:
        # Use the two most relevant keywords joined with OR for broad coverage.
        terms = keywords[:2]
        return " OR ".join(f'"{t}"' for t in terms)

    async def fetch(self, lane: str) -> EventFeed:
        keywords = get_keywords_for_lane(lane)
        query = self._build_query(keywords)

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
            logger.warning("GDELT fetch failed for lane '%s': %s", lane, exc)
            return EventFeed(lane=lane, query=query, error=str(exc))

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

        return EventFeed(lane=lane, query=query, articles=articles)
