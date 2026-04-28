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
    """Generic JSON provider kept for backward compatibility.

    You can point this at any REST service that returns a JSON array of
    objects with a date and value field.
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        date_field: str = "date",
        value_field: str = "value",
        lane_param: str = "lane",
        start_param: str = "start_date",
        end_param: str = "end_date",
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
            raw_date = item.get(self._date_field)
            raw_value = item.get(self._value_field)
            if raw_date is None or raw_value is None:
                continue
            value = max(float(raw_value), settings.MIN_PRICE)
            try:
                d = date.fromisoformat(str(raw_date))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Invalid date in freight API response: {raw_date}") from exc
            points.append(RatePoint(date=d, value=value))

        points.sort(key=lambda rp: rp.date)
        return points


class TradingEconomicsProvider(RateDataProvider):
    """Data provider using the free Trading Economics API.

    Trading Economics exposes commodities and indices through REST endpoints.
    A very limited `guest:guest` key works for experimentation, but the
    recommended path is to register for a free developer key.[web:48][web:59][web:61][web:66][web:67]
    """

    def __init__(self, api_key: Optional[str] = None, timeout_seconds: float = 10.0) -> None:
        self._api_key = api_key or settings.TRADING_ECONOMICS_API_KEY
        self._timeout = timeout_seconds

    async def get_historical_rates(
        self,
        lane: str,
        start_date: date,
        end_date: date,
    ) -> List[RatePoint]:
        symbol = self._map_lane_to_symbol(lane)
        if symbol is None:
            raise ValueError(f"Unsupported lane for Trading Economics provider: {lane}")

        params = {
            "c": self._api_key,
            "d1": start_date.isoformat(),
            "d2": end_date.isoformat(),
        }
        url = f"{settings.TRADING_ECONOMICS_API_BASE_URL}/markets/historical/{symbol}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, list):
            raise ValueError("Unexpected response from Trading Economics API")

        points: List[RatePoint] = []
        for item in data:
            # Historical markets endpoint returns fields such as Symbol, Date, Close.[web:60]
            raw_date = item.get("Date") or item.get("date")
            raw_value = item.get("Close") or item.get("close")
            if raw_date is None or raw_value is None:
                continue
            value = max(float(raw_value), settings.MIN_PRICE)
            try:
                d = date.fromisoformat(str(raw_date).split("T")[0])
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Invalid date in Trading Economics response: {raw_date}") from exc
            points.append(RatePoint(date=d, value=value))

        points.sort(key=lambda rp: rp.date)
        return points

    @staticmethod
    def _map_lane_to_symbol(lane: str) -> Optional[str]:
        lane_upper = lane.upper().strip()
        if lane_upper in {"TE_CONTAINERIZED", "CONTAINERIZED_FREIGHT_INDEX"}:
            return settings.TRADING_ECONOMICS_CONTAINERIZED_SYMBOL
        if lane_upper in {"TE_WCI", "WORLD_CONTAINER_INDEX"}:
            return settings.TRADING_ECONOMICS_WCI_SYMBOL
        return None


class CTSCsvProvider(RateDataProvider):
    """Local CSV provider for CTS free global price index data.

    CTS offers free monthly global container price indices and volumes after
    free registration.[web:37]
    You are expected to download the latest CSV and place it at the path
    configured in settings. This provider reads that file and filters by
    date range.
    """

    def __init__(self, csv_path: Optional[str] = None) -> None:
        self._csv_path = csv_path or settings.CTS_FREE_CSV_PATH

    async def get_historical_rates(
        self,
        lane: str,
        start_date: date,
        end_date: date,
    ) -> List[RatePoint]:
        # `lane` is ignored here; CTS free data is typically a single global index.
        import csv
        from pathlib import Path

        path = Path(self._csv_path)
        if not path.exists():
            raise ValueError(f"CTS CSV not found at {path}. Please download and save it.")

        points: List[RatePoint] = []
        with path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_date = row.get(settings.CTS_DATE_COLUMN)
                raw_value = row.get(settings.CTS_VALUE_COLUMN)
                if raw_date is None or raw_value is None:
                    continue
                try:
                    d = date.fromisoformat(str(raw_date))
                except Exception:
                    # Skip malformed rows
                    continue
                if d < start_date or d > end_date:
                    continue
                try:
                    value = max(float(raw_value), settings.MIN_PRICE)
                except Exception:
                    continue
                points.append(RatePoint(date=d, value=value))

        points.sort(key=lambda rp: rp.date)
        return points
