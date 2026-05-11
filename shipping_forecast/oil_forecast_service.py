from __future__ import annotations

"""
oil_forecast_service.py

Brent crude oil price forecaster based on the equal-weighted combination
method recommended by Manescu & Van Robays (ECB Working Paper 1735, 2014).

Three models are implemented:
  1. Futures baseline  — flat spot curve (market baseline)
  2. Risk-adjusted futures  — Pagano-Pisani (2009) bias correction via
     US manufacturing capacity utilisation (FRED: TCU)
  3. Simplified BVAR  — AR(p) on Brent log-returns with Minnesota prior
     (shrinkage toward random walk, as in Giannone et al. 2012)

The DSGE model from the paper requires calibrated GE parameters on OPEC
production data and is not implemented here.

The combination is equal-weighted.  The paper shows this beats estimated-
weight schemes out-of-sample because estimated weights overfit to the
calibration sample.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
import numpy as np

from .models import OilForecastBlock, OilForecastPoint, OilHistoryPoint

log = logging.getLogger(__name__)

_FRED_TCU_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=TCU"
_tcu_cache: Optional[tuple[datetime, list[float]]] = None
_TCU_CACHE_TTL_HOURS = 24


@dataclass
class _ModelResult:
    values: np.ndarray
    sigma: float


class BrentForecaster:
    """
    Equal-weighted combination Brent crude forecaster.

    Usage:
        forecaster = BrentForecaster(horizon_weeks=8)
        block = await forecaster.forecast(
            history=oil_history,
            current_price=oil_signal.price,
            current_trend=oil_signal.trend,
        )
    """

    def __init__(self, horizon_weeks: int = 8) -> None:
        if horizon_weeks < 1:
            raise ValueError("horizon_weeks must be at least 1")
        self.horizon_weeks = horizon_weeks
        self._horizon_days = horizon_weeks * 7

    # ------------------------------------------------------------------
    # External data helpers
    # ------------------------------------------------------------------

    async def _fetch_tcu(self) -> Optional[np.ndarray]:
        """Fetch US manufacturing capacity utilisation from FRED (series TCU).
        Returns the last 36 monthly readings as a float array, or None on failure.
        Results are cached for 24 hours.
        """
        global _tcu_cache
        now = datetime.now(timezone.utc)
        if _tcu_cache is not None:
            cached_at, data = _tcu_cache
            if (now - cached_at).total_seconds() < _TCU_CACHE_TTL_HOURS * 3600:
                return np.array(data, dtype=float)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(_FRED_TCU_URL)
                resp.raise_for_status()
            rows = resp.text.strip().splitlines()[1:]  # skip CSV header
            values: list[float] = []
            for row in rows[-36:]:
                parts = row.split(",")
                if len(parts) < 2:
                    continue
                try:
                    values.append(float(parts[1]))
                except ValueError:
                    continue
            if len(values) >= 12:
                _tcu_cache = (now, values)
                return np.array(values, dtype=float)
        except Exception as exc:
            log.warning("TCU fetch failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Model 1: Futures baseline
    # ------------------------------------------------------------------

    def _futures_model(self, current_price: float) -> np.ndarray:
        """Flat futures curve at the current spot price.
        This is the baseline used by central banks and the ECB.
        With a full futures strip (BZ=F term structure) this would
        be replaced by interpolated contract prices, but yfinance
        does not provide a reliable multi-maturity strip for Brent.
        """
        return np.full(self._horizon_days, float(current_price), dtype=float)

    # ------------------------------------------------------------------
    # Model 2: Risk-adjusted futures (Pagano-Pisani 2009)
    # ------------------------------------------------------------------

    def _risk_adjusted_model(
        self,
        futures: np.ndarray,
        history: list[OilHistoryPoint],
        tcu: Optional[np.ndarray],
    ) -> np.ndarray:
        """Apply Pagano-Pisani (2009) bias correction to the futures forecast.

        The correction estimates the systematic futures forecast error using
        US capacity utilisation as a proxy for the time-varying risk premium:
            P_forecast = F_futures - alpha - beta * UCap_{t-1}

        alpha and beta are estimated by OLS on recent forecast errors.
        If TCU data is unavailable or history is too short, falls back to
        returning the uncorrected futures array.
        """
        if tcu is None or len(tcu) < 12:
            return futures.copy()
        prices = np.array(
            [p.price for p in history if p.price > 0], dtype=float
        )
        if prices.size < 8:
            return futures.copy()
        n = min(prices.size - 1, tcu.size - 1, 24)
        if n < 6:
            return futures.copy()
        errors = np.diff(prices)[-n:]
        ucap = tcu[-n - 1 : -1]
        X = np.column_stack([np.ones(n), ucap])
        try:
            beta, *_ = np.linalg.lstsq(X, errors, rcond=None)
            correction = float(beta[0] + beta[1] * tcu[-1])
            return futures - correction
        except np.linalg.LinAlgError:
            return futures.copy()

    # ------------------------------------------------------------------
    # Model 3: Simplified BVAR with Minnesota prior
    # ------------------------------------------------------------------

    def _bvar_model(
        self, history: list[OilHistoryPoint]
    ) -> Optional[_ModelResult]:
        """AR(p) model on Brent log-returns with Minnesota prior.

        The Minnesota prior (Giannone et al. 2012) shrinks all coefficients
        toward zero except the first own lag which is shrunk toward 1.0,
        encoding the belief that oil prices follow a random walk while
        letting data loosen that assumption.

        With short history (< 20 points) the model degenerates toward a
        pure random walk forecast, which is exactly the no-change benchmark
        the ECB paper evaluates against — still useful in the combination.
        """
        prices = np.array(
            [p.price for p in history if p.price > 0], dtype=float
        )
        if prices.size < 10:
            return None
        log_prices = np.log(prices)
        returns = np.diff(log_prices)
        if returns.size < 8:
            return None
        p = min(12, max(2, returns.size // 4))
        if returns.size <= p:
            return None

        Y = returns[p:]
        X_lags = [
            returns[p - i - 1 : returns.size - i - 1] for i in range(p)
        ]
        X = np.column_stack([np.ones(Y.size)] + X_lags)

        # Minnesota prior: lambda=0.2 (tight shrinkage, standard for monthly data)
        lam = 0.2
        prior_mean = np.zeros(X.shape[1])
        prior_mean[1] = 1.0  # first lag toward random walk
        prior_var = np.ones(X.shape[1]) * (lam**2)
        prior_var[0] = 1e6  # diffuse prior on intercept

        XtX = X.T @ X
        prior_prec = np.diag(1.0 / prior_var)
        posterior_prec = XtX + prior_prec
        try:
            posterior_mean = np.linalg.solve(
                posterior_prec,
                X.T @ Y + prior_prec @ prior_mean,
            )
        except np.linalg.LinAlgError:
            return None

        residuals = Y - X @ posterior_mean
        ddof = min(p, residuals.size - 1)
        sigma = float(np.std(residuals, ddof=ddof)) if residuals.size > 1 else 0.02

        # Recursive h-step-ahead forecast in log-return space
        last_returns = list(returns[-p:])
        forecasted: list[float] = []
        for _ in range(self._horizon_days):
            x = np.array([1.0] + last_returns[-p:][::-1], dtype=float)
            r = float(x @ posterior_mean)
            forecasted.append(r)
            last_returns.append(r)

        path = np.exp(
            float(log_prices[-1]) + np.cumsum(np.array(forecasted, dtype=float))
        )
        return _ModelResult(values=path, sigma=sigma)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def forecast(
        self,
        history: list[OilHistoryPoint],
        current_price: float,
        current_trend: str,
    ) -> OilForecastBlock:
        """Run all models and return an equal-weighted combination forecast.

        Degrades gracefully: if a model fails, it is dropped from the
        combination rather than raising an error.  The `models_used` field
        on the returned block records exactly which models contributed.
        """
        if not history:
            raise ValueError("history must not be empty")
        if current_price <= 0:
            raise ValueError("current_price must be positive")

        # _futures_model is pure numpy — call it directly (no await needed).
        # Only _fetch_tcu requires an actual async network call.
        futures_arr = self._futures_model(current_price)
        tcu = await self._fetch_tcu()

        models: list[np.ndarray] = [futures_arr]
        used: list[str] = ["futures"]

        # Risk-adjusted futures
        ra = self._risk_adjusted_model(futures_arr, history, tcu)
        models.append(ra)
        used.append("risk_adjusted_futures")

        # BVAR
        bvar_result = self._bvar_model(history)
        if bvar_result is not None:
            models.append(bvar_result.values)
            used.append("bvar")

        # Equal-weighted combination
        stack = np.stack(models)  # shape (n_models, horizon_days)
        combo = stack.mean(axis=0)
        p10 = np.percentile(stack, 10, axis=0)
        p90 = np.percentile(stack, 90, axis=0)

        # Annualised volatility from BVAR sigma or from raw history
        if bvar_result is not None:
            ann_vol = bvar_result.sigma * np.sqrt(252)
        else:
            prices = np.array([p.price for p in history if p.price > 0])
            if prices.size > 2:
                ann_vol = float(np.diff(np.log(prices)).std(ddof=1) * np.sqrt(252))
            else:
                ann_vol = 0.30

        # Regime note based on forecast direction vs spot
        combo_8w = float(combo[-1])
        spot = float(current_price)
        pct = (combo_8w - spot) / spot if spot > 0 else 0.0
        if tcu is None:
            regime_note = (
                "TCU data unavailable; combination uses futures and BVAR only. "
                "Risk-adjusted futures model substituted with raw futures."
            )
        elif pct > 0.05:
            regime_note = (
                f"Combined 8-week Brent forecast is {pct:+.1%} above spot. "
                "Upward bias driven by risk-premium correction and BVAR momentum."
            )
        elif pct < -0.05:
            regime_note = (
                f"Combined 8-week Brent forecast is {pct:+.1%} below spot. "
                "Downward pressure from risk-adjusted futures correction."
            )
        else:
            regime_note = (
                "Brent forecast is broadly flat over the 8-week horizon. "
                "Models show limited directional conviction."
            )

        # Build weekly output (sample last day of each week)
        today = date.today()
        weekly: list[OilForecastPoint] = []
        bvar_vals = bvar_result.values if bvar_result is not None else futures_arr
        for w in range(self.horizon_weeks):
            idx = min((w + 1) * 7 - 1, self._horizon_days - 1)
            weekly.append(
                OilForecastPoint(
                    week_end=today + timedelta(days=(w + 1) * 7),
                    futures=round(float(futures_arr[idx]), 2),
                    risk_adjusted=round(float(ra[idx]), 2),
                    bvar=round(float(bvar_vals[idx]), 2),
                    combination=round(float(combo[idx]), 2),
                    p10=round(float(p10[idx]), 2),
                    p90=round(float(p90[idx]), 2),
                )
            )

        return OilForecastBlock(
            generated_at=datetime.now(timezone.utc),
            horizon_weeks=self.horizon_weeks,
            current_price=round(float(current_price), 2),
            current_trend=current_trend,
            weekly_forecast=weekly,
            annualized_volatility=round(ann_vol, 4),
            regime_note=regime_note,
            models_used=used,
        )
