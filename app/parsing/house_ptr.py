"""House PTR (Periodic Transaction Report) PDF parser. parser_version: house-ptr-0.1

Extracts the transaction table from the standard Clerk PTR form using
text-mode parsing (the form has no usable column ruling lines, so table
extraction merges cells — verified 2026-07-24).

Form layout (per transaction, possibly wrapping across two lines):
    [ID] [Owner] Asset text TYPE MM/DD/YYYY MM/DD/YYYY $min [- $max]
    ...continuation: rest of asset name, (TICKER), [TYPE-CODE], [$max]

Conventions (House PTR form instructions): Owner blank = the filer (SELF);
codes SP/JT/DC = spouse/joint/dependent child. TYPE is P/S/E. Tickers are
only read from explicit "(...)" delimiters; "[XX]" is the Clerk's asset-type
abbreviation. Anything else stays unmapped — never guessed.

parse_confidence is deterministic and decomposable (see _confidence).
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from app.core.enums import AssetType, OwnerType, TransactionType

logger = logging.getLogger(__name__)

PARSER_VERSION = "house-ptr-0.1"

_OWNER_CODES = {
    "SP": OwnerType.SPOUSE,
    "JT": OwnerType.JOINT,
    "DC": OwnerType.DEPENDENT_CHILD,
}
_TX_TYPE_CODES = {
    "P": TransactionType.PURCHASE,
    "S": TransactionType.SALE,
    "E": TransactionType.EXCHANGE,
}
_ASSET_TYPE_CODES = {
    "ST": AssetType.STOCK,
    "OP": AssetType.STOCK_OPTION,
}

_DATE = r"\d{2}/\d{2}/\d{4}"
_AMOUNT_FRAGMENT = r"(?:Over\s+)?\$[\d,]+(?:\s*-\s*\$?[\d,]*)?"
_ROW_START = re.compile(
    rf"^(?P<head>.+?)\s+(?P<type>[PSE])(?P<partial>\s*\(partial\))?\s+"
    rf"(?P<tx_date>{_DATE})\s+(?P<notif_date>{_DATE})\s+"
    rf"(?P<amount>{_AMOUNT_FRAGMENT})\s*(?P<cap>Yes|No)?\s*$"
)
# Loose detector for the independent row-count validation pass: any line that
# looks transaction-ish (two dates and a dollar amount) but may fail strict parsing.
_CANDIDATE_ROW = re.compile(rf"{_DATE}\s+{_DATE}.*\$")
_TRAILING_DOLLARS = re.compile(r"\$[\d,]+\s*$")
_TICKER = re.compile(r"\(([A-Z][A-Z0-9.]{0,7})\)")
_ASSET_TYPE_BRACKET = re.compile(r"\[([A-Z]{1,3})\]")
# Lines that terminate a continuation (page furniture / form boilerplate).
_STOP_LINE = re.compile(
    r"^(F\x00|Digitally Signed|I CERTIFY|Clerk of the House|Filing ID|"
    r"\* For the complete list)|P\s+T\s+R\s*$"
)


@dataclass(frozen=True)
class ParsedTransaction:
    """One transaction row extracted from a PTR."""

    owner: OwnerType
    asset_name: str
    ticker: str | None
    asset_type_code: str | None  # verbatim Clerk bracket code, e.g. "ST"
    transaction_type: TransactionType
    transaction_date: date | None
    notification_date: date | None
    amount_min: Decimal | None
    amount_max: Decimal | None
    raw_text: str
    parse_confidence: float
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PtrParseResult:
    """Result of parsing one PTR PDF."""

    filer_name: str | None
    state_district: str | None
    transactions: tuple[ParsedTransaction, ...]
    candidate_row_lines: int  # loose-regex count, for the validation pass
    warnings: tuple[str, ...]


def _parse_form_date(value: str) -> date | None:
    try:
        month, day, year = (int(p) for p in value.split("/"))
        return date(year, month, day)
    except ValueError:
        return None


def _parse_dollars(value: str) -> Decimal | None:
    digits = value.replace("$", "").replace(",", "").strip()
    if not digits:
        return None
    try:
        return Decimal(digits)
    except ArithmeticError:
        return None


def _split_amount(fragment: str, continuation: str) -> tuple[Decimal | None, Decimal | None, bool]:
    """Parse the amount range; the max may trail at the end of the continuation.

    Returns (min, max, assembled_from_wrap). "Over $X" yields (X, None).
    """
    text = fragment
    wrapped = False
    if text.rstrip().endswith("-") or text.rstrip().endswith("- $"):
        trailing = _TRAILING_DOLLARS.search(continuation)
        if trailing:
            text = f"{text} {trailing.group(0)}"
            wrapped = True
    if text.lower().startswith("over"):
        return _parse_dollars(text), None, wrapped
    parts = [p for p in text.split("-") if p.strip()]
    amount_min = _parse_dollars(parts[0]) if parts else None
    amount_max = _parse_dollars(parts[1]) if len(parts) > 1 else None
    return amount_min, amount_max, wrapped


def _confidence(
    *,
    ticker: str | None,
    asset_type_code: str | None,
    amount_max: Decimal | None,
    continuation_lines: int,
    field_warnings: int,
) -> float:
    """Deterministic, decomposable confidence score.

    Start at 1.0 and deduct: -0.1 no ticker but a type bracket (e.g. [GS]
    treasuries carry no ticker); -0.3 neither ticker nor type bracket (asset
    not clearly identifiable); -0.2 open/unfinished amount range; -0.1 per
    continuation line beyond the first; -0.1 per field-level warning.
    Floor 0.1.
    """
    score = 1.0
    if ticker is None:
        score -= 0.1 if asset_type_code else 0.3
    if amount_max is None:
        score -= 0.2
    score -= 0.1 * max(0, continuation_lines - 1)
    score -= 0.1 * field_warnings
    return max(0.1, round(score, 2))


def _clean_asset_text(text: str) -> str:
    """Remove ticker/type-bracket annotations and normalize whitespace."""
    text = _TICKER.sub("", text)
    text = _ASSET_TYPE_BRACKET.sub("", text)
    text = _TRAILING_DOLLARS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_header(lines: list[str]) -> tuple[str | None, str | None]:
    filer_name: str | None = None
    state_district: str | None = None
    for line in lines[:15]:
        if line.startswith("Name:"):
            filer_name = line.removeprefix("Name:").strip()
        elif line.startswith("State/District:"):
            state_district = line.removeprefix("State/District:").strip()
    return filer_name, state_district


def parse_ptr_text(text: str) -> PtrParseResult:
    """Parse the full extracted text of a PTR into typed transactions."""
    lines = [line for line in text.split("\n") if line.strip()]
    filer_name, state_district = _extract_header(lines)
    candidate_row_lines = sum(1 for line in lines if _CANDIDATE_ROW.search(line))

    transactions: list[ParsedTransaction] = []
    filing_warnings: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _ROW_START.match(line)
        if match is None:
            i += 1
            continue

        # Gather continuation lines belonging to this row.
        continuation: list[str] = []
        j = i + 1
        while j < len(lines) and not _ROW_START.match(lines[j]) and not _STOP_LINE.search(lines[j]):
            continuation.append(lines[j])
            j += 1
        continuation_text = " ".join(continuation)

        row_warnings: list[str] = []
        if match.group("partial"):
            row_warnings.append("partial sale (position only partially closed)")
        head = match.group("head").strip()
        owner = OwnerType.SELF  # form convention: blank owner column = filer
        first_token, _, rest = head.partition(" ")
        if first_token in _OWNER_CODES:
            owner = _OWNER_CODES[first_token]
            head = rest.strip()

        ticker_match = _TICKER.search(continuation_text) or _TICKER.search(head)
        type_match = _ASSET_TYPE_BRACKET.search(continuation_text) or _ASSET_TYPE_BRACKET.search(
            head
        )
        ticker = ticker_match.group(1) if ticker_match else None
        asset_type_code = type_match.group(1) if type_match else None

        amount_min, amount_max, _wrapped = _split_amount(
            match.group("amount"), continuation_text
        )
        if amount_min is None:
            row_warnings.append("amount min unparseable")
        if amount_max is None:
            row_warnings.append("open or unfinished amount range")

        tx_date = _parse_form_date(match.group("tx_date"))
        notif_date = _parse_form_date(match.group("notif_date"))
        if tx_date is None or notif_date is None:
            row_warnings.append("date unparseable")

        asset_name = _clean_asset_text(f"{head} {continuation_text}")
        if not asset_name:
            row_warnings.append("empty asset name")

        raw_text = " ".join([line, *continuation])
        transactions.append(
            ParsedTransaction(
                owner=owner,
                asset_name=asset_name,
                ticker=ticker,
                asset_type_code=asset_type_code,
                transaction_type=_TX_TYPE_CODES[match.group("type")],
                transaction_date=tx_date,
                notification_date=notif_date,
                amount_min=amount_min,
                amount_max=amount_max,
                raw_text=raw_text,
                parse_confidence=_confidence(
                    ticker=ticker,
                    asset_type_code=asset_type_code,
                    amount_max=amount_max,
                    continuation_lines=len(continuation),
                    field_warnings=len(row_warnings),
                ),
                warnings=tuple(row_warnings),
            )
        )
        i = j

    if candidate_row_lines != len(transactions):
        filing_warnings.append(
            f"row-count mismatch: {candidate_row_lines} candidate lines vs "
            f"{len(transactions)} strictly parsed"
        )
    return PtrParseResult(
        filer_name=filer_name,
        state_district=state_district,
        transactions=tuple(transactions),
        candidate_row_lines=candidate_row_lines,
        warnings=tuple(filing_warnings),
    )


def parse_ptr_pdf(path: Path) -> PtrParseResult:
    """Parse one PTR PDF. Unreadable/non-PTR files return an empty result with
    a warning — the parser never crashes the batch on one bad document."""
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:  # pdfplumber raises assorted exceptions on bad PDFs
        logger.warning("cannot open %s: %s", path, exc)
        return PtrParseResult(
            filer_name=None,
            state_district=None,
            transactions=(),
            candidate_row_lines=0,
            warnings=(f"unreadable PDF: {exc}",),
        )
    if "Periodic Transaction Report" not in text and "P   T   R" not in text:
        return PtrParseResult(
            filer_name=None,
            state_district=None,
            transactions=(),
            candidate_row_lines=0,
            warnings=("not a PTR document",),
        )
    return parse_ptr_text(text)


def cross_check(
    result: PtrParseResult,
    *,
    filing_date: date | None = None,
    expected_filer: str | None = None,
) -> tuple[str, ...]:
    """Deterministic post-parse validation. Returns warnings (never raises)."""
    warnings: list[str] = list(result.warnings)
    if expected_filer and result.filer_name:
        # Normalized surname check: the index filer name and PDF header should agree.
        index_last = expected_filer.strip().split()[-1].lower()
        if index_last not in result.filer_name.lower():
            warnings.append(
                f"filer mismatch: index {expected_filer!r} vs PDF {result.filer_name!r}"
            )
    for tx in result.transactions:
        if filing_date and tx.transaction_date and tx.transaction_date > filing_date:
            warnings.append(
                f"transaction date {tx.transaction_date} after filing date {filing_date}"
            )
        if (
            tx.notification_date
            and tx.transaction_date
            and tx.notification_date < tx.transaction_date
        ):
            warnings.append(
                f"notification date {tx.notification_date} before transaction date "
                f"{tx.transaction_date} (source anomaly or misread)"
            )
        if (
            tx.amount_min is not None
            and tx.amount_max is not None
            and tx.amount_min > tx.amount_max
        ):
            warnings.append(f"amount min {tx.amount_min} > max {tx.amount_max}")
    return tuple(warnings)
