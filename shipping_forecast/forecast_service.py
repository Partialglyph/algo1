from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

from .data_provider import RateDataProvider
from .event_features import build_features
from .event_provider import GDELTEventProvider
from .mc_model import CalibrationResult, MonteCarloShippingForecaster
from .models import EventRiskResponse, ForecastRequest, ForecastResponse
from .risk_overlay import compute_overlay
from . import settings


class ForecastService:
    def __init__(self, provider: RateDataProvider) -> None:
        self._provider = provider
        self._event_provider = GDELTEventProvider(lookback_days=14, max_articles=50)

    async def generate_forecast(self, req: ForecastRequest) -> ForecastResponse:
        today = date.today()
        start_date = today - timedelta(days=req.lookback_days)

        # Fetch historical rates and GDELT events concurrently.
        history_task = self._provider.get_historical_rates(
            lane=req.lane,
            start_date=start_date,
            end_date=today,
        )
        event_task = self._event_provider.fetch(lane=req.lane)

        history, event_feed = await asyncio.gather(history_task, event_task)

        if len(history) < settings.MIN_DATA_POINTS:
            raise ValueError(
                f"Insufficient history for lane '{req.lane}'. "
                f"Got {len(history)} points, need {settings.MIN_DATA_POINTS}."
            )

        # --- Baseline calibration ---
        last_point = history[-1]
        forecaster = MonteCarloShippingForecaster(num_paths=req.num_paths)
        calib = forecaster.calibrate(history)

        # --- Event risk overlay ---
        features = build_features(event_feed)
        overlay = compute_overlay(features)

        # Apply overlay to calibration parameters.
        adjusted_calib = CalibrationResult(
            mu_daily=calib.mu_daily + overlay.delta_mu_daily,
            sigma_daily=calib.sigma_daily * overlay.sigma_multiplier,
        )

        event_risk_response = EventRiskResponse(
            regime_label=overlay.regime_label,
            net_risk_score=overlay.net_risk_score,
            sigma_multiplier=overlay.sigma_multiplier,
            delta_mu_daily=overlay.delta_mu_daily,
            explanation=overlay.explanation,
            article_count=features.article_count,
            disruption_count=features.disruption_count,
            top_headlines=features.top_headlines,
        )

        # --- Simulation with adjusted parameters ---
        dates, paths = forecaster.simulate_paths(
            last_price=last_point.value,
            start_date=last_point.date,
            horizon_weeks=req.horizon_weeks,
            calib=adjusted_calib,
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
            historical_points=history,
            daily_forecast=daily,
            weekly_forecast=weekly,
            annualized_volatility=sigma_annual,
            event_risk=event_risk_response,
        )
