"""Official dataset sources for M1 ingestion.

The unitedstates/congress-legislators project publishes YAML snapshots of
current legislators, committees, and committee membership. URLs verified
2026-07-24; verification notes live in tasks/todo.md (M1).
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.http import fetch, sha256_bytes

logger = logging.getLogger(__name__)

BASE_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
SNAPSHOT_SUBDIR = "congress_legislators"


@dataclass(frozen=True)
class DatasetSource:
    """One upstream dataset file."""

    filename: str
    url: str


LEGISLATORS = DatasetSource("legislators-current.yaml", f"{BASE_URL}/legislators-current.yaml")
COMMITTEES = DatasetSource("committees-current.yaml", f"{BASE_URL}/committees-current.yaml")
MEMBERSHIP = DatasetSource(
    "committee-membership-current.yaml", f"{BASE_URL}/committee-membership-current.yaml"
)
ALL_SOURCES = (LEGISLATORS, COMMITTEES, MEMBERSHIP)


def snapshot_datasets(raw_dir: Path, *, refresh: bool = False) -> dict[str, Path]:
    """Download all dataset files into `raw_dir`/SNAPSHOT_SUBDIR with a manifest.

    The manifest records source URL, sha256, byte count, and fetch time for
    every file so later parsing stages stay traceable. Returns paths keyed by
    filename.
    """
    snapshot_dir = raw_dir / SNAPSHOT_SUBDIR
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC).isoformat()

    paths: dict[str, Path] = {}
    manifest: dict[str, dict[str, object]] = {}
    for source in ALL_SOURCES:
        path = snapshot_dir / source.filename
        payload = fetch(source.url, path, refresh=refresh)
        manifest[source.filename] = {
            "url": source.url,
            "sha256": sha256_bytes(payload),
            "bytes": len(payload),
            "fetched_at": fetched_at,
        }
        paths[source.filename] = path

    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    logger.info("snapshot manifest written to %s", manifest_path)
    return paths
