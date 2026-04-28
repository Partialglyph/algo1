from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Tuple

import numpy as np

from .models import RatePoint, DailyForecastPoint, WeeklyForecastPoint
from . import settings


@dataclass
class CalibrationResult:
    mu_daily: float
    sigma_daily: float


class MonteCarloShippingForecaster:
    def __init__(self, num_paths: int = settings.DEFAULT_NUM_PATHS, seed: int | None = None) -> None:
        if num_paths < 100:
            raise ValueError("num_paths must be at least 100")
        self._num_paths = num_paths
        self._rng = np.random.default_rng(seed)

    @staticmethod
    def _compute_log_returns(points: List[RatePoint]) -> np.ndarray:
        values = np.array([p.value for p in points], dtype=float)
        if values.size < settings.MIN_DATA_POINTS:
            raise ValueError(f"Not enough data points for calibration, got {values.size}")
        if np.any(values <= 0):
            raise ValueError("All prices must be positive to compute log returns")
        log_values = np.log(values)
        returns = np.diff(log_values)
        return returns

    def calibrate(self, points: List[RatePoint]) -> CalibrationResult:
        returns = self._compute_log_returns(points)
        mu_daily = float(returns.mean())
        sigma_daily = float(returns.std(ddof=1))
        if sigma_daily <= 0:
            raise ValueError("Calibrated volatility is non‑positive, check input data")
        return CalibrationResult(mu_daily=mu_daily, sigma_daily=sigma_daily)

    def simulate_paths(
        self,
        last_price: float,
        start_date: date,
        horizon_weeks: int,
        calib: CalibrationResult,
    ) -> Tuple[List[date], np.ndarray]:
        if last_price <= 0:
            raise ValueError("last_price must be positive")
        num_days = horizon_weeks * 7
        dates = [start_date + timedelta(days=i + 1) for i in range(num_days)]

        dt = settings.DAY_FRACTION
        mu = calib.mu_daily
        sigma = calib.sigma_daily

        z = self._rng.standard_normal(size=(self._num_paths, num_days))
        increments = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
        log_paths = np.cumsum(increments, axis=1)
        paths = last_price * np.exp(log_paths)
        paths = np.maximum(paths, settings.MIN_PRICE)
        return dates, paths

    @staticmethod
    def summarize_daily(dates: List[date], paths: np.ndarray) -> List[DailyForecastPoint]:
        if paths.ndim != 2:
            raise ValueError("paths must be a 2D array of shape (num_paths, num_days)")
        num_paths, num_days = paths.shape
        if num_days != len(dates):
            raise ValueError("dates length must match num_days")
        expected = paths.mean(axis=0)
        p05 = np.percentile(paths, 5, axis=0)
        p50 = np.percentile(paths, 50, axis=0)
        p95 = np.percentile(paths, 95, axis=0)

        daily_points: List[DailyForecastPoint] = []
        for i, d in enumerate(dates):
            daily_points.append(
                DailyForecastPoint(
                    date=d,
                    expected=float(expected[i]),
                    p05=float(p05[i]),
                    p50=float(p50[i]),
                    p95=float(p95[i]),
                )
            )
        return daily_points

    @staticmethod
    def summarize_weekly(daily: List[DailyForecastPoint]) -> List[WeeklyForecastPoint]:
        if not daily:
            return []
        weekly: List[WeeklyForecastPoint] = []
        current_chunk: List[DailyForecastPoint] = []
        current_start = daily[0].date

        for point in daily:
            current_chunk.append(point)
            if (point.date - current_start).days >= 6:
                weekly.append(MonteCarloShippingForecaster._aggregate_chunk(current_chunk))
                current_chunk = []
                current_start = point.date + timedelta(days=1)

        if current_chunk:
            weekly.append(MonteCarloShippingForecaster._aggregate_chunk(current_chunk))

        return weekly

    @staticmethod
    def _aggregate_chunk(chunk: List[DailyForecastPoint]) -> WeeklyForecastPoint:
        week_end = max(p.date for p in chunk)
        last = chunk[-1]
        return WeeklyForecastPoint(
            week_end=week_end,
            expected=float(last.expected),
            p05=float(last.p05),
            p50=float(last.p50),
            p95=float(last.p95),
        )

    @staticmethod
    def estimate_annualized_volatility(paths: np.ndarray) -> float:
        if paths.ndim != 2 or paths.shape[1] < 2:
            raise ValueError("Need at least two days to estimate volatility")
        log_paths = np.log(np.maximum(paths, settings.MIN_PRICE))
        returns = np.diff(log_paths, axis=1)
        sigma_daily = float(returns.std(ddof=1))
        trading_days_per_year = 252.0
        sigma_annual = sigma_daily * np.sqrt(trading_days_per_year)
        return sigma_annual
