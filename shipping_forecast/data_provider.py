from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import List, Optional

import httpx

from .models import RatePoint
from . import settings


class RateDataProvider(ABC):
    @abstractmethod
    async def get_historical_rates(
        self,
        lane: str,
        start_date: date,
        end_date: date,
    ) -> List[RatePoint]:
        """Return historical rate points in ascending date order."""
        raise NotImplementedError


class HttpJsonRateDataProvider(RateDataProvider):
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        date_field: str = settings.FREIGHT_API_DATE_FIELD,
        value_field: str = settings.FREIGHT_API_VALUE_FIELD,
        lane_param: str = settings.FREIGHT_API_LANE_PARAM,
        start_param: str = settings.FREIGHT_API_START_PARAM,
        end_param: str = settings.FREIGHT_API_END_PARAM,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._date_field = date_field
        self._value_field = value_field
        self._lane_param = lane_param
        self._start_param = start_param
        self._end_param = end_param
        self._timeout = timeout_seconds

    async def get_historical_rates(
        self,
        lane: str,
        start_date: date,
        end_date: date,
    ) -> List[RatePoint]:
        params = {
            self._lane_param: lane,
            self._start_param: start_date.isoformat(),
            self._end_param: end_date.isoformat(),
        }
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._base_url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, list):
            raise ValueError("Expected JSON array from freight API")

        points: List[RatePoint] = []
        for item in data:
            try:
                raw_date = item[self._date_field]
                raw_value = float(item[self._value_field])
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Invalid item in freight API response: {item}") from exc

            value = max(raw_value, settings.MIN_PRICE)
            try:
                d = date.fromisoformat(str(raw_date))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Invalid date in freight API response: {raw_date}") from exc

            points.append(RatePoint(date=d, value=value))

        points.sort(key=lambda rp: rp.date)
        return points
