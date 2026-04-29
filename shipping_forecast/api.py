from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import settings
from .data_provider import (
    HttpJsonRateDataProvider,
    TradingEconomicsProvider,
    CTSCsvProvider,
    ExcelDataProvider,
)
from .forecast_service import ForecastService
from .models import ForecastRequest, ForecastResponse

app = FastAPI(title="Shipping Price Forecast API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_provider() -> object:
    use_cts = os.getenv("USE_CTS", "false").lower() == "true"
    if use_cts:
        return CTSCsvProvider()
    te_key = os.getenv("TRADING_ECONOMICS_API_KEY", "")
    if te_key:
        return TradingEconomicsProvider(api_key=te_key)
    return ExcelDataProvider()


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