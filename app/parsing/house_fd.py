"""House annual FD parser — assets (Schedule A) + liabilities (Schedule C).

parser_version: house-fd-0.1

Coordinate-based extraction: the FD form renders fixed column positions per
section, so words are assigned to column bins anchored on each section's own
header positions. (Text-mode regex is brittle here: asset names, values, and
income cells wrap across lines in three different orders — verified
2026-07-24.) Row groups are assembled with the asset-type bracket "[XX]" and
account wrapper "⇒" anchors. Everything is preserved verbatim in raw_text;
income details are captured verbatim (structuring them is not needed for M3).

Scope: assets and liabilities only. Transactions (Schedule B) duplicate M2b
PTR data and are skipped; income/positions/agreements/gifts/travel sections
are skipped.
"""

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pdfplumber

from app.core.enums import OwnerType

logger = logging.getLogger(__name__)

PARSER_VERSION = "house-fd-0.1"

_OWNER_CODES = {
    "SP": OwnerType.SPOUSE,
    "JT": OwnerType.JOINT,
    "DC": OwnerType.DEPENDENT_CHILD,
}
_TICKER = re.compile(r"\(([A-Z][A-Z0-9.]{0,7})\)")
_ASSET_TYPE_BRACKET = re.compile(r"\[([A-Z0-9]{1,3})\]")
_DOLLARS = re.compile(r"\$[\d,]+")
_ANNOTATION = re.compile(r"^([LD])\s*:\s*(.*)$")

# Column header signatures -> section name. Lines are scanned across the
# whole document (sections can start mid-page on candidate filings).
_SECTION_HEADERS = [
    (("asset", "owner", "value of asset", "income"), "assets"),
    (("asset", "owner", "date", "tx."), "transactions"),  # skipped (M2b data)
    (("owner", "creditor", "date incurred", "type"), "liabilities"),
    (("source", "type", "amount"), "other"),  # earned income — out of scope
    (("position", "name of organization"), "other"),
    (("date", "parties to"), "other"),  # agreements — out of scope
    (("trip details",), "other"),  # travel — out of scope
]
# x anchors per section: ordered keywords whose x0 starts each bin.
_ASSETS_ANCHORS = ["owner", "value", "type(s)", "income"]
_LIABILITIES_ANCHORS = ["owner", "creditor", "date", "type", "amount"]
# Header-vocabulary filter: lines made only of these words are multi-row
# column headers/subheaders, not data (old form variants wrap headers).
_HEADER_VOCAB = {
    "current", "preceding", "year", "to", "filing", "type(s)", "income", "tx.",
    "cap.", "gains", ">", "$1,000?", "$200?", "asset", "owner", "value", "of",
    "date", "type", "amount", "creditor", "incurred", "liability", "source",
}


def _is_header_line(text: str) -> bool:
    words = set(text.lower().replace("?", "").split())
    return bool(words) and words <= _HEADER_VOCAB


# Multi-row header remnants to skip inside section bodies (old form variant).
_HEADER_CONTINUATION_LINES = {
    "current year to",
    "preceding",
    "filing year",
    "current year",
    "to preceding",
    "preceding year",
    "current year to preceding",
    "current year to filing year",
}
# Spaced-banner lines that end a section (appendices and skipped schedules).
_OTHER_BANNERS = {
    "s a b i v d",  # Schedule A/B investment-vehicle details appendix
}
# Cleaned spaced banners of the form "S C: E I" / "S B: T" (section starts we
# do not parse).
_SECTION_BANNER = re.compile(r"^s(\s+[a-z])+\s*:")
# Form footer lines ("* For the complete list of asset type abbreviations...").
_FOOTER_LINE = re.compile(
    r"^\*\s*(for the complete list|investment vehicle details)|asset-type-codes\.aspx"
)


@dataclass(frozen=True)
class ParsedHolding:
    """One asset holding from Schedule A."""

    account: str | None
    owner: OwnerType
    asset_name: str
    ticker: str | None
    asset_type_code: str | None
    value_min: Decimal | None
    value_max: Decimal | None
    income_details: str | None
    description: str | None
    location: str | None
    raw_text: str
    parse_confidence: float
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ParsedLiability:
    """One liability from Schedule C."""

    owner: OwnerType
    creditor_name: str
    liability_type: str | None
    date_incurred: str | None
    value_min: Decimal | None
    value_max: Decimal | None
    raw_text: str
    parse_confidence: float
    warnings: tuple[str, ...]


