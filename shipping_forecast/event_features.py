from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict

from .event_provider import EventArticle, EventFeed

# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------

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
    "tariff",
    "port closure",
    "canal",
]

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

# Theme clusters: maps a display theme name to its associated keywords.
# Used for ThemeBreakdown generation.
THEME_CLUSTERS: Dict[str, list[str]] = {
    "Port disruption": ["port", "terminal", "congestion", "closure", "strike", "labor"],
    "Conflict & security": ["war", "conflict", "attack", "missile", "Houthi", "piracy", "drone", "explosion"],
    "Sanctions & tariffs": ["sanctions", "tariff", "ban", "embargo", "trade war"],
    "Canal & chokepoint": ["Suez", "Panama", "Red Sea", "Strait", "reroute", "canal"],
    "Weather & environment": ["storm", "typhoon", "hurricane", "flood", "drought", "weather"],
    "Capacity & demand": ["capacity", "shortage", "backlog", "demand", "booking", "space"],
}


# ---------------------------------------------------------------------------
# Feature set dataclass
# ---------------------------------------------------------------------------

@dataclass
class EventFeatureSet:
    article_count: int
    disruption_count: int
    high_severity_count: int
    mean_tone: float
    disruption_score: float    # 0.0 – 1.0
    severity_score: float      # 0.0 – 1.0
    sentiment_score: float     # 0.0 – 1.0, higher = more negative
    volume_score: float        # 0.0 – 1.0, elevated volume vs baseline
    concentration_score: float # 0.0 – 1.0, narrative convergence
    net_risk_score: float      # 0.0 – 1.0 composite
    top_headlines: list[str]
    # Volume counts for ArticleVolume model
    count_24h: int
    count_72h: int
    count_7d: int
    # Theme breakdown: theme -> (article_count, avg_tone)
    theme_counts: Dict[str, tuple[int, float]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_keyword(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


def _recency_weight(article: EventArticle, half_life_days: float = 7.0) -> float:
    """Exponential decay — more recent articles count more."""
    if not article.published:
        return 0.5
    now = datetime.now(timezone.utc)
    pub = article.published
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    age_days = (now - pub).total_seconds() / 86400
    return math.exp(-math.log(2) / half_life_days * max(0.0, age_days))


def _age_hours(article: EventArticle) -> float:
    if not article.published:
        return 9999.0
    now = datetime.now(timezone.utc)
    pub = article.published
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    return (now - pub).total_seconds() / 3600


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_features(feed: EventFeed) -> EventFeatureSet:
    """Convert a raw EventFeed into normalised risk feature scores."""
    articles = feed.articles or []

    empty = EventFeatureSet(
        article_count=0, disruption_count=0, high_severity_count=0,
        mean_tone=0.0, disruption_score=0.0, severity_score=0.0,
        sentiment_score=0.0, volume_score=0.0, concentration_score=0.0,
        net_risk_score=0.0, top_headlines=[],
        count_24h=0, count_72h=0, count_7d=0,
        theme_counts={},
    )
    if not articles:
        return empty

    disruption_arts = [a for a in articles if _has_keyword(a.title, DISRUPTION_KEYWORDS)]
    high_sev_arts = [a for a in articles if _has_keyword(a.title, HIGH_SEVERITY_KEYWORDS)]

    tones = [a.tone for a in articles]
    mean_tone = sum(tones) / len(tones)

    # Recency-weighted scores
    total_w = sum(_recency_weight(a) for a in articles) or 1.0
    disruption_w = sum(_recency_weight(a) for a in disruption_arts)
    severity_w = sum(_recency_weight(a) for a in high_sev_arts)

    disruption_score = min(disruption_w / total_w, 1.0)
    severity_score = min(severity_w / total_w, 1.0)

    # GDELT tone: negative = bad news. Map to 0–1 risk scale.
    sentiment_score = max(0.0, min(1.0, (-mean_tone + 5.0) / 10.0))

    # Volume score: articles in last 72h vs a soft baseline of ~5 articles
    count_24h = sum(1 for a in articles if _age_hours(a) <= 24)
    count_72h = sum(1 for a in articles if _age_hours(a) <= 72)
    count_7d = len(articles)
    BASELINE = 5
    volume_score = min((count_72h / BASELINE) / 3.0, 1.0)  # saturates at 3× baseline

    # Concentration score: fraction of articles that share the top theme
    theme_counts: Dict[str, list[float]] = {}
    for a in articles:
        for theme, keywords in THEME_CLUSTERS.items():
            if _has_keyword(a.title, keywords):
                theme_counts.setdefault(theme, []).append(a.tone)

    if theme_counts:
        max_theme_count = max(len(v) for v in theme_counts.values())
        concentration_score = min(max_theme_count / max(len(articles), 1), 1.0)
    else:
        concentration_score = 0.0

    # Composite weighted sum — matches plan weights
    net_risk_score = (
        0.30 * volume_score
        + 0.25 * sentiment_score
        + 0.30 * disruption_score
        + 0.15 * concentration_score
    )

    top_headlines = [a.title for a in articles[:5] if a.title]

    # Build theme_counts summary: theme -> (count, avg_tone)
    theme_summary: Dict[str, tuple[int, float]] = {
        theme: (
            len(tones_list),
            round(sum(tones_list) / len(tones_list), 2),
        )
        for theme, tones_list in theme_counts.items()
    }

    return EventFeatureSet(
        article_count=len(articles),
        disruption_count=len(disruption_arts),
        high_severity_count=len(high_sev_arts),
        mean_tone=round(mean_tone, 3),
        disruption_score=round(disruption_score, 3),
        severity_score=round(severity_score, 3),
        sentiment_score=round(sentiment_score, 3),
        volume_score=round(volume_score, 3),
        concentration_score=round(concentration_score, 3),
        net_risk_score=round(net_risk_score, 3),
        top_headlines=top_headlines,
        count_24h=count_24h,
        count_72h=count_72h,
        count_7d=count_7d,
        theme_counts=theme_summary,
    )
