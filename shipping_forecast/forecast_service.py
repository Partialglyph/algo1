from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from .data_provider import RateDataProvider
from .event_features import (
    build_features,
    _has_keyword,
    DISRUPTION_KEYWORDS,
    HIGH_SEVERITY_KEYWORDS,
    THEME_CLUSTERS,
)
from .event_provider import GDELTEventProvider
from .lane_event_map import get_keywords_for_lane
from .mc_model import MonteCarloShippingForecaster
from .models import (
    ArticleVolume,
    FeaturedArticle,
    ForecastBlock,
    ForecastRequest,
    ForecastResponse,
    NewsRiskBlock,
    ThemeBreakdown,
)
from .summarizer import (
    generate_article_summary,
    generate_risk_summary,
    generate_top_drivers,
    generate_why_it_matters,
)
from .translation_service import ensure_english_title
from .risk_overlay import compute_overlay

log = logging.getLogger(__name__)


class ForecastService:
    def __init__(self, provider: RateDataProvider) -> None:
        self._provider = provider
        self._gdelt = GDELTEventProvider()

    async def generate_forecast(self, req: ForecastRequest) -> ForecastResponse:
        end_date = date.today()
        start_date = end_date - timedelta(days=req.lookback_days)

        historical, event_feed = await asyncio.gather(
            self._provider.get_historical_rates(req.lane, start_date, end_date),
            self._gdelt.fetch(
                keywords=get_keywords_for_lane(req.lane),
                timespan_hours=72,
                max_articles=12,
            ),
        )

        model = MonteCarloShippingForecaster(
            historical_points=historical,
            horizon_weeks=req.horizon_weeks,
            num_paths=req.num_paths,
        )

        features = build_features(event_feed)
        overlay = compute_overlay(features)

        forecast_block = model.run(
            delta_mu_daily=overlay.delta_mu_daily,
            sigma_multiplier=overlay.sigma_multiplier,
        )

        # Compute article-level fields from title keywords (EventArticle has no themes/relevance)
        total_articles = max(features.article_count, 1)
        score_100 = features.net_risk_score * 100.0
        featured: list[FeaturedArticle] = []
        for art in (event_feed.articles or []):
            title_en, lang = await ensure_english_title(art.title)

            if _has_keyword(art.title, HIGH_SEVERITY_KEYWORDS):
                relevance = 0.95
            elif _has_keyword(art.title, DISRUPTION_KEYWORDS):
                relevance = 0.80
            else:
                relevance = 0.5

            art_themes = [
                theme for theme, kws in THEME_CLUSTERS.items()
                if _has_keyword(art.title, kws)
            ]
            risk_contribution = round((relevance / total_articles) * score_100, 1)

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
                summary_english=generate_article_summary(title_en, art.source, art.tone, art_themes),
                why_it_matters=generate_why_it_matters(title_en, art_themes, relevance, art.tone),
                is_congestion_relevant=any("port" in t.lower() or "congestion" in t.lower() for t in art_themes),
                is_oil_relevant=any("oil" in t.lower() or "fuel" in t.lower() or "energy" in t.lower() for t in art_themes),
                is_duty_relevant=any("tariff" in t.lower() or "duty" in t.lower() or "sanction" in t.lower() for t in art_themes),
            ))

        vol = ArticleVolume(
            last_24h=features.count_24h,
            last_72h=features.count_72h,
            last_7d=features.count_7d,
            baseline_7d=5,
            volume_vs_baseline=round(features.count_72h / 5, 2),
        )

        theme_breakdown: list[ThemeBreakdown] = [
            ThemeBreakdown(
                theme=k,
                article_count=n,
                avg_tone=round(avg, 2),
                risk_contribution=0.0,
            )
            for k, (n, avg) in features.theme_counts.items()
        ]

        regime = overlay.regime_label

        news_risk = NewsRiskBlock(
            net_risk_score=round(score_100, 2),
            risk_label=regime,
            risk_summary=generate_risk_summary(features, regime, score_100),
            top_drivers=generate_top_drivers(features, regime),
            article_volume=vol,
            featured_articles=featured,
            theme_breakdown=theme_breakdown,
            sigma_multiplier=overlay.sigma_multiplier,
            delta_mu_daily=overlay.delta_mu_daily,
        )

        return ForecastResponse(
            forecast=ForecastBlock(
                lane=req.lane,
                generated_at=datetime.now(timezone.utc),
                horizon_weeks=req.horizon_weeks,
                num_paths=req.num_paths,
                last_observed_date=forecast_block.last_observed_date,
                last_observed_value=forecast_block.last_observed_value,
                annualized_volatility=forecast_block.annualized_volatility,
                historical_points=forecast_block.historical_points,
                daily_forecast=forecast_block.daily_forecast,
                weekly_forecast=forecast_block.weekly_forecast,
            ),
            news_risk=news_risk,
        )
