"""Tiingo price provider (M6): token-authenticated daily adjusted closes.

The provider for validation and track-record scoring. Token resolution:
`TIINGO_API_KEY` env var first, then project `.env` (see core.config).
The key is only ever placed in the request URL — never logged or printed.
Raw responses are cached under data/raw/prices/ so runs are reproducible.
Tests use recorded fixtures / synthetic series — never the network.
"""

import json
import logging
from datetime import date
from pathlib import Path

from app.core.config import get_secret
from app.ingestion.http import fetch

logger = logging.getLogger(__name__)

PRICES_SUBDIR = "prices"
_DAILY_URL = (
    "https://api.tiingo.com/tiingo/daily/{symbol}/prices"
    "?startDate={start}&token={token}"
)
_START_DATE = "2015-01-01"  # full history window for validation lookbacks


class TiingoAuthError(RuntimeError):
    """Raised when no Tiingo API key is available."""


def tiingo_symbol(ticker: str) -> str:
    """Normalize a ticker for Tiingo (BRK.B -> BRK-B)."""
    return ticker.upper().replace(".", "-")


def _parse_daily(payload: bytes) -> dict[date, float]:
    """Parse a Tiingo /daily/prices response into date->adjClose."""
    try:
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise ValueError("expected a JSON array")
        series: dict[date, float] = {}
        for row in rows:
            series[date.fromisoformat(row["date"][:10])] = float(row["adjClose"])
        return series
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("unparseable Tiingo payload: %s", exc)
        return {}


def fetch_daily_closes_tiingo(
    ticker: str, raw_dir: Path, *, refresh: bool = False
) -> dict[date, float]:
    """Full daily adjusted-close history for a ticker, from cache or Tiingo."""
    token = get_secret("TIINGO_API_KEY")
    if not token:
        raise TiingoAuthError("TIINGO_API_KEY not set (env or .env)")
    symbol = tiingo_symbol(ticker)
    cache_path = raw_dir / PRICES_SUBDIR / f"tiingo_{symbol}.json"
    payload = fetch(
        _DAILY_URL.format(symbol=symbol, start=_START_DATE, token=token),
        cache_path,
        refresh=refresh,
    )
    series = _parse_daily(payload)
    if not series:
        logger.warning("no Tiingo price rows for %s (%s)", ticker, symbol)
    return series
