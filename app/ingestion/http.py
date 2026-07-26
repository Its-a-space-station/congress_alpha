"""Polite HTTP fetching with on-disk raw caching.

Every fetched payload is written verbatim to disk before any parsing touches
it — raw sources stay immutable and re-runnable per CLAUDE.md traceability
rules. Cache hits make ingestion stages independently re-runnable without
re-hitting the network. Credential query params (e.g. `token=...`) are
masked in all logs and error messages so secrets never leak to output.
"""

import hashlib
import logging
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "congress-alpha/0.1 (local-first disclosure research)"
DEFAULT_TIMEOUT = 30.0
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0

_CREDENTIAL_PARAM = re.compile(
    r"((?:token|api_key|apikey|access_token)=)[^&\s'\"]+", re.IGNORECASE
)


def redact_credentials(text: str) -> str:
    """Mask credential query-param values (e.g. `token=abc`) for safe logging."""
    return _CREDENTIAL_PARAM.sub(r"\1***", text)


def sha256_bytes(payload: bytes) -> str:
    """Hex sha256 of a payload, for provenance manifests."""
    return hashlib.sha256(payload).hexdigest()


def fetch(url: str, cache_path: Path, *, refresh: bool = False) -> bytes:
    """Fetch `url` with retry/backoff, caching raw bytes at `cache_path`.

    Returns cached bytes when present unless `refresh` is set. The cache file
    doubles as the immutable raw snapshot for later parsing stages. The URL is
    credential-redacted in every log line and in the raised error.
    """
    if cache_path.exists() and not refresh:
        logger.info("cache hit: %s", cache_path)
        return cache_path.read_bytes()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    safe_url = redact_credentials(url)
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.content
            cache_path.write_bytes(payload)
            logger.info("fetched %s -> %s (%d bytes)", safe_url, cache_path, len(payload))
            return payload
        except httpx.HTTPError as exc:
            last_error = exc
            wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "fetch attempt %d/%d failed for %s: %s; retrying in %.1fs",
                attempt,
                MAX_ATTEMPTS,
                safe_url,
                redact_credentials(str(exc)),
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"failed to fetch {safe_url} after {MAX_ATTEMPTS} attempts") from last_error
