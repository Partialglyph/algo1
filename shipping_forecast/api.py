from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
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
from .oil_forecast_service import BrentForecaster
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

provider = None
service = None
_gdelt = None
_oil_forecaster = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup warm-up:
      1. Warm numpy BLAS thread pool (eliminates first-call linalg overhead).
      2. Pre-fetch FRED TCU into the 24h cache.
      3. Fire one dummy dashboard call so yfinance, GDELT, and the
         rate data provider are all fully initialised before the first
         real user request arrives.

    Set WARMUP_LANE env var to control which lane is used (default: shanghai_la).
    On a public domain, uvicorn starts before any traffic hits, so by the
    time DNS resolves and a browser loads the frontend, the warm-up is done.
    """
    global provider, service, _gdelt, _oil_forecaster

    provider = _build_provider()
    service = ForecastService(provider=provider)
    _gdelt = GDELTEventProvider()
    _oil_forecaster = BrentForecaster(horizon_weeks=8)

    # 1. Warm BLAS
    _ = np.linalg.solve(np.eye(4), np.ones(4))
    log.info("[warmup] numpy BLAS thread pool initialised")

    # 2. Pre-fetch TCU
    try:
        await _oil_forecaster._fetch_tcu()
        log.info("[warmup] FRED TCU cache populated")
    except Exception as exc:
        log.warning("[warmup] TCU pre-fetch failed (non-fatal): %s", exc)

    # 3. Dummy dashboard call to warm all external connections
    warmup_lane = os.getenv("WARMUP_LANE", "shanghai_la")
    try:
        warmup_req = ForecastRequest(
            lane=warmup_lane,
            horizon_weeks=8,
            num_paths=200,   # minimal paths -- just enough to exercise the stack
            lookback_days=90,
        )
        await _run_dashboard(warmup_req)
        log.info("[warmup] dashboard pre-warm complete (lane=%s)", warmup_lane)
    except Exception as exc:
        log.warning("[warmup] dashboard pre-warm failed (non-fatal): %s", exc)

    yield
    # Shutdown: nothing to clean up currently


def _build_provider():
    use_cts = os.getenv("USE_CTS", "false").lower() == "true"
    if use_cts:
        return CTSCsvProvider()
    te_key = os.getenv("TRADING_ECONOMICS_API_KEY", "")
    if te_key:
        return TradingEconomicsProvider(api_key=te_key)
    return ExcelDataProvider()


app = FastAPI(
    title="Shipping Price Forecast API",
    version="2.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://demo.owenkan.com",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        log.exception("list_lanes error")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.post("/forecast", response_model=ForecastResponse)
async def forecast(req: ForecastRequest) -> ForecastResponse:
    try:
        return await service.generate_forecast(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("forecast error")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


async def _run_dashboard(req: ForecastRequest) -> DashboardResponse:
    """
    Core dashboard logic extracted so it can be called by both the
    HTTP handler and the startup warm-up without going through HTTP.
    """
    forecast_resp, event_feed, oil_result = await asyncio.gather(
        service.generate_forecast(req),
        _gdelt.fetch(
            keywords=get_keywords_for_lane(req.lane),
            timespan_hours=72,
            max_articles=12,
        ),
        fetch_oil_signal(),
    )

    oil_signal, oil_history = oil_result

    oil_forecast = await _oil_forecaster.forecast(
        history=oil_history,
        current_price=oil_signal.price,
        current_trend=oil_signal.trend,
    )

    fc: ForecastBlock = forecast_resp.forecast

    features = build_features(event_feed)
    overlay = compute_overlay(features)
    score_100 = round(features.net_risk_score * 100.0, 2)
    regime = overlay.regime_label

    from .event_features import _has_keyword, DISRUPTION_KEYWORDS, HIGH_SEVERITY_KEYWORDS, THEME_CLUSTERS
    total_articles = max(features.article_count, 1)
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
        prediction = "Slight upward bias -- not strongly directional."
    elif pct_chg < -10:
        sentiment = "Very Bearish"
        prediction = "Strong downside risk in rates over the forecast horizon."
    elif pct_chg < -5:
        sentiment = "Bearish"
        prediction = "Meaningful downside pressure expected over coming weeks."
    else:
        sentiment = "Neutral"
        prediction = "Largely neutral to sideways outlook."

    oil_8w_combo = (
        oil_forecast.weekly_forecast[-1].combination
        if oil_forecast.weekly_forecast else oil_signal.price
    )
    oil_pct = (
        (oil_8w_combo - oil_signal.price) / oil_signal.price * 100.0
        if oil_signal.price > 0 else 0.0
    )

    key_conclusions: list[str] = [
        f"Current freight index: {last_val:,.0f}",
        f"8-week median forecast: {p50_8w:,.0f} ({pct_chg:+.1f}%)",
        f"Annualised volatility: {round(fc.annualized_volatility * 100, 1)}%",
        f"News risk regime: {regime} (score {score_100:.0f}/100)",
        f"Brent crude spot: ${oil_signal.price:.2f}/bbl ({oil_signal.trend})",
        f"Brent 8-week forecast: ${oil_8w_combo:.2f}/bbl ({oil_pct:+.1f}%) [{', '.join(oil_forecast.models_used)}]",
    ]
    if score_100 >= 50:
        key_conclusions.append(
            f"Risk elevated -- {len([a for a in featured if a.risk_contribution > 0])} "
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

    quant = QuantBundle(
        forecast=fc,
        regime_label=regime,
        volatility_multiplier=overlay.sigma_multiplier,
        drift_adjustment=overlay.delta_mu_daily,
        live_status="news-affected" if score_100 >= 25 else "stable",
        oil_forecast=oil_forecast,
    )

    congestion = get_congestion_signals(req.lane, score_100)
    news = NewsBundle(
        risk=news_risk_block,
        oil_signals=[oil_signal],
        oil_history=oil_history,
        congestion_signals=congestion,
        event_summary=(
            f"{regime} risk environment. "
            f"{features.count_72h} articles matched in 72 h. "
            f"Oil spot: ${oil_signal.price:.2f}/bbl ({oil_signal.trend}). "
            f"Oil 8-week combined forecast: ${oil_8w_combo:.2f}/bbl ({oil_pct:+.1f}%). "
            f"Congestion at {congestion[0].node_name if congestion else 'key nodes'}: "
            f"{congestion[0].trend if congestion else 'data unavailable'}."
        ),
    )

    costs = build_cost_bundle(req.lane, oil_price=oil_signal.price)

    return DashboardResponse(
        overview=overview,
        quant=quant,
        news=news,
        costs=costs,
        generated_at=datetime.now(timezone.utc),
    )


import sqlite3
from datetime import date as _date
from concurrent.futures import ThreadPoolExecutor

_TRENDS_PATH    = Path(__file__).parent.parent / "trends.json"
_DB_PATH        = Path(__file__).parent.parent / "trends.db"
_CUSTOM_PATH    = Path(__file__).parent.parent / "fashion_trends" / "custom_keywords.json"
_DICT_PATH      = Path(__file__).parent.parent / "fashion_trends" / "dictionary.json"
_search_executor = ThreadPoolExecutor(max_workers=2)


def _get_db() -> sqlite3.Connection:
    """Open a short-lived DB connection (thread-safe, per-request)."""
    return sqlite3.connect(_DB_PATH, check_same_thread=False)


def _base_keywords() -> set[str]:
    """Return the set of terms already in dictionary.json (lowercase)."""
    try:
        d = json.loads(_DICT_PATH.read_text())
        return {kw.lower() for lst in d.values() for kw in lst}
    except Exception:
        return set()


def _scrape_term_rss_sync(term: str) -> None:
    """
    Synchronous RSS-only scrape for a single term.
    Runs in a thread pool so it doesn't block the event loop.
    """
    try:
        import feedparser  # type: ignore
    except ImportError:
        log.warning("feedparser not installed — search scrape skipped")
        return

    RSS_FEEDS = [
        ("vogue",        "media", "https://www.vogue.com/feed/rss"),
        ("whowhatwear",  "media", "https://www.whowhatwear.com/feeds/all.rss"),
        ("harpersbazaar","media", "https://www.harpersbazaar.com/feed/"),
        ("elle",         "media", "https://www.elle.com/feed/"),
        ("refinery29",   "media", "https://www.refinery29.com/en-us/rss.xml"),
    ]

    today = _date.today().isoformat()
    conn  = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, keyword TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            site_source TEXT NOT NULL, source_group TEXT NOT NULL DEFAULT 'retail',
            UNIQUE(date, keyword, site_source)
        )
    """)

    for site, group, url in RSS_FEEDS:
        try:
            feed  = feedparser.parse(url)
            texts = []
            for entry in feed.entries[:60]:
                texts.append(getattr(entry, "title",   "") or "")
                texts.append(getattr(entry, "summary", "") or "")
                for c in getattr(entry, "content", []):
                    texts.append(c.get("value", "") or "")
            combined = " ".join(texts).lower()
            cnt = combined.count(term.lower())
            conn.execute(
                """INSERT INTO daily_counts (date, keyword, count, site_source, source_group)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(date, keyword, site_source) DO UPDATE SET count=excluded.count""",
                (today, term, cnt, site, group),
            )
            conn.commit()
        except Exception as exc:
            log.warning("search RSS %s failed: %s", site, exc)

    conn.close()

    # Persist to custom_keywords.json for future nightly scrapes
    try:
        existing: list[str] = json.loads(_CUSTOM_PATH.read_text()) if _CUSTOM_PATH.exists() else []
        if term.lower() not in [k.lower() for k in existing]:
            existing.append(term)
            _CUSTOM_PATH.write_text(json.dumps(existing, indent=2))
    except Exception as exc:
        log.warning("Could not update custom_keywords.json: %s", exc)


