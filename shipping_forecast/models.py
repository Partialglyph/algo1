from datetime import date, datetime
from typing import List, Optional

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
    lane: str = Field(..., description="Logical lane or series identifier from your data provider")
    horizon_weeks: int = Field(8, ge=1, le=52)
    num_paths: int = Field(5000, ge=100, le=100_000)
    lookback_days: int = Field(365, ge=60, le=1825)

    @validator("lane")
    def lane_not_empty(cls, v: str) -> str:  # type: ignore[override]
        v = v.strip()
        if not v:
            raise ValueError("lane must not be empty")
        return v


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------

class LaneListResponse(BaseModel):
    """Response model for the /lanes endpoint."""
    lanes: List[str]


# ---------------------------------------------------------------------------
# Forecast block  (Monte Carlo side)
# ---------------------------------------------------------------------------

class ForecastBlock(BaseModel):
    """Pure quantitative forecast output from the Monte Carlo simulation."""
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
# News risk block  (qualitative / GDELT side)
# ---------------------------------------------------------------------------

class FeaturedArticle(BaseModel):
    """A single news article surfaced as a risk signal."""
    title_original: str
    title_english: str
    language: str = "en"
    source: str
    url: str
    published_at: Optional[datetime] = None
    tone: float = 0.0
    themes: List[str] = Field(default_factory=list)
    shipping_relevance: float = Field(0.0, ge=0.0, le=1.0)
    summary_english: str = ""
    why_it_matters: str = ""


class ThemeBreakdown(BaseModel):
    """Aggregated risk contribution for a single theme cluster."""
    theme: str
    article_count: int
    avg_tone: float
    risk_contribution: float  # 0–100 scale, portion of net_risk_score driven by this theme


class ArticleVolume(BaseModel):
    """Rolling article volume counts used for the volume score component."""
    last_24h: int = 0
    last_72h: int = 0
    last_7d: int = 0
    baseline_7d: int = 0        # historical average weekly volume for this lane
    volume_vs_baseline: float = 0.0  # ratio: last_7d / baseline_7d


class NewsRiskBlock(BaseModel):
    """
    Event-driven risk environment derived from GDELT news signals.
    Kept intentionally separate from the Monte Carlo forecast block so that
    the two layers can be consumed, displayed, and updated independently.
    """
    net_risk_score: float = Field(..., ge=0.0, le=100.0, description="Composite risk score 0–100")
    risk_label: str = Field(..., description="Normal | Elevated | Severe")
    risk_summary: str = Field(..., description="One-sentence narrative explaining the score")
    top_drivers: List[str] = Field(default_factory=list, description="Bullet reasons behind the score")
    article_volume: ArticleVolume
    featured_articles: List[FeaturedArticle] = Field(default_factory=list)
    theme_breakdown: List[ThemeBreakdown] = Field(default_factory=list)
    # Internal overlay parameters — consumed by the MC model, also exposed for transparency
    sigma_multiplier: float = 1.0
    delta_mu_daily: float = 0.0


# ---------------------------------------------------------------------------
# Top-level response  (single API call, two clean blocks)
# ---------------------------------------------------------------------------

class ForecastResponse(BaseModel):
    """
    Unified response returned by POST /forecast.
    The 'forecast' block contains all Monte Carlo output.
    The 'news_risk' block contains the qualitative news-driven risk layer.
    Keeping them separate allows the frontend to render two distinct tabs
    without mixing model logic with UI logic.
    """
    forecast: ForecastBlock
    news_risk: Optional[NewsRiskBlock] = None
