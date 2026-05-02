from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .data_provider import RateDataProvider
from .event_features import build_features
from .event_provider import GDELTEventProvider
from .lane_event_map import get_keywords_for_lane
from .mc_model import MonteCarloShippingForecaster
from .models import (
    ForecastBlock,
    ForecastRequest,
    ForecastResponse,
    FeaturedArticle,
    NewsRiskBlock,
    ArticleVolume,
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
        historical, event_feed = await asyncio.gather(
            self._provider.fetch(req.lane, lookback_days=req.lookback_days),
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

        featured: list[FeaturedArticle] = []
        for art in (event_feed.articles or []):
            title_en, lang = await ensure_english_title(art.title)
            featured.append(FeaturedArticle(
                title_original=art.title,
                title_english=title_en,
                language=lang,
                source=art.source,
                url=art.url,
                published_at=art.published,
                tone=art.tone,
                themes=art.themes,
                shipping_relevance=art.relevance,
                risk_contribution=art.risk_contribution,
                summary_english=generate_article_summary(title_en, art.source, art.tone, art.themes),
                why_it_matters=generate_why_it_matters(title_en, art.themes, art.relevance, art.tone),
                is_congestion_relevant=any("port" in t.lower() or "congestion" in t.lower() for t in art.themes),
                is_oil_relevant=any("oil" in t.lower() or "fuel" in t.lower() or "energy" in t.lower() for t in art.themes),
                is_duty_relevant=any("tariff" in t.lower() or "duty" in t.lower() or "sanction" in t.lower() for t in art.themes),
            ))

        vol = features.article_volume if hasattr(features, "article_volume") else ArticleVolume(
            last_24h=features.count_24h if hasattr(features, "count_24h") else 0,
            last_72h=features.count_72h,
            last_7d=features.count_7d,
            baseline_7d=5,
            volume_vs_baseline=round(features.count_72h / 5, 2),
        )

        theme_breakdown = [
            __import__("shipping_forecast.models", fromlist=["ThemeBreakdown"]).ThemeBreakdown(
                theme=k,
                article_count=n,
                avg_tone=round(avg, 2),
                risk_contribution=0.0,
            )
            for k, (n, avg) in features.theme_counts.items()
        ]

        score_100 = features.net_risk_score * 100.0
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