def _build_search_result(term: str, found_in_cache: bool) -> dict:
    today     = _date.today().isoformat()
    seven_ago = str(_date.fromordinal(_date.today().toordinal() - 7))
    conn      = _get_db()

    rows = conn.execute(
        "SELECT site_source, source_group, count FROM daily_counts WHERE date=? AND keyword=?",
        (today, term),
    ).fetchall()

    today_count      = sum(r[2] for r in rows)
    source_breakdown = {r[0]: r[2] for r in rows if r[2] > 0}

    # 7-day average
    hist = conn.execute(
        """SELECT SUM(count) FROM daily_counts
           WHERE date>=? AND date<? AND keyword=? GROUP BY date""",
        (seven_ago, today, term),
    ).fetchall()
    totals = [r[0] for r in hist if r[0]]
    avg_7d = round(sum(totals) / len(totals), 1) if totals else float(today_count)

    # Velocity
    trend_pct = round(((today_count - avg_7d) / avg_7d) * 100, 1) if avg_7d else 0.0

    # Flag
    flag = "Rising" if trend_pct > 15 else "Fading" if trend_pct < -15 else "Staple"

    # Sparkline
    spark_rows = conn.execute(
        """SELECT date, SUM(count) FROM daily_counts
           WHERE date>=? AND keyword=? GROUP BY date ORDER BY date""",
        (seven_ago, term),
    ).fetchall()
    sparkline = [r[1] for r in spark_rows]
    while len(sparkline) < 7:
        sparkline.insert(0, 0)

    conn.close()

    return {
        "term":            term,
        "scrape_date":     today,
        "found_in_cache":  found_in_cache,
        "today_count":     today_count,
        "avg_7d":          avg_7d,
        "trend_pct":       trend_pct,
        "flag":            flag,
        "sparkline":       sparkline[-7:],
        "source_breakdown": source_breakdown,
        "is_custom":       term.lower() not in _base_keywords(),
    }


