"""End-of-day price provider (M5b): Yahoo Finance v8 chart API, cached raw.

Read-only public data (the same JSON endpoint Yahoo's own site calls), no API
key. NOTE: the original choice, stooq.com, serves a JavaScript anti-bot
challenge to plain GETs (verified 2026-07-25); we do not circumvent bot
challenges, so Yahoo's public chart endpoint is used instead.

Responses are cached verbatim under data/raw/prices/ so validation runs are
reproducible and re-runnable without re-hitting the network. Tests inject
synthetic series and never touch the network. Adjusted closes are used when
present (split/dividend-adjusted returns), falling back to raw closes.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.ingestion.http import fetch

logger = logging.getLogger(__name__)

PRICES_SUBDIR = "prices"
_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10y&interval=1d"


def yahoo_symbol(ticker: str) -> str:
    """Normalize a ticker for Yahoo (BRK.B -> BRK-B)."""
    return ticker.upper().replace(".", "-")


def _parse_chart(payload: bytes) -> dict[date, float]:
    """Parse a Yahoo v8 chart response into a date->adjusted-close mapping."""
    try:
        result = json.loads(payload)["chart"]["result"][0]
        timestamps = result["timestamp"]
        gmt_offset = result["meta"].get("gmtoffset", 0)
        quote = result["indicators"]["quote"][0]
        adjclose_block = result["indicators"].get("adjclose", [quote])[0]
        closes = adjclose_block.get("adjclose", quote["close"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("unparseable chart payload: %s", exc)
        return {}

    tz = timezone(timedelta(seconds=gmt_offset))
    series: dict[date, float] = {}
    for ts, close in zip(timestamps, closes, strict=True):
        if close is None:
            continue
        day = datetime.fromtimestamp(ts, tz=tz).date()
        series[day] = float(close)
    return series


def fetch_daily_closes(
    ticker: str, raw_dir: Path, *, refresh: bool = False
) -> dict[date, float]:
    """Full daily-close history for a ticker, from cache or Yahoo."""
    symbol = yahoo_symbol(ticker)
    cache_path = raw_dir / PRICES_SUBDIR / f"{symbol}.json"
    payload = fetch(_CHART_URL.format(symbol=symbol), cache_path, refresh=refresh)
    series = _parse_chart(payload)
    if not series:
        logger.warning("no price rows for %s (%s)", ticker, symbol)
    return series


def close_on_or_after(series: dict[date, float], day: date) -> tuple[date, float] | None:
    """First close on or after `day` — the public entry point for a signal.

    Never returns a close dated BEFORE `day` (point-in-time guard).
    """
    candidates = sorted(d for d in series if d >= day)
    if not candidates:
        return None
    first = candidates[0]
    return first, series[first]


def close_on_or_before(series: dict[date, float], day: date) -> tuple[date, float] | None:
    """Last close on or before `day` (horizon-end pricing for non-trading days)."""
    candidates = sorted(d for d in series if d <= day)
    if not candidates:
        return None
    last = candidates[-1]
    return last, series[last]
