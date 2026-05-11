"""Tests for the Brent crude combination forecaster."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import numpy as np
import pytest

from shipping_forecast.oil_forecast_service import BrentForecaster
from shipping_forecast.models import OilForecastBlock, OilHistoryPoint


def _make_history(n: int = 60, start: float = 80.0, drift: float = 0.001) -> list[OilHistoryPoint]:
    """Generate synthetic Brent history with mild upward drift."""
    base = datetime(2024, 1, 1)
    price = start
    out = []
    rng = np.random.default_rng(42)
    for i in range(n):
        price *= 1.0 + drift + rng.normal(0, 0.01)
        out.append(OilHistoryPoint(
            date=str((base + timedelta(days=i)).date()),
            price=round(max(price, 1.0), 2),
        ))
    return out


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestBrentForecaster:

    def test_output_shape(self):
        f = BrentForecaster(horizon_weeks=4)
        block = run(f.forecast(_make_history(), 80.0, "stable"))
        assert isinstance(block, OilForecastBlock)
        assert len(block.weekly_forecast) == 4

    def test_current_price_stored(self):
        f = BrentForecaster(horizon_weeks=8)
        block = run(f.forecast(_make_history(), 95.50, "rising"))
        assert block.current_price == 95.50
        assert block.current_trend == "rising"

    def test_models_used_non_empty(self):
        f = BrentForecaster(horizon_weeks=8)
        block = run(f.forecast(_make_history(60), 80.0, "stable"))
        assert len(block.models_used) >= 2  # at least futures + risk_adjusted

    def test_combination_within_p10_p90(self):
        f = BrentForecaster(horizon_weeks=8)
        block = run(f.forecast(_make_history(60), 80.0, "stable"))
        for pt in block.weekly_forecast:
            assert pt.p10 <= pt.combination <= pt.p90, (
                f"Week ending {pt.week_end}: combo {pt.combination} outside [{pt.p10}, {pt.p90}]"
            )

    def test_bvar_runs_with_sufficient_history(self):
        f = BrentForecaster(horizon_weeks=8)
        block = run(f.forecast(_make_history(60), 80.0, "stable"))
        assert "bvar" in block.models_used

    def test_graceful_on_short_history(self):
        """With only 5 points BVAR should be skipped, not raise."""
        f = BrentForecaster(horizon_weeks=4)
        block = run(f.forecast(_make_history(5), 80.0, "stable"))
        assert block is not None
        assert "bvar" not in block.models_used

    def test_annualized_vol_positive(self):
        f = BrentForecaster(horizon_weeks=8)
        block = run(f.forecast(_make_history(60), 80.0, "stable"))
        assert block.annualized_volatility > 0.0

    def test_invalid_price_raises(self):
        f = BrentForecaster(horizon_weeks=4)
        with pytest.raises(ValueError, match="current_price"):
            run(f.forecast(_make_history(), -5.0, "stable"))

    def test_empty_history_raises(self):
        f = BrentForecaster(horizon_weeks=4)
        with pytest.raises(ValueError, match="history"):
            run(f.forecast([], 80.0, "stable"))
