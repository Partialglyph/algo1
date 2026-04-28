from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

from . import settings
from .data_provider import (
    HttpJsonRateDataProvider,
    TradingEconomicsProvider,
    CTSCsvProvider,
)
from .forecast_service import ForecastService
from .models import ForecastRequest, ForecastResponse

app = FastAPI(title="Shipping Price Forecast API", version="1.1.0")


def _build_provider() -> object:
    """Construct a RateDataProvider using free data sources.

    Lanes are mapped as follows:
    - "TE_CONTAINERIZED" / "CONTAINERIZED_FREIGHT_INDEX" -> Trading Economics
      Containerized Freight Index.
    - "TE_WCI" / "WORLD_CONTAINER_INDEX" -> Trading Economics World Container Index.
    - "CTS_GLOBAL" -> CTS CSV global price index.

    Any other lane falls back to the generic HttpJsonRateDataProvider, which
    you can point at your own service.
    """

    # If the user explicitly sets USE_CTS, prefer CTS for CTS_GLOBAL lane.
    use_cts = os.getenv("USE_CTS", "false").lower() == "true"

    if use_cts:
        return CTSCsvProvider()

    # Default to Trading Economics provider for known TE lanes.
    return TradingEconomicsProvider(api_key=os.getenv("TRADING_ECONOMICS_API_KEY"))


provider = _build_provider()
service = ForecastService(provider=provider)


@app.post("/forecast", response_model=ForecastResponse)
async def forecast(req: ForecastRequest) -> ForecastResponse:
    try:
        return await service.generate_forecast(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Internal server error") from exc
