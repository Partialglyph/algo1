import os
from pathlib import Path
from datetime import timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; rely on shell env

# Root of the repository (two levels up from this file: shipping_forecast/settings.py)
_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT   = _PACKAGE_DIR.parent

DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_NUM_PATHS = 5000
DEFAULT_HORIZON_WEEKS = 8

MIN_DATA_POINTS = 10
MIN_PRICE = 1e-6
DAY_FRACTION = 1.0

TRADING_ECONOMICS_API_BASE_URL = "https://api.tradingeconomics.com"
TRADING_ECONOMICS_API_KEY = "guest:guest"
TRADING_ECONOMICS_CONTAINERIZED_SYMBOL = "CONTFREIGHT:COM"
TRADING_ECONOMICS_WCI_SYMBOL = "WORLDCONTAINER:COM"

CTS_FREE_CSV_PATH = str(_REPO_ROOT / "data" / "cts_global_price_index.csv")
CTS_DATE_COLUMN = "date"
CTS_VALUE_COLUMN = "value"

# Always resolved to the actual file, regardless of the working directory
# uvicorn is launched from.
EXCEL_DATA_PATH = str(
    Path(os.getenv("EXCEL_DATA_PATH", "")).resolve()
    if os.getenv("EXCEL_DATA_PATH", "")
    else _REPO_ROOT / "data.xlsx"
)

# Translation — loaded from .env if present
DEEPL_API_KEY: str | None = os.getenv("DEEPL_API_KEY", None)

# Oil price source
EIA_OIL_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/?api_key=DEMO&data[]=value&facets[series][]=RBRTE&sort[0][column]=period&sort[0][direction]=desc&length=7"

# Vessel congestion stub endpoint (replace with real source when available)
CONGESTION_STUB = True

# Duty rates stub (replace with WTO/TRAINS feed when available)
DUTY_STUB = True
