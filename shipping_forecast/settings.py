"""Global settings for the shipping_forecast package.

This module defines configuration for multiple free data sources so you can
prototype without paid vendor contracts. You can swap to commercial feeds
later by adding new providers.
"""

from datetime import timedelta

# Core Monte Carlo defaults
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_NUM_PATHS = 5000
DEFAULT_HORIZON_WEEKS = 8

MIN_DATA_POINTS = 30
MIN_PRICE = 1e-6
DAY_FRACTION = 1.0

# ---------------------------------------------------------------------------
# Trading Economics configuration
# ---------------------------------------------------------------------------
# Trading Economics offers a free developer account and also supports a
# very limited "guest:guest" key for experimentation.[web:48][web:59][web:61][web:66][web:67]
# Register at https://developer.tradingeconomics.com/ for your own free key.

TRADING_ECONOMICS_API_BASE_URL = "https://api.tradingeconomics.com"
# Use environment variable TRADING_ECONOMICS_API_KEY in production.
TRADING_ECONOMICS_API_KEY = "guest:guest"

# Symbols for two freight-related indices exposed on Trading Economics:
# - Containerized Freight Index
# - World Container Index[web:13][web:52][web:68]
TRADING_ECONOMICS_CONTAINERIZED_SYMBOL = "CONTFREIGHT:COM"  # example symbol
TRADING_ECONOMICS_WCI_SYMBOL = "WORLDCONTAINER:COM"        # example symbol

# ---------------------------------------------------------------------------
# Container Trade Statistics (CTS) free data configuration
# ---------------------------------------------------------------------------
# CTS offers free monthly global container price indices and volumes after
# free registration.[web:37]
# We expect a local CSV export with columns: date,value

CTS_FREE_CSV_PATH = "data/cts_global_price_index.csv"
CTS_DATE_COLUMN = "date"
CTS_VALUE_COLUMN = "value"

# Ensure the data path is relative to project root by convention. Create the
# file manually from the latest CTS free download.
