from datetime import date, datetime
from typing import List

from pydantic import BaseModel, Field, field_validator


class RatePoint(BaseModel):
    date: date
    value: float = Field(..., gt=0.0)


class HistoricalPoint(BaseModel):
    date: date
    value: float


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


class ForecastRequest(BaseModel):
    lane: str = Field(..., description="Logical lane or series identifier from your data provider")
    horizon_weeks: int = Field(8, ge=1, le=52)
    num_paths: int = Field(5000, ge=100, le=100_000)
    lookback_days: int = Field(365, ge=60, le=1825)

    @field_validator("lane")
    @classmethod
    def lane_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("lane must not be empty")
        return v


class ForecastResponse(BaseModel):
    lane: str
    generated_at: datetime
    horizon_weeks: int
    num_paths: int
    last_observed_date: date
    last_observed_value: float
    historical_points: List[HistoricalPoint]
    daily_forecast: List[DailyForecastPoint]
    weekly_forecast: List[WeeklyForecastPoint]
    annualized_volatility: float


class LaneListResponse(BaseModel):
    lanes: List[str]