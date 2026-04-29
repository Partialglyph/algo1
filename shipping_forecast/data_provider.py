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

    async def list_lanes(self) -> List[str]:
        raise NotImplementedError("This provider does not support lane discovery")


class HttpJsonRateDataProvider(RateDataProvider):
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
            except Exception as exc:
                raise ValueError(f"Invalid date in freight API response: {raw_date}") from exc
            points.append(RatePoint(date=d, value=value))

        points.sort(key=lambda rp: rp.date)
        return points


class TradingEconomicsProvider(RateDataProvider):
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
            raw_date = item.get("Date") or item.get("date")
            raw_value = item.get("Close") or item.get("close")
            if raw_date is None or raw_value is None:
                continue
            value = max(float(raw_value), settings.MIN_PRICE)
            try:
                d = date.fromisoformat(str(raw_date).split("T")[0])
            except Exception as exc:
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
    def __init__(self, csv_path: Optional[str] = None) -> None:
        self._csv_path = csv_path or settings.CTS_FREE_CSV_PATH

    async def get_historical_rates(
        self,
        lane: str,
        start_date: date,
        end_date: date,
    ) -> List[RatePoint]:
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


class ExcelDataProvider(RateDataProvider):
    MONTH_MAP = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "may": 5, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    def __init__(self, xlsx_path: Optional[str] = None) -> None:
        self._xlsx_path = xlsx_path or settings.EXCEL_DATA_PATH

    async def list_lanes(self) -> List[str]:
        from pathlib import Path

        try:
            import pandas as pd
        except ImportError as exc:
            raise ValueError("pandas is required for ExcelDataProvider. Run: pip install pandas openpyxl") from exc

        path = Path(self._xlsx_path)
        if not path.exists():
            raise ValueError(
                f"Excel data file not found at '{path}'. "
                f"Make sure data.xlsx is in the project root."
            )

        df = pd.read_excel(path, sheet_name=0, header=None)
        lanes: List[str] = []
        seen: set[str] = set()

        for i in range(len(df)):
            raw = df.iloc[i, 0]
            if pd.isna(raw):
                continue
            cell = str(raw).strip()
            cell_lower = cell.lower()

            if not cell:
                continue
            if cell_lower == "year":
                continue
            if cell_lower.startswith("change"):
                continue
            if cell.startswith("*"):
                continue

            try:
                float(cell)
                continue
            except Exception:
                pass

            if cell not in seen:
                seen.add(cell)
                lanes.append(cell)

        if not lanes:
            raise ValueError("No lane headers found in Excel file.")

        return lanes

    async def get_historical_rates(
        self,
        lane: str,
        start_date: date,
        end_date: date,
    ) -> List[RatePoint]:
        import calendar
        from pathlib import Path

        try:
            import pandas as pd
        except ImportError as exc:
            raise ValueError("pandas is required for ExcelDataProvider. Run: pip install pandas openpyxl") from exc

        path = Path(self._xlsx_path)
        if not path.exists():
            raise ValueError(
                f"Excel data file not found at '{path}'. "
                f"Make sure data.xlsx is in the project root (C:\\Users\\owenk\\algo1)."
            )

        df = pd.read_excel(path, sheet_name=0, header=None)
        lane_clean = lane.strip().lower()

        match_idx = None
        for i in range(len(df)):
            cell = str(df.iloc[i, 0]).strip().lower()
            if cell == lane_clean:
                match_idx = i
                break

        if match_idx is None:
            raise ValueError(
                f"Lane '{lane}' not found in Excel file. "
                f"Check that the lane name exactly matches a section header in data.xlsx."
            )

        header_row = df.iloc[match_idx + 1]
        headers = [str(h).strip().lower() for h in header_row]

        if headers[0] != "year":
            raise ValueError(
                f"Expected 'Year' header row after lane name at row {match_idx + 1}, "
                f"got '{headers[0]}'."
            )

        month_col: dict[str, int] = {}
        for col_i, h in enumerate(headers):
            if h in self.MONTH_MAP:
                month_col[h] = col_i

        points: List[RatePoint] = []
        for row_i in range(match_idx + 2, min(match_idx + 20, len(df))):
            row = df.iloc[row_i]
            year_raw = str(row.iloc[0]).strip()

            if year_raw.startswith("Change") or year_raw.startswith("*") or year_raw in ("nan", ""):
                break

            try:
                year = int(float(year_raw))
            except (ValueError, TypeError):
                break

            for month_name, col_i in month_col.items():
                raw = row.iloc[col_i]
                if pd.isna(raw):
                    continue
                try:
                    value = float(raw)
                except (ValueError, TypeError):
                    continue

                month_num = self.MONTH_MAP[month_name]
                last_day = calendar.monthrange(year, month_num)[1]
                d = date(year, month_num, last_day)

                if start_date <= d <= end_date:
                    points.append(RatePoint(date=d, value=max(value, settings.MIN_PRICE)))

        points.sort(key=lambda rp: rp.date)

        if len(points) < settings.MIN_DATA_POINTS:
            raise ValueError(
                f"Only {len(points)} data points found for lane '{lane}' "
                f"in the date range {start_date} to {end_date}. "
                f"Need at least {settings.MIN_DATA_POINTS}. "
                f"Try increasing lookback_days to 730 or more."
            )

        return points