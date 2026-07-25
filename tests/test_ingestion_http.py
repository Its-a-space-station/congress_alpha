"""Tests for the cached fetcher and dataset snapshot stage."""

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
