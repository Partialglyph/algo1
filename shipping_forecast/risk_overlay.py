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
    explanations: list[str] = []

    if score < 0.20:
        regime = "Normal"
        delta_mu = 0.0
        sigma_mult = 1.0
        explanations.append("No significant disruption signals detected in recent coverage.")

    elif score < 0.45:
        regime = "Elevated"
        delta_mu = -0.0003
        sigma_mult = 1.12
        if features.disruption_count > 0:
            explanations.append(
                f"{features.disruption_count} disruption-related articles detected in the past 14 days."
            )
        if features.mean_tone < -1.0:
            explanations.append("News tone for this route region is trending negative.")
        if features.volume_score > 0.4:
            explanations.append(
                f"Article volume in the last 72 hours is elevated vs baseline "
                f"({features.count_72h} articles)."
            )
        explanations.append("Forecast uncertainty band widened by ~12%.")

    else:
        regime = "Severe"
        delta_mu = -0.0010
        sigma_mult = 1.30
        if features.high_severity_count > 0:
            explanations.append(
                f"{features.high_severity_count} high-severity events detected "
                f"(conflict / sanctions / attack) in the past 14 days."
            )
        if features.disruption_count > 0:
            explanations.append(
                f"{features.disruption_count} disruption-related articles detected."
            )
        if features.mean_tone < -2.0:
            explanations.append("News tone strongly negative for this route region.")
        if features.concentration_score > 0.5:
            explanations.append(
                "Coverage is converging on a single disruption theme — "
                "narrative concentration is high."
            )
        explanations.append("Forecast uncertainty band widened by ~30%.")

    if features.top_headlines:
        explanations.append(f'Top signal: "{features.top_headlines[0]}"')

    return RiskOverlay(
        regime_label=regime,
        net_risk_score=round(score, 3),
        delta_mu_daily=delta_mu,
        sigma_multiplier=sigma_mult,
        explanation=explanations,
    )


def build_news_risk_block(
    features: EventFeatureSet,
    overlay: RiskOverlay,
    feed: EventFeed,
) -> NewsRiskBlock:
    """
    Assemble the full NewsRiskBlock from features + overlay.
    This is the object returned in ForecastResponse.news_risk.
    """
    score_100 = round(overlay.net_risk_score * 100, 1)

    # --- Risk summary narrative ---
    if overlay.regime_label == "Normal":
        summary = (
            "Current news coverage shows no meaningful disruption signals for this lane. "
            "The forecast reflects baseline historical volatility."
        )
    elif overlay.regime_label == "Elevated":
        summary = (
            f"Risk is elevated due to {features.disruption_count} disruption-related articles "
            f"and {'negative' if features.mean_tone < -1.0 else 'mixed'} news tone. "
            "The forecast uncertainty band has been widened modestly."
        )
    else:
        summary = (
            f"Risk is severe — {features.high_severity_count} high-severity events detected "
            f"alongside strongly negative news tone (mean tone {features.mean_tone:.1f}). "
            "The forecast uncertainty band has been significantly widened."
        )

    # --- Article volume block ---
    article_volume = ArticleVolume(
        last_24h=features.count_24h,
        last_72h=features.count_72h,
        last_7d=features.count_7d,
        baseline_7d=5,  # soft baseline; can be made lane-specific later
        volume_vs_baseline=round(features.count_7d / 5, 2),
    )

    # --- Featured articles (top 5 with relevance proxy) ---
    featured: list[FeaturedArticle] = []
    for art in (feed.articles or [])[:5]:
        from .event_features import _has_keyword, DISRUPTION_KEYWORDS, HIGH_SEVERITY_KEYWORDS
        relevance = 0.5
        if _has_keyword(art.title, HIGH_SEVERITY_KEYWORDS):
            relevance = 0.95
        elif _has_keyword(art.title, DISRUPTION_KEYWORDS):
            relevance = 0.80

        art_themes = [
            theme for theme, kws in THEME_CLUSTERS.items()
            if _has_keyword(art.title, kws)
        ]

        featured.append(FeaturedArticle(
            title_original=art.title,
            title_english=art.title,  # translation layer added in Phase 3
            language="en",
            source=art.source,
            url=art.url,
            published_at=art.published,
            tone=art.tone,
            themes=art_themes,
            shipping_relevance=relevance,
            summary_english="",      # summarization added in Phase 3
            why_it_matters="",       # narrative generation added in Phase 3
        ))

    # --- Theme breakdown ---
    total_articles = max(features.article_count, 1)
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
        risk_summary=summary,
        top_drivers=overlay.explanation,
        article_volume=article_volume,
        featured_articles=featured,
        theme_breakdown=theme_breakdown,
        sigma_multiplier=overlay.sigma_multiplier,
        delta_mu_daily=overlay.delta_mu_daily,
    )
