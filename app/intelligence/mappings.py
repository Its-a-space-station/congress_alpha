"""Loader for the curated mapping files in mappings/ (M4).

Both files carry a `mapping_version`; every change must bump it so scoring
runs stay interpretable. Unmapped committees/assets contribute nothing.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

MAPPINGS_DIR = Path(__file__).resolve().parent.parent.parent / "mappings"


@dataclass(frozen=True)
class Mappings:
    """Loaded committee/ticker sector maps with their versions."""

    committee_sectors: dict[str, list[str]]
    ticker_sectors: dict[str, str]
    committee_version: str
    ticker_version: str

    @property
    def version(self) -> str:
        """Combined version string recorded on score runs."""
        return f"committee@{self.committee_version}+ticker@{self.ticker_version}"


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping document")
    return data


def load_mappings(mappings_dir: Path = MAPPINGS_DIR) -> Mappings:
    """Load and validate both mapping files."""
    committee_doc = _load(mappings_dir / "committee_sectors.yaml")
    ticker_doc = _load(mappings_dir / "ticker_sectors.yaml")

    committee_sectors = {
        str(code): [str(s) for s in sectors]
        for code, sectors in (committee_doc.get("committees") or {}).items()
    }
    ticker_sectors = {
        str(ticker).upper(): str(sector)
        for ticker, sector in (ticker_doc.get("tickers") or {}).items()
    }
    used = {s for sectors in committee_sectors.values() for s in sectors}
    unknown = {s for s in ticker_sectors.values()} - used
    if unknown:
        logger.warning("ticker map uses sectors not present in committee map: %s", sorted(unknown))

    return Mappings(
        committee_sectors=committee_sectors,
        ticker_sectors=ticker_sectors,
        committee_version=str(committee_doc.get("mapping_version", "unknown")),
        ticker_version=str(ticker_doc.get("mapping_version", "unknown")),
    )
