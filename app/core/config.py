"""Environment-driven configuration for Congress Alpha.

Kept deliberately simple for Phase 0: a frozen dataclass resolved from
environment variables with sensible local-first defaults. All paths stay
inside the project root unless explicitly overridden.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# Project root = parent of the `app` package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_ENV_DB_URL = "CONGRESS_ALPHA_DB_URL"
_ENV_LOG_LEVEL = "CONGRESS_ALPHA_LOG_LEVEL"


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved once at startup."""

    data_dir: Path
    raw_dir: Path
    processed_dir: Path
    exports_dir: Path
    db_url: str
    log_level: str


def get_settings() -> Settings:
    """Resolve settings from environment variables with local defaults."""
    data_dir = PROJECT_ROOT / "data"
    default_db = data_dir / "congress_alpha.db"
    return Settings(
        data_dir=data_dir,
        raw_dir=data_dir / "raw",
        processed_dir=data_dir / "processed",
        exports_dir=data_dir / "exports",
        db_url=os.environ.get(_ENV_DB_URL, f"sqlite:///{default_db}"),
        log_level=os.environ.get(_ENV_LOG_LEVEL, "INFO"),
    )