@dataclass
class FdParseResult:
    """Result of parsing one annual FD PDF."""

    filer_name: str | None
    state_district: str | None
    holdings: list[ParsedHolding] = field(default_factory=list)
    liabilities: list[ParsedLiability] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Line:
    """Words sharing a y position, rendered as text with an x0 per word."""

    top: float
    words: tuple[tuple[float, str], ...]  # (x0, text) sorted by x0

    @property
    def text(self) -> str:
        return " ".join(word for _, word in self.words)


def _group_lines(words: list[dict], tolerance: float = 2.5) -> list[_Line]:
    """Cluster pdfplumber words into visual lines by their `top` coordinate."""
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for word in words:
        if lines and abs(word["top"] - lines[-1][-1]["top"]) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [
        _Line(top=line[0]["top"], words=tuple((w["x0"], w["text"]) for w in line))
        for line in lines
    ]


def _clean(text: str) -> str:
    """Normalize the form's spaced/nul-padded furniture text."""
    return re.sub(r"\s+", " ", text.replace("\x00", " ")).strip()


def _detect_section(line_text: str) -> str | None:
    lowered = _clean(line_text).lower()
    for keywords, section in _SECTION_HEADERS:
        if all(keyword in lowered for keyword in keywords):
            return section
    return None


def _anchors_for(line: _Line, keywords: list[str]) -> dict[str, float] | None:
    """x0 position of each anchor keyword within a header line."""
    anchors: dict[str, float] = {}
    for keyword in keywords:
        for x0, word in line.words:
            if _clean(word).lower().startswith(keyword):
                anchors[keyword] = x0
                break
        else:
            return None
    return anchors


def _bin_assign(line: _Line, bin_starts: list[tuple[str, float]]) -> dict[str, list[str]]:
    """Assign each word of a line to a column bin by x0."""
    bins: dict[str, list[str]] = {name: [] for name, _ in bin_starts}
    for x0, word in line.words:
        assigned = bin_starts[0][0]
        for name, start in bin_starts:
            if x0 >= start - 1.0:
                assigned = name
        bins[assigned].append(word)
    return bins


def _parse_range(text: str) -> tuple[Decimal | None, Decimal | None]:
    amounts = [
        Decimal(m.group(0).replace("$", "").replace(",", ""))
        for m in _DOLLARS.finditer(text)
    ]
    if not amounts:
        return None, None
    if text.lower().startswith("over"):
        return amounts[0], None
    return amounts[0], amounts[1] if len(amounts) > 1 else None


def _holding_confidence(
    *, has_bracket: bool, value_min: Decimal | None, value_disclosed_none: bool,
    name: str, warnings: int,
) -> float:
    """Deterministic decomposable confidence (see PTR parser for the pattern)."""
    score = 1.0
    if not has_bracket:
        score -= 0.3
    if value_min is None and not value_disclosed_none:
        score -= 0.3
    if not name:
        score -= 0.3
    score -= 0.1 * warnings
    return max(0.1, round(score, 2))


def _value_complete(value_text: str) -> bool:
    """A row's value cell is complete: a $ range or an explicit "None"."""
    return _DOLLARS.search(value_text) is not None or value_text.strip().lower() == "none"


def _parse_assets(
    rows: list[tuple[_Line, _Line]], holdings: list[ParsedHolding]
) -> None:
    """Parse Schedule A rows from a (header, body_line) stream.

    Each body line is binned to true columns via the x anchors of the section
    header that governs it, so wrapped cells never bleed across columns.
    Row groups are anchored on the asset-type bracket / "⇒" wrapper in the
    asset column; L:/D: annotation lines attach to the current row.
    """
    current: dict[str, list[str]] = {"asset": [], "owner": [], "value": [], "income": []}
    annotations: list[str] = []

    def flush() -> None:
        if any(current[col] for col in ("asset", "value")):
            holdings.append(_build_holding(current, annotations))
        for key in current:
            current[key] = []
        annotations.clear()

    for header, line in rows:
        anchors = _anchors_for(header, ["owner", "value", "type(s)", "income"])
        if anchors is None:  # variant without the "Type(s)" wording
            anchors = _anchors_for(header, ["owner", "value", "income"])
        if anchors is None:
            continue
        bin_starts = [("asset", 0.0), *sorted(anchors.items(), key=lambda kv: kv[1])]

        text = _clean(line.text)
        if not text:
            continue
        if text.lower() in _HEADER_CONTINUATION_LINES or _is_header_line(text):
            continue  # multi-row header remnant ("Current Year to", etc.)
        annotation = _ANNOTATION.match(text)
        if annotation:
            annotations.append(f"{annotation.group(1)}: {annotation.group(2)}")
            continue
        bins = _bin_assign(line, bin_starts)
        asset_text = _clean(" ".join(bins.get("asset", [])))
        if not asset_text:
            # continuation line: only column content (wrapped value/income)
            for col in ("owner", "value"):
                current[col].extend(bins.get(col, []))
            current["income"].extend(bins.get("type(s)", []) + bins.get("income", []))
            continue
        row_complete = (
            _ASSET_TYPE_BRACKET.search(" ".join(current["asset"])) is not None
            and _value_complete(" ".join(current["value"]))
        )
        if row_complete:
            flush()
        for col in ("owner", "value"):
            current[col].extend(bins.get(col, []))
        current["income"].extend(bins.get("type(s)", []) + bins.get("income", []))
        current["asset"].append(asset_text)
    flush()


