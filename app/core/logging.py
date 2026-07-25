"""Logging setup for Congress Alpha.

One `setup_logging()` entrypoint used by the CLI and jobs; library modules
just grab `logging.getLogger(__name__)` and never configure handlers.
"""

import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once. Safe to call repeatedly."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    root.setLevel(numeric)
