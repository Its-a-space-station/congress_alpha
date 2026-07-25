"""Sanity tests for the shared enums."""

from app.core.enums import (
    AssetType,
    CertaintyLabel,
    Chamber,
    FilingType,
    OwnerType,
    ScoreLabel,
    TransactionType,
)


def test_enums_are_string_valued() -> None:
    # Stored in SQLite as plain strings; values must stay stable.
    assert Chamber.HOUSE == "house"
    assert FilingType.PERIODIC_TRANSACTION == "periodic_transaction"
    assert OwnerType.SPOUSE == "spouse"
    assert AssetType.STOCK_OPTION == "stock_option"
    assert TransactionType.EXCHANGE == "exchange"
    assert CertaintyLabel.MEDIUM == "medium"
    assert ScoreLabel.ELEVATED == "elevated"


def test_certainty_and_score_labels_cover_expected_bands() -> None:
    assert set(CertaintyLabel) == {
        CertaintyLabel.LOW,
        CertaintyLabel.MEDIUM,
        CertaintyLabel.HIGH,
    }
    assert set(ScoreLabel) == {
        ScoreLabel.LOW,
        ScoreLabel.MODERATE,
        ScoreLabel.ELEVATED,
        ScoreLabel.HIGH,
    }
