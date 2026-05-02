from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import settings
from .congestion_service import get_congestion_signals
from .cost_service import build_cost_bundle
from .data_provider import (
    CTSCsvProvider,
    ExcelDataProvider,
    HttpJsonRateDataProvider,
    TradingEconomicsProvider,
)
from .event_features import build_features
from .event_provider import GDELTEventProvider
from .forecast_service import ForecastService
from .lane_event_map import get_keywords_for_lane
from .mc_model import MonteCarloShippingForecaster
from .models import (
    ArticleVolume,
    CostBundle,
    DashboardResponse,
    FeaturedArticle,
    ForecastBlock,
    ForecastRequest,
    ForecastResponse,
    LaneListResponse,
    NewsBundle,
    NewsRiskBlock,
    OverviewBundle,
    QuantBundle,
    ThemeBreakdown,
)
from .oil_service import fetch_oil_signal
from .risk_overlay import compute_overlay
from .summarizer import (
    generate_article_summary,
    generate_risk_summary,
    generate_top_drivers,
    generate_why_it_matters,
)
from .translation_service import ensure_english_title

log = logging.getLogger(__name__)

app = FastAPI(title="Shipping Price Forecast API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_provider():
    use_cts = os.getenv("USE_CTS", "false").lower() == "true"
    if use_cts:
        return CTSCsvProvider()
    te_key = os.getenv("TRADING_ECONOMICS_API_KEY", "")
    if te_key:
        return TradingEconomicsProvider(api_key=te_key)
    return ExcelDataProvider()


provider = _build_provider()
service = ForecastService(provider=provider)
_gdelt = GDELTEventProvider()


@app.get("/lanes", response_model=LaneListResponse)
async def list_lanes() -> LaneListResponse:
    try:
        if not hasattr(provider, "list_lanes"):
            raise ValueError("Configured provider does not support lane listing")
        lanes = await provider.list_lanes()
        return LaneListResponse(lanes=lanes)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.post("/forecast", response_model=ForecastResponse)
async def forecast(req: ForecastRequest) -> ForecastResponse:
    try:
        return await service.generate_forecast(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.post("/dashboard", response_model=DashboardResponse)
async def dashboard(req: ForecastRequest) -> DashboardResponse:
    """
    Four-tab dashboard endpoint.
    Returns: overview, quant, news, costs — all pre-computed, frontend-ready.
    """
    try:
        # --- Parallel fetch: forecast + events + oil ---
        forecast_resp, event_feed, oil_signal = await asyncio.gather(
            service.generate_forecast(req),
            _gdelt.fetch(
                keywords=get_keywords_for_lane(req.lane),
                timespan_hours=72,
                max_articles=12,
            ),
            fetch_oil_signal(),
        )

        fc: ForecastBlock = forecast_resp.forecast
        nr: NewsRiskBlock = forecast_resp.news_risk

        # Recompute features from freshly fetched event feed
        features = build_features(event_feed)
        overlay = compute_overlay(features)
        score_100 = round(features.net_risk_score * 100.0, 2)
        regime = overlay.regime_label

        # --- Translate articles ---
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

        vol = ArticleVolume(
            last_24h=getattr(features, "count_24h", 0),
            last_72h=features.count_72h,
            last_7d=features.count_7d,
            baseline_7d=5,
            volume_vs_baseline=round(features.count_72h / 5, 2),
        )

        theme_bd = [
            ThemeBreakdown(
                theme=k,
                article_count=n,
                avg_tone=round(avg, 2),
                risk_contribution=0.0,
            )
            for k, (n, avg) in features.theme_counts.items()
        ]

        news_risk_block = NewsRiskBlock(
            net_risk_score=score_100,
            risk_label=regime,
            risk_summary=generate_risk_summary(features, regime, score_100),
            top_drivers=generate_top_drivers(features, regime),
            article_volume=vol,
            featured_articles=featured,
            theme_breakdown=theme_bd,
            sigma_multiplier=overlay.sigma_multiplier,
            delta_mu_daily=overlay.delta_mu_daily,
        )

        # --- OVERVIEW ---
        last_val = fc.last_observed_value
        p50_8w = fc.weekly_forecast[-1].p50 if fc.weekly_forecast else last_val
        pct_chg = round((p50_8w - last_val) / last_val * 100, 2) if last_val else None

        if pct_chg is None:
            sentiment = "Neutral"
            prediction = "Insufficient data."
        elif pct_chg > 10:
            sentiment = "Very Bullish"
            prediction = "Strong upside momentum in freight rates over the 8-week horizon."
        elif pct_chg > 5:
            sentiment = "Bullish"
            prediction = "Mild upside bias in the forecast horizon."
        elif pct_chg > 1:
            sentiment = "Slightly Bullish"
            prediction = "Slight upward bias — not strongly directional."
        elif pct_chg < -10:
            sentiment = "Very Bearish"
            prediction = "Strong downside risk in rates over the forecast horizon."
        elif pct_chg < -5:
            sentiment = "Bearish"
            prediction = "Meaningful downside pressure expected over coming weeks."
        else:
            sentiment = "Neutral"
            prediction = "Largely neutral to sideways outlook."

        key_conclusions: list[str] = [
            f"Current freight index: {last_val:,.0f}",
            f"8-week median forecast: {p50_8w:,.0f} ({pct_chg:+.1f}%)",
            f"Annualised volatility: {round(fc.annualized_volatility * 100, 1)}%",
            f"News risk regime: {regime} (score {score_100:.0f}/100)",
            f"Brent crude: ${oil_signal.price:.2f}/bbl ({oil_signal.trend})",
        ]
        if score_100 >= 50:
            key_conclusions.append(
                f"Risk elevated — {len([a for a in featured if a.risk_contribution > 0])} "
                f"articles flagged as operationally relevant"
            )

        overview = OverviewBundle(
            lane=req.lane,
            current_value=last_val,
            latest_change_pct=pct_chg,
            latest_change_absolute=round(p50_8w - last_val, 2),
            overall_sentiment=sentiment,
            overall_prediction=prediction,
            confidence_score=0.80,
            key_conclusions=key_conclusions,
            risk_regime=regime,
            oil_price=oil_signal.price,
            oil_trend=oil_signal.trend,
        )

        # --- QUANT ---
        quant = QuantBundle(
            forecast=fc,
            regime_label=regime,
            volatility_multiplier=overlay.sigma_multiplier,
            drift_adjustment=overlay.delta_mu_daily,
            live_status="news-affected" if score_100 >= 25 else "stable",
        )

        # --- NEWS ---
        congestion = get_congestion_signals(req.lane, score_100)
        news = NewsBundle(
            risk=news_risk_block,
            oil_signals=[oil_signal],
            congestion_signals=congestion,
            event_summary=(
                f"{regime} risk environment. "
                f"{features.count_72h} articles matched in 72 h. "
                f"Oil: ${oil_signal.price:.2f}/bbl ({oil_signal.trend}). "
                f"Congestion at {congestion[0].node_name if congestion else 'key nodes'}: "
                f"{congestion[0].trend if congestion else 'data unavailable'}."
            ),
        )

        # --- COSTS ---
        costs = build_cost_bundle(req.lane, oil_price=oil_signal.price)

        return DashboardResponse(
            overview=overview,
            quant=quant,
            news=news,
            costs=costs,
            generated_at=datetime.now(timezone.utc),
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Dashboard error")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