def _build_holding(
    columns: dict[str, list[str]], annotations: list[str]
) -> ParsedHolding:
    """Assemble one ParsedHolding from binned column fragments."""
    asset_joined = " ".join(columns["asset"])
    account: str | None = None
    if "⇒" in asset_joined:
        account_part, _, rest = asset_joined.partition("⇒")
        account = account_part.strip()
        asset_joined = rest.strip()

    bracket = _ASSET_TYPE_BRACKET.search(asset_joined)
    ticker = _TICKER.search(asset_joined)
    name = _ASSET_TYPE_BRACKET.sub("", asset_joined)
    name = _TICKER.sub("", name)
    name = _clean(name).strip("- ")

    value_text = " ".join(columns["value"])
    value_min, value_max = _parse_range(value_text)
    value_disclosed_none = value_text.strip().lower() == "none"
    owner = OwnerType.SELF  # form convention: blank owner column = filer
    for token in columns["owner"]:
        if token in _OWNER_CODES:
            owner = _OWNER_CODES[token]
            break

    warnings: list[str] = []
    if bracket is None:
        warnings.append("no asset-type bracket on row")
    if value_min is None and not value_disclosed_none:
        warnings.append("no value range parsed")
    if not name:
        warnings.append("empty asset name")

    income_details = _clean(" ".join(columns["income"])) or None
    description = next((a[2:] for a in annotations if a.startswith("D:")), None)
    location = next((a[2:] for a in annotations if a.startswith("L:")), None)

    return ParsedHolding(
        account=account,
        owner=owner,
        asset_name=name,
        ticker=ticker.group(1) if ticker else None,
        asset_type_code=bracket.group(1) if bracket else None,
        value_min=value_min,
        value_max=value_max,
        income_details=income_details,
        description=_clean(description) if description else None,
        location=_clean(location) if location else None,
        raw_text=_clean(
            " ".join(
                [
                    *columns["asset"],
                    *columns["owner"],
                    *columns["value"],
                    *columns["income"],
                    *annotations,
                ]
            )
        ),
        parse_confidence=_holding_confidence(
            has_bracket=bracket is not None,
            value_min=value_min,
            value_disclosed_none=value_disclosed_none,
            name=name,
            warnings=len(warnings),
        ),
        warnings=tuple(warnings),
    )


