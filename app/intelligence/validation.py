"""Historical validation harness (M5b): PTR-purchase signals vs forward returns.

Scope (labeled in every report): "PTR purchase signal validation" — do PTR
purchases of tickered stocks beat SPY over fixed horizons, measured from the
FILING date t0 (the first moment the public could know)? Full score-history
validation needs historical committee rosters and is a documented follow-up.

Point-in-time discipline (per tasks/plan.md §3-M5): entry = first close on or
after t0; exit = last close on or before t0+horizon; only filings with a real
filing_date are used; data-as-of dates are recorded in the report. Metrics
are decomposed: counts, mean excess return, Wilson-CI hit rates, per-quarter
and overlap-cohort breakdowns, and an explicit thin-sample flag — never one
headline number.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select

from app.db.models import Asset, Committee, CommitteeMembership, Filing, Member, Transaction
from app.intelligence.mappings import load_mappings
from app.intelligence.prices import close_on_or_after, close_on_or_before

logger = logging.getLogger(__name__)

HORIZON_DAYS = (30, 90)
THIN_SAMPLE_MIN = 10
BENCHMARK_TICKER = "SPY"


@dataclass(frozen=True)
class Signal:
    """One PTR purchase with a usable filing date and ticker."""

    member_name: str
    ticker: str
    t0: date  # filing date — first public knowledge
    sector_overlap: bool


@dataclass(frozen=True)
class HorizonMetrics:
    """Decomposed metrics for one horizon over one cohort."""

    horizon_days: int
    signals: int
    mean_excess_return: float | None
    hit_rate: float | None
    wilson_low: float | None
    wilson_high: float | None
    thin_sample: bool


@dataclass
class ValidationReport:
    """The full validation report (serialized to JSON + Markdown)."""

    generated_at: str
    data_as_of: str
    scope: str
    caveats: list[str]
    horizons: list[HorizonMetrics]
    cohorts: dict[str, list[HorizonMetrics]] = field(default_factory=dict)
    per_quarter: dict[str, list[HorizonMetrics]] = field(default_factory=dict)


def wilson_interval(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def forward_return(series: dict[date, float], t0: date, days: int) -> float | None:
    """Return from first close on/after t0 to last close on/before t0+days."""
    entry = close_on_or_after(series, t0)
    exit_ = close_on_or_before(series, t0 + timedelta(days=days))
    if entry is None or exit_ is None or exit_[0] <= entry[0]:
        return None
    return exit_[1] / entry[1] - 1.0


def _metrics(horizon: int, excess_returns: list[float]) -> HorizonMetrics:
    n = len(excess_returns)
    if n == 0:
        return HorizonMetrics(horizon, 0, None, None, None, None, True)
    hits = sum(1 for r in excess_returns if r > 0)
    low, high = wilson_interval(hits, n)
    return HorizonMetrics(
        horizon_days=horizon,
        signals=n,
        mean_excess_return=sum(excess_returns) / n,
        hit_rate=hits / n,
        wilson_low=low,
        wilson_high=high,
        thin_sample=n < THIN_SAMPLE_MIN,
    )


def collect_signals(session: Session) -> tuple[list[Signal], int]:
    """PTR purchases with a ticker and a filing date. Returns (signals, skipped)."""
    mappings = load_mappings()
    rows = session.exec(
        select(Transaction, Asset, Filing, Member)
        .join(Asset, Transaction.asset_id == Asset.id)  # type: ignore[arg-type]
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .join(Member, Filing.member_id == Member.id)  # type: ignore[arg-type]
        .where(Transaction.transaction_type == "purchase")
    ).all()

    member_sectors: dict[int, set[str]] = {}
    memberships = session.exec(
        select(CommitteeMembership, Committee).join(
            Committee, onclause=CommitteeMembership.committee_id == Committee.id  # type: ignore[arg-type]
        )
    ).all()
    for membership, committee in memberships:
        sectors = mappings.committee_sectors.get(committee.code, [])
        member_sectors.setdefault(membership.member_id, set()).update(sectors)

    signals: list[Signal] = []
    skipped = 0
    for _tx, asset, filing, member in rows:
        if not asset.ticker or filing.filing_date is None:
            skipped += 1
            continue
        sector = mappings.ticker_sectors.get(asset.ticker.upper())
        member_sector_set = member_sectors.get(member.id, set())  # type: ignore[arg-type]
        overlap = bool(sector and sector in member_sector_set)
        signals.append(
            Signal(
                member_name=f"{member.first_name} {member.last_name}",
                ticker=asset.ticker.upper(),
                t0=filing.filing_date,
                sector_overlap=overlap,
            )
        )
    return signals, skipped


def run_validation(
    session: Session,
    price_provider: Callable[[str], dict[date, float]],
    *,
    as_of: date | None = None,
) -> ValidationReport:
    """Run the harness. `price_provider(ticker) -> date->close series`."""
    effective_as_of = as_of or date.today()
    signals, skipped = collect_signals(session)
    caveats = [
        "Scope: PTR purchase signal validation. Full score-history validation "
        "needs historical committee rosters (follow-up).",
    ]
    if skipped:
        caveats.append(f"{skipped} purchase rows excluded (no ticker or no filing date).")

    tickers = sorted({s.ticker for s in signals} | {BENCHMARK_TICKER})
    series_by_ticker: dict[str, dict[date, float]] = {}
    missing: list[str] = []
    for ticker in tickers:
        series = price_provider(ticker)
        if series:
            series_by_ticker[ticker] = series
        else:
            missing.append(ticker)
    if missing:
        caveats.append(f"No price data for: {', '.join(missing)}.")
    benchmark = series_by_ticker.get(BENCHMARK_TICKER, {})

    excess: dict[int, list[float]] = {h: [] for h in HORIZON_DAYS}
    cohort_excess: dict[str, dict[int, list[float]]] = {
        "overlap": {h: [] for h in HORIZON_DAYS},
        "no_overlap": {h: [] for h in HORIZON_DAYS},
    }
    quarter_excess: dict[str, dict[int, list[float]]] = {}
    for signal in signals:
        signal_series = series_by_ticker.get(signal.ticker)
        if not signal_series:
            continue
        cohort = "overlap" if signal.sector_overlap else "no_overlap"
        quarter = f"{signal.t0.year}Q{(signal.t0.month - 1) // 3 + 1}"
        quarter_excess.setdefault(quarter, {h: [] for h in HORIZON_DAYS})
        for horizon in HORIZON_DAYS:
            stock_ret = forward_return(signal_series, signal.t0, horizon)
            bench_ret = forward_return(benchmark, signal.t0, horizon)
            if stock_ret is None or bench_ret is None:
                continue
            value = stock_ret - bench_ret
            excess[horizon].append(value)
            cohort_excess[cohort][horizon].append(value)
            quarter_excess[quarter][horizon].append(value)

    report = ValidationReport(
        generated_at=datetime.now(UTC).isoformat(),
        data_as_of=effective_as_of.isoformat(),
        scope="PTR purchase signal validation (filing-date t0, excess vs SPY)",
        caveats=caveats,
        horizons=[_metrics(h, excess[h]) for h in HORIZON_DAYS],
        cohorts={
            cohort: [_metrics(h, values[h]) for h in HORIZON_DAYS]
            for cohort, values in cohort_excess.items()
        },
        per_quarter={
            quarter: [_metrics(h, values[h]) for h in HORIZON_DAYS]
            for quarter, values in sorted(quarter_excess.items())
        },
    )
    return report


def _fmt(value: float | None, pct: bool = True) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1%}" if pct else f"{value:.3f}"


def _metrics_rows(metrics: list[HorizonMetrics]) -> list[str]:
    lines = ["| horizon | signals | mean excess | hit rate | Wilson 95% CI | thin? |",
             "|---|---|---|---|---|---|"]
    for m in metrics:
        ci = f"[{_fmt(m.wilson_low)}, {_fmt(m.wilson_high)}]" if m.signals else "n/a"
        lines.append(
            f"| {m.horizon_days}d | {m.signals} | {_fmt(m.mean_excess_return)} "
            f"| {_fmt(m.hit_rate)} | {ci} | {'YES' if m.thin_sample else 'no'} |"
        )
    return lines


def render_markdown(report: ValidationReport) -> str:
    """Markdown rendering of the report (same decomposed numbers as the JSON)."""
    lines = [
        "# Validation report — PTR purchase signal validation",
        "",
        f"Generated: {report.generated_at} · Data as of: {report.data_as_of}",
        f"Scope: {report.scope}",
        "",
        "## Caveats",
        *[f"- {caveat}" for caveat in report.caveats],
        "",
        "## Overall",
        *_metrics_rows(report.horizons),
    ]
    for cohort, metrics in report.cohorts.items():
        lines += ["", f"## Cohort: {cohort}", *_metrics_rows(metrics)]
    if report.per_quarter:
        lines += ["", "## Per quarter"]
        for quarter, metrics in report.per_quarter.items():
            lines += ["", f"### {quarter}", *_metrics_rows(metrics)]
    lines += [
        "",
        "_Hit rate = share of signals with positive excess return vs SPY. "
        "Thin-sample flags apply below 10 signals — treat those numbers as "
        "insufficient evidence, not as findings._",
    ]
    return "\n".join(lines) + "\n"


def write_report(report: ValidationReport, exports_dir: Path) -> tuple[Path, Path]:
    """Write validation_report.json and validation_report.md."""
    exports_dir.mkdir(parents=True, exist_ok=True)
    json_path = exports_dir / "validation_report.json"
    md_path = exports_dir / "validation_report.md"
    json_path.write_text(json.dumps(asdict(report), indent=2, default=str) + "\n")
    md_path.write_text(render_markdown(report))
    return json_path, md_path