@app.get("/trends")
async def get_trends():
    """
    Serve pre-computed fashion/retail trend data from trends.json.
    Updated daily by the GitHub Actions scrape workflow.
    """
    if not _TRENDS_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Trend data not yet generated. Check back after the daily scrape.",
        )
    try:
        return json.loads(_TRENDS_PATH.read_text())
    except Exception as exc:
        log.exception("Failed to read trends.json")
        raise HTTPException(status_code=500, detail="Trend data read error.") from exc


@app.get("/trends/search")
async def search_trend(q: str):
    """
    Search for any fashion keyword.
    Phase 1: return from today's DB cache instantly if available.
    Phase 2: run a lightweight RSS-only scrape (~3-5s) if not cached.
    Persists new terms to custom_keywords.json for future daily tracking.
    """
    term = q.strip().lower()
    if not term:
        raise HTTPException(status_code=400, detail="q parameter is required")

    today = _date.today().isoformat()

    # Phase 1: check cache
    if _DB_PATH.exists():
        conn = _get_db()
        cached = conn.execute(
            "SELECT COUNT(*) FROM daily_counts WHERE date=? AND keyword=?",
            (today, term),
        ).fetchone()[0]
        conn.close()
    else:
        cached = 0

    found_in_cache = cached > 0

    if not found_in_cache:
        # Phase 2: RSS scrape in thread pool (non-blocking)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_search_executor, _scrape_term_rss_sync, term)

    try:
        return _build_search_result(term, found_in_cache)
    except Exception as exc:
        log.exception("search_trend build result failed")
        raise HTTPException(status_code=500, detail="Search result error.") from exc


@app.post("/dashboard", response_model=DashboardResponse)
async def dashboard(req: ForecastRequest) -> DashboardResponse:
    """
    Four-tab dashboard endpoint.
    Returns: overview, quant (incl. oil_forecast), news, costs.
    """
    try:
        return await _run_dashboard(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Dashboard error")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
