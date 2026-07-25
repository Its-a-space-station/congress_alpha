"""Environment-driven configuration for Congress Alpha.

Kept deliberately simple: a frozen dataclass resolved from environment
variables with sensible local-first defaults, plus a minimal stdlib `.env`
loader (KEY=VALUE lines, no dependencies). Real environment variables always
take precedence over `.env`. Secrets (e.g. TIINGO_API_KEY) live only in
`.env` (gitignored, mode 600) or the environment — never in code.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# Project root = parent of the `app` package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_ENV_DB_URL = "CONGRESS_ALPHA_DB_URL"
_ENV_LOG_LEVEL = "CONGRESS_ALPHA_LOG_LEVEL"
_ENV_TIINGO_KEY = "TIINGO_API_KEY"


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file (KEY=VALUE lines, # comments, blank lines)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_secret(name: str) -> str | None:
    """Resolve a secret: real environment first, then project .env."""
    value = os.environ.get(name)
    if value:
        return value
    return _load_dotenv(PROJECT_ROOT / ".env").get(name)


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
