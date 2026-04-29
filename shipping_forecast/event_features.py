from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .event_provider import EventArticle, EventFeed

# Keywords that indicate an active disruption in news coverage.
DISRUPTION_KEYWORDS: list[str] = [
    "strike",
    "disruption",
    "closure",
    "blocked",
    "congestion",
    "delay",
    "attack",
    "conflict",
    "sanctions",
    "halt",
    "piracy",
    "drone",
    "missile",
    "Houthi",
    "grounded",
    "reroute",
]

# Subset with higher direct impact on freight risk.
HIGH_SEVERITY_KEYWORDS: list[str] = [
    "war",
    "conflict",
    "sanctions",
    "missile",
    "attack",
    "Houthi",
    "piracy",
    "blockade",
    "explosion",
]


@dataclass
class EventFeatureSet:
    article_count: int
    disruption_count: int
    high_severity_count: int
    mean_tone: float
    disruption_score: float   # 0.0 – 1.0
    severity_score: float     # 0.0 – 1.0
    sentiment_score: float    # 0.0 – 1.0, higher = more negative news
    net_risk_score: float     # 0.0 – 1.0 composite
    top_headlines: list[str]


def _has_keyword(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


def _recency_weight(article: EventArticle, half_life_days: float = 7.0) -> float:
    """Exponential decay weight — more recent articles count more."""
    if not article.published:
        return 0.5
    age_days = (datetime.utcnow() - article.published).total_seconds() / 86400
    return math.exp(-math.log(2) / half_life_days * max(0.0, age_days))


def build_features(feed: EventFeed) -> EventFeatureSet:
    """Convert a raw EventFeed into normalised risk feature scores."""
    articles = feed.articles or []

    if not articles:
        return EventFeatureSet(
            article_count=0,
            disruption_count=0,
            high_severity_count=0,
            mean_tone=0.0,
            disruption_score=0.0,
            severity_score=0.0,
            sentiment_score=0.0,
            net_risk_score=0.0,
            top_headlines=[],
        )

    disruption_arts = [a for a in articles if _has_keyword(a.title, DISRUPTION_KEYWORDS)]
    high_sev_arts = [a for a in articles if _has_keyword(a.title, HIGH_SEVERITY_KEYWORDS)]

    tones = [a.tone for a in articles]
    mean_tone = sum(tones) / len(tones)

    # Recency-weighted scores.
    total_w = sum(_recency_weight(a) for a in articles) or 1.0
    disruption_w = sum(_recency_weight(a) for a in disruption_arts)
    severity_w = sum(_recency_weight(a) for a in high_sev_arts)

    disruption_score = min(disruption_w / total_w, 1.0)
    severity_score = min(severity_w / total_w, 1.0)

    # GDELT tone: negative = bad news. Map to 0–1 risk scale.
    # Range roughly -10 to +10 in practice; clamp outside that.
    sentiment_score = max(0.0, min(1.0, (-mean_tone + 5.0) / 10.0))

    # Composite weighted sum.
    net_risk_score = (
        0.45 * disruption_score
        + 0.35 * severity_score
        + 0.20 * sentiment_score
    )

    top_headlines = [a.title for a in articles[:5] if a.title]

    return EventFeatureSet(
        article_count=len(articles),
        disruption_count=len(disruption_arts),
        high_severity_count=len(high_sev_arts),
        mean_tone=round(mean_tone, 3),
        disruption_score=round(disruption_score, 3),
        severity_score=round(severity_score, 3),
        sentiment_score=round(sentiment_score, 3),
        net_risk_score=round(net_risk_score, 3),
        top_headlines=top_headlines,
    )
