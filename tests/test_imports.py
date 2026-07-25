"""Smoke tests: every app module imports cleanly."""


def test_core_modules_import() -> None:
    import app.cli  # noqa: F401
    import app.core.config  # noqa: F401
    import app.core.enums  # noqa: F401
    import app.core.logging  # noqa: F401


def test_db_modules_import() -> None:
    import app.db.models  # noqa: F401
    import app.db.session  # noqa: F401


def test_layer_packages_import() -> None:
    import app.dashboard  # noqa: F401
    import app.ingestion  # noqa: F401
    import app.intelligence  # noqa: F401
    import app.jobs  # noqa: F401
    import app.parsing  # noqa: F401
