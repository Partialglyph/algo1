from __future__ import annotations

from dataclasses import dataclass

from .event_features import EventFeatureSet, THEME_CLUSTERS
from .event_provider import EventFeed
from .models import (
    ArticleVolume,
    FeaturedArticle,
    NewsRiskBlock,
    ThemeBreakdown,
)
from .summarizer import (
    ensure_english_title,
    generate_article_summary,
    generate_why_it_matters,
    generate_risk_summary,
    generate_top_drivers,
)
from .event_features import _has_keyword, DISRUPTION_KEYWORDS, HIGH_SEVERITY_KEYWORDS


@dataclass
class RiskOverlay:
    """Internal overlay parameters fed back into the Monte Carlo model."""
    regime_label: str
    net_risk_score: float      # 0.0 – 1.0 internal scale
    delta_mu_daily: float
    sigma_multiplier: float
    explanation: list[str]


def compute_overlay(features: EventFeatureSet) -> RiskOverlay:
    """
    Convert event features into Monte Carlo parameter adjustments.
    Three regimes: Normal / Elevated / Severe.
    Volatility is widened more aggressively than drift is shifted.
    """
    score = features.net_risk_score

    if score < 0.20:
        regime = "Normal"
        delta_mu = 0.0
        sigma_mult = 1.0
    elif score < 0.45:
        regime = "Elevated"
        delta_mu = -0.0003
        sigma_mult = 1.12
    else:
        regime = "Severe"
        delta_mu = -0.0010
        sigma_mult = 1.30

    # Generate deterministic explanation bullets from live metrics
    drivers = generate_top_drivers(features, regime)

    return RiskOverlay(
        regime_label=regime,
        net_risk_score=round(score, 3),
        delta_mu_daily=delta_mu,
        sigma_multiplier=sigma_mult,
        explanation=drivers,
    )


def build_news_risk_block(
    features: EventFeatureSet,
    overlay: RiskOverlay,
    feed: EventFeed,
) -> NewsRiskBlock:
    """
    Assemble the full NewsRiskBlock from features + overlay.
    This is the object returned in ForecastResponse.news_risk.
    All text fields are now populated by the summarizer module.
    """
    score_100 = round(overlay.net_risk_score * 100, 1)

    # Narrative risk summary generated from live metrics
    risk_summary = generate_risk_summary(features, overlay.regime_label, score_100)

    # Article volume block
    article_volume = ArticleVolume(
        last_24h=features.count_24h,
        last_72h=features.count_72h,
        last_7d=features.count_7d,
        baseline_7d=5,
        volume_vs_baseline=round(features.count_7d / 5, 2),
    )

    # Featured articles — fully populated including translation + summaries
    total_articles = max(features.article_count, 1)
    featured: list[FeaturedArticle] = []
    for art in (feed.articles or [])[:5]:
        # Determine relevance
        relevance = 0.5
        if _has_keyword(art.title, HIGH_SEVERITY_KEYWORDS):
            relevance = 0.95
        elif _has_keyword(art.title, DISRUPTION_KEYWORDS):
            relevance = 0.80

        # Theme matching
        art_themes = [
            theme for theme, kws in THEME_CLUSTERS.items()
            if _has_keyword(art.title, kws)
        ]

        # Risk contribution: (relevance * recency_weight) as share of score
        risk_contribution = round((relevance / total_articles) * score_100, 1)

        # English title + language tag
        title_en, lang = ensure_english_title(art.title)

        # Generated summary and why-it-matters text
        summary = generate_article_summary(
            title_en, art.source, art.tone, art_themes
        )
        why = generate_why_it_matters(
            title_en, art_themes, relevance, art.tone
        )

        featured.append(FeaturedArticle(
            title_original=art.title,
            title_english=title_en,
            language=lang,
            source=art.source,
            url=art.url,
            published_at=art.published,
            tone=art.tone,
            themes=art_themes,
            shipping_relevance=relevance,
            risk_contribution=risk_contribution,
            summary_english=summary,
            why_it_matters=why,
        ))

    # Theme breakdown
    theme_breakdown: list[ThemeBreakdown] = [
        ThemeBreakdown(
            theme=theme,
            article_count=count,
            avg_tone=avg_tone,
            risk_contribution=round((count / total_articles) * score_100, 1),
        )
        for theme, (count, avg_tone) in sorted(
            features.theme_counts.items(),
            key=lambda x: x[1][0],
            reverse=True,
        )
    ]

    return NewsRiskBlock(
        net_risk_score=score_100,
        risk_label=overlay.regime_label,
        risk_summary=risk_summary,
        top_drivers=overlay.explanation,
        article_volume=article_volume,
        featured_articles=featured,
        theme_breakdown=theme_breakdown,
        sigma_multiplier=overlay.sigma_multiplier,
        delta_mu_daily=overlay.delta_mu_daily,
    )
