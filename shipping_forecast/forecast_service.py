from __future__ import annotations

from datetime import date, datetime, timedelta

from .data_provider import RateDataProvider
from .mc_model import MonteCarloShippingForecaster
from .models import ForecastRequest, ForecastResponse
from . import settings


class ForecastService:
    def __init__(self, provider: RateDataProvider) -> None:
        self._provider = provider

    async def generate_forecast(self, req: ForecastRequest) -> ForecastResponse:
        today = date.today()
        start_date = today - timedelta(days=req.lookback_days)
        history = await self._provider.get_historical_rates(
            lane=req.lane,
            start_date=start_date,
            end_date=today,
        )

        if len(history) < settings.MIN_DATA_POINTS:
            raise ValueError(f"Insufficient history for lane {req.lane}. Got {len(history)} points.")

        last_point = history[-1]
        forecaster = MonteCarloShippingForecaster(num_paths=req.num_paths)
        calib = forecaster.calibrate(history)
        dates, paths = forecaster.simulate_paths(
            last_price=last_point.value,
            start_date=last_point.date,
            horizon_weeks=req.horizon_weeks,
            calib=calib,
        )
        daily = forecaster.summarize_daily(dates, paths)
        weekly = forecaster.summarize_weekly(daily)
        sigma_annual = forecaster.estimate_annualized_volatility(paths)

        return ForecastResponse(
            lane=req.lane,
            generated_at=datetime.utcnow(),
            horizon_weeks=req.horizon_weeks,
            num_paths=req.num_paths,
            last_observed_date=last_point.date,
            last_observed_value=last_point.value,
            daily_forecast=daily,
            weekly_forecast=weekly,
            annualized_volatility=sigma_annual,
        )
