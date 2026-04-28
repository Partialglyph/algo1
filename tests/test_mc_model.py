from datetime import date, timedelta
from typing import List

import numpy as np

from shipping_forecast.mc_model import MonteCarloShippingForecaster, CalibrationResult
from shipping_forecast.models import RatePoint


def make_linear_series(
    start_date: date,
    num_days: int,
    start_value: float = 1000.0,
    daily_increment: float = 5.0,
) -> List[RatePoint]:
    return [
        RatePoint(date=start_date + timedelta(days=i), value=start_value + i * daily_increment)
        for i in range(num_days)
    ]


def test_calibration_produces_positive_sigma():
    start = date(2025, 1, 1)
    points = make_linear_series(start, num_days=60)
    forecaster = MonteCarloShippingForecaster(num_paths=500)
    calib = forecaster.calibrate(points)
    assert calib.sigma_daily > 0


def test_simulate_paths_shape_and_positive():
    start = date(2025, 3, 1)
    last_price = 2000.0
    forecaster = MonteCarloShippingForecaster(num_paths=1000, seed=42)
    calib = CalibrationResult(mu_daily=0.001, sigma_daily=0.02)
    dates, paths = forecaster.simulate_paths(last_price, start, horizon_weeks=8, calib=calib)
    assert len(dates) == 8 * 7
    assert paths.shape == (1000, 8 * 7)
    assert np.all(paths > 0)


def test_daily_summary_matches_paths_length():
    start = date(2025, 5, 1)
    forecaster = MonteCarloShippingForecaster(num_paths=500, seed=1)
    calib = CalibrationResult(mu_daily=0.0, sigma_daily=0.01)
    dates, paths = forecaster.simulate_paths(1500.0, start, horizon_weeks=4, calib=calib)
    daily = forecaster.summarize_daily(dates, paths)
    assert len(daily) == len(dates)
    assert daily[0].date == dates[0]
    assert daily[-1].date == dates[-1]


def test_annualized_volatility_reasonable():
    start = date(2025, 6, 1)
    forecaster = MonteCarloShippingForecaster(num_paths=500, seed=123)
    calib = CalibrationResult(mu_daily=0.0, sigma_daily=0.02)
    _, paths = forecaster.simulate_paths(1800.0, start, horizon_weeks=8, calib=calib)
    sigma_annual = forecaster.estimate_annualized_volatility(paths)
    assert sigma_annual > 0
    assert sigma_annual < 1.0
