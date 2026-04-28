from datetime import date, timedelta
from typing import List

import pytest

from shipping_forecast.data_provider import RateDataProvider
from shipping_forecast.forecast_service import ForecastService
from shipping_forecast.models import ForecastRequest, RatePoint


class FakeProvider(RateDataProvider):
    def __init__(self, points: List[RatePoint]) -> None:
        self._points = points

    async def get_historical_rates(self, lane, start_date, end_date) -> List[RatePoint]:
        return [p for p in self._points if start_date <= p.date <= end_date]


def make_fake_history(num_days: int = 365) -> List[RatePoint]:
    start = date(2025, 1, 1)
    points: List[RatePoint] = []
    value = 1500.0
    for i in range(num_days):
        value *= 1.0 + 0.0005
        points.append(RatePoint(date=start + timedelta(days=i), value=value))
    return points


@pytest.mark.asyncio
async def test_forecast_service_happy_path():
    history = make_fake_history(365)
    provider = FakeProvider(history)
    service = ForecastService(provider)
    req = ForecastRequest(
        lane="TEST_LANE",
        horizon_weeks=8,
        num_paths=1000,
        lookback_days=365,
    )
    resp = await service.generate_forecast(req)
    assert resp.lane == "TEST_LANE"
    assert resp.horizon_weeks == 8
    assert len(resp.daily_forecast) == 8 * 7
    assert len(resp.weekly_forecast) >= 8
    assert resp.annualized_volatility > 0
    assert resp.last_observed_date == history[-1].date
    assert resp.last_observed_value == history[-1].value
