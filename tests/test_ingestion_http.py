"""Tests for the cached fetcher and dataset snapshot stage."""

import logging
from pathlib import Path

import httpx
import pytest

from app.ingestion.http import fetch, sha256_bytes
from app.ingestion.sources import ALL_SOURCES, snapshot_datasets


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


def test_fetch_returns_cached_bytes_without_network(tmp_path: Path) -> None:
    cache_path = tmp_path / "cached.yaml"
    cache_path.write_bytes(b"cached")
    # An unroutable URL proves the network is never touched on a cache hit.
    assert fetch("https://invalid.example/nope", cache_path) == b"cached"


def test_fetch_writes_cache_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *args, **kwargs: _FakeResponse(b"payload")  # noqa: ARG005
    )

    cache_path = tmp_path / "sub" / "file.yaml"
    assert fetch("https://example.test/file", cache_path, refresh=True) == b"payload"
    assert cache_path.read_bytes() == b"payload"

    paths = snapshot_datasets(tmp_path / "raw", refresh=True)
    assert set(paths) == {source.filename for source in ALL_SOURCES}
    manifest = (paths[ALL_SOURCES[0].filename].parent / "manifest.json").read_text()
    assert sha256_bytes(b"payload") in manifest
    assert "url" in manifest


def _raise_404(url: str, *args: object, **kwargs: object) -> None:
    request = httpx.Request("GET", url)
    response = httpx.Response(404, request=request)
    raise httpx.HTTPStatusError(
        f"Client error '404 Not Found' for url '{url}'",
        request=request,
        response=response,
    )


def test_fetch_redacts_token_in_error_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    url = "https://api.tiingo.com/tiingo/daily/XYZ/prices?startDate=2015-01-01&token=secret123"
    monkeypatch.setattr(httpx, "get", _raise_404)
    monkeypatch.setattr("app.ingestion.http.time.sleep", lambda *_args: None)
    with caplog.at_level(logging.WARNING, logger="app.ingestion.http"):
        with pytest.raises(RuntimeError) as excinfo:
            fetch(url, tmp_path / "dead.json")
    assert "secret123" not in str(excinfo.value)
    assert "secret123" not in caplog.text
    assert "token=***" in str(excinfo.value)


def test_fetch_redacts_token_in_success_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    url = "https://api.tiingo.com/tiingo/daily/SPY/prices?token=secret123"
    monkeypatch.setattr(
        httpx, "get", lambda *args, **kwargs: _FakeResponse(b"[]")  # noqa: ARG005
    )
    with caplog.at_level(logging.INFO, logger="app.ingestion.http"):
        fetch(url, tmp_path / "spy.json")
    assert "secret123" not in caplog.text
