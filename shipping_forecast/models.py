from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, validator


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class RatePoint(BaseModel):
    date: date
    value: float = Field(..., gt=0.0)


class DailyForecastPoint(BaseModel):
    date: date
    expected: float
    p05: float
    p50: float
    p95: float


class WeeklyForecastPoint(BaseModel):
    week_end: date
    expected: float
    p05: float
    p50: float
    p95: float


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class ForecastRequest(BaseModel):
    lane: str = Field(..., description="Logical lane identifier")
    horizon_weeks: int = Field(8, ge=1, le=52)
    num_paths: int = Field(5000, ge=100, le=100_000)
    lookback_days: int = Field(365, ge=60, le=1825)

    @validator("lane")
    def lane_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("lane must not be empty")
        return v


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------

class LaneListResponse(BaseModel):
    lanes: List[str]


# ---------------------------------------------------------------------------
# Forecast block  (Monte Carlo)
# ---------------------------------------------------------------------------

class ForecastBlock(BaseModel):
    lane: str
    generated_at: datetime
    horizon_weeks: int
    num_paths: int
    last_observed_date: date
    last_observed_value: float
    annualized_volatility: float
    historical_points: List[RatePoint] = Field(default_factory=list)
    daily_forecast: List[DailyForecastPoint]
    weekly_forecast: List[WeeklyForecastPoint]


# ---------------------------------------------------------------------------
# News risk block
# ---------------------------------------------------------------------------

class FeaturedArticle(BaseModel):
    title_original: str
    title_english: str
    language: str = "en"
    source: str
    url: str
    published_at: Optional[datetime] = None
    tone: float = 0.0
    themes: List[str] = Field(default_factory=list)
    shipping_relevance: float = Field(0.0, ge=0.0, le=1.0)
    risk_contribution: float = Field(0.0)
    summary_english: str = ""
    why_it_matters: str = ""
    is_congestion_relevant: bool = False
    is_oil_relevant: bool = False
    is_duty_relevant: bool = False


class ThemeBreakdown(BaseModel):
    theme: str
    article_count: int
    avg_tone: float
    risk_contribution: float


class ArticleVolume(BaseModel):
    last_24h: int = 0
    last_72h: int = 0
    last_7d: int = 0
    baseline_7d: int = 0
    volume_vs_baseline: float = 0.0


class NewsRiskBlock(BaseModel):
    net_risk_score: float = Field(..., ge=0.0, le=100.0)
    risk_label: str
    risk_summary: str
    top_drivers: List[str] = Field(default_factory=list)
    article_volume: ArticleVolume
    featured_articles: List[FeaturedArticle] = Field(default_factory=list)
    theme_breakdown: List[ThemeBreakdown] = Field(default_factory=list)
    sigma_multiplier: float = 1.0
    delta_mu_daily: float = 0.0


# ---------------------------------------------------------------------------
# Oil, congestion, costs
# ---------------------------------------------------------------------------

class OilSignal(BaseModel):
    benchmark: Literal["Brent", "WTI"] = "Brent"
    price: float
    change_24h_pct: Optional[float] = None
    trend: Literal["rising", "falling", "stable"] = "stable"
    source: str = "EIA"
    fetched_at: Optional[datetime] = None


class CongestionSignal(BaseModel):
    node_type: Literal["port", "chokepoint", "regional"] = "port"
    node_name: str
    lane: str
    congestion_index: float = Field(..., ge=0.0, le=1.0)
    trend: Literal["improving", "stable", "worsening"] = "stable"
    last_updated: Optional[datetime] = None
    note: str = ""


class DutyRate(BaseModel):
    import_country: str
    export_country: str
    lane: str
    rate_pct: float = Field(..., ge=0.0)
    rate_type: str = "ad valorem"
    last_updated: Optional[datetime] = None
    source: str = "stub"


class ClearanceCost(BaseModel):
    lane: str
    currency: str = "USD"
    base_cost: float
    volatility_adjustment: float = 0.0
    last_updated: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Dashboard bundles
# ---------------------------------------------------------------------------

class OverviewBundle(BaseModel):
    lane: str
    current_value: float
    latest_change_pct: Optional[float] = None
    latest_change_absolute: Optional[float] = None
    overall_sentiment: str
    overall_prediction: str
    confidence_score: float = Field(0.8, ge=0.0, le=1.0)
    key_conclusions: List[str]
    risk_regime: str
    oil_price: Optional[float] = None
    oil_trend: Optional[str] = None


class QuantBundle(BaseModel):
    forecast: ForecastBlock
    regime_label: str
    volatility_multiplier: float
    drift_adjustment: float
    live_status: Literal["stable", "volatile", "news-affected"] = "stable"


class NewsBundle(BaseModel):
    risk: NewsRiskBlock
    oil_signals: List[OilSignal] = Field(default_factory=list)
    congestion_signals: List[CongestionSignal] = Field(default_factory=list)
    event_summary: str = ""


class CostBundle(BaseModel):
    duty_rate: Optional[DutyRate] = None
    clearance_cost: Optional[ClearanceCost] = None
    duty_trend: Optional[str] = None
    duty_safety: Literal["low", "medium", "high"] = "medium"
    cost_pressure_summary: str
    cost_drivers: List[str] = Field(default_factory=list)
    total_cost_exposure: float = 0.0


class DashboardResponse(BaseModel):
    overview: OverviewBundle
    quant: QuantBundle
    news: NewsBundle
    costs: CostBundle
    generated_at: datetime


# ---------------------------------------------------------------------------
# Legacy single-response (kept for /forecast endpoint compatibility)
# ---------------------------------------------------------------------------

class ForecastResponse(BaseModel):
    forecast: ForecastBlock
    news_risk: Optional[NewsRiskBlock] = None
