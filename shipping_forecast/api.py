from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

from . import settings
from .data_provider import HttpJsonRateDataProvider
from .forecast_service import ForecastService
from .models import ForecastRequest, ForecastResponse

app = FastAPI(title="Shipping Price Forecast API", version="1.0.0")

api_base_url = os.getenv("FREIGHT_API_BASE_URL", settings.FREIGHT_API_BASE_URL)
api_key = os.getenv("FREIGHT_API_KEY", settings.FREIGHT_API_KEY)

provider = HttpJsonRateDataProvider(base_url=api_base_url, api_key=api_key)
service = ForecastService(provider=provider)


@app.post("/forecast", response_model=ForecastResponse)
async def forecast(req: ForecastRequest) -> ForecastResponse:
    try:
        return await service.generate_forecast(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Internal server error") from exc