def _parse_liabilities(
    rows: list[tuple[_Line, _Line]], liabilities: list[ParsedLiability]
) -> None:
    """Schedule C rows from a (header, body_line) stream (cells may wrap)."""
    current: dict[str, list[str]] = {
        "owner": [],
        "creditor": [],
        "date": [],
        "type": [],
        "amount": [],
    }

    def flush() -> None:
        if not any(current["creditor"]):
            for key in current:
                current[key] = []
            return
        value_min, value_max = _parse_range(" ".join(current["amount"]))
        owner = OwnerType.SELF
        for token in current["owner"]:
            if token in _OWNER_CODES:
                owner = _OWNER_CODES[token]
                break
        creditor = _clean(" ".join(current["creditor"]))
        liability_type = _clean(" ".join(current["type"])) or None
        date_incurred = _clean(" ".join(current["date"])) or None

        warnings: list[str] = []
        if not creditor:
            warnings.append("empty creditor name")
        if value_min is None:
            warnings.append("no amount range parsed")
        if date_incurred is None:
            warnings.append("no incurred date parsed")

        score = 1.0 - 0.2 * len(warnings)
        liabilities.append(
            ParsedLiability(
                owner=owner,
                creditor_name=creditor,
                liability_type=liability_type,
                date_incurred=date_incurred,
                value_min=value_min,
                value_max=value_max,
                raw_text=_clean(
                    " ".join(
                        [
                            *current["owner"],
                            *current["creditor"],
                            *current["date"],
                            *current["type"],
                            *current["amount"],
                        ]
                    )
                ),
                parse_confidence=max(0.1, round(score, 2)),
                warnings=tuple(warnings),
            )
        )
        for key in current:
            current[key] = []

    for header, line in rows:
        anchors = _anchors_for(header, _LIABILITIES_ANCHORS)
        if anchors is None:
            continue
        bin_starts = sorted(anchors.items(), key=lambda kv: kv[1])
        text = _clean(line.text)
        if not text:
            continue
        bins = _bin_assign(line, bin_starts)
        # Row start = fresh creditor text. Amount-only lines are continuations
        # of a wrapped amount ("$1,000,001 -" / "$5,000,000"), never new rows.
        starts_row = bool(bins.get("creditor"))
        row_complete = _DOLLARS.search(" ".join(current["amount"])) is not None
        if starts_row and row_complete:
            flush()
        for key in current:
            current[key].extend(bins.get(key, []))
    flush()


def parse_fd_pdf(path: Path) -> FdParseResult:
    """Parse one annual FD PDF into holdings and liabilities.

    Unreadable or non-FD documents return an empty result with a warning —
    one bad document never crashes the batch.
    """
    try:
        with pdfplumber.open(path) as pdf:
            pages = [_group_lines(page.extract_words()) for page in pdf.pages]
            header_text = " ".join(p.extract_text() or "" for p in pdf.pages[:1])
    except Exception as exc:
        logger.warning("cannot open %s: %s", path, exc)
        return FdParseResult(None, None, warnings=[f"unreadable PDF: {exc}"])

    result = FdParseResult(filer_name=None, state_district=None)
    for text_line in header_text.split("\n"):
        if text_line.startswith("Name:"):
            result.filer_name = text_line.removeprefix("Name:").strip()
        elif text_line.startswith("State/District:"):
            result.state_district = text_line.removeprefix("State/District:").strip()
    if "Filing Type:" not in header_text and "Financial Disclosure" not in header_text:
        result.warnings.append("not an annual FD document")
        return result

    # Build document-order (header, body_line) streams per section. Sections
    # can start mid-page on candidate filings; each body line keeps the header
    # whose x anchors govern it.
    section: str | None = None
    last_header: _Line | None = None
    asset_rows: list[tuple[_Line, _Line]] = []
    liability_rows: list[tuple[_Line, _Line]] = []
    for page_lines in pages:
        for line in page_lines:
            detected = _detect_section(line.text)
            if detected:
                section = detected
                last_header = line
                continue
            cleaned = _clean(line.text)
            lowered = cleaned.lower()
            if lowered in _OTHER_BANNERS or _SECTION_BANNER.match(lowered):
                section = None
                last_header = None
                continue
            if _FOOTER_LINE.match(lowered):
                continue
            if last_header is None:
                continue
            if section == "assets":
                asset_rows.append((last_header, line))
            elif section == "liabilities":
                liability_rows.append((last_header, line))

    _parse_assets(asset_rows, result.holdings)
    _parse_liabilities(liability_rows, result.liabilities)
    if not result.holdings:
        result.warnings.append("no holdings parsed (or 'None disclosed.')")
    return result


def cross_check_fd(result: FdParseResult) -> tuple[str, ...]:
    """Deterministic post-parse validation. Returns warnings (never raises)."""
    warnings: list[str] = list(result.warnings)
    for holding in result.holdings:
        if (
            holding.value_min is not None
            and holding.value_max is not None
            and holding.value_min > holding.value_max
        ):
            warnings.append(f"holding value min {holding.value_min} > max {holding.value_max}")
    for liability in result.liabilities:
        if (
            liability.value_min is not None
            and liability.value_max is not None
            and liability.value_min > liability.value_max
        ):
            warnings.append(
                f"liability amount min {liability.value_min} > max {liability.value_max}"
            )
    return tuple(warnings)
