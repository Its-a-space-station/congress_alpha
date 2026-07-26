"""Tests for app.core.logging setup."""

import logging

from app.core.logging import setup_logging


def test_setup_logging_silences_http_client_loggers() -> None:
    """httpx logs every request URL at INFO; those URLs can carry credentials
    (e.g. the Tiingo `token=` param), so HTTP-client loggers stay at WARNING+.
    """
    setup_logging()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
