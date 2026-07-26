"""Alpha-decay + signal-persistence analysis (M8): House PTR buys vs Tiingo.

Four questions, all on the local corpus (no new upstream sources):

1. Lag decay — how much of the post-trade move happens in the invisible
   window (trade date -> filing date), and what is left for a copy-trader in
   the +30d/+90d copy window? Bucketed by disclosure lag length.
2. Entry vs copy edge — SPY-excess returns at 30/90d measured from the
   trade-date t0 (the member's approximate entry) vs the filing-date t0 (the
   first moment the public could act). The gap is the disclosure-lag cost.
3. Walk-forward persistence — do members with strong past track records keep
   outperforming? Per-member mean 30d excess split at 2021-01-01, Spearman
   rank correlation and top-decile retention.
4. Size weighting — 30d excess bucketed by the STOCK Act amount brackets:
   do large buys outperform token buys?

Point-in-time discipline matches the M5b validation harness: entry = first
close on/after t0; exit = last close on/before horizon end; SPY excess; only
purchases with a ticker, a trade date, and a filing date. Price data comes
from the injected provider (cached Tiingo in production, synthetic in tests —
never the network in tests).
"""

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import sqrt
from pathlib import Path
from statistics import median

from sqlmodel import Session, select

from app.db.models import Asset, Filing, Member, Transaction
from app.intelligence.prices import close_on_or_after, close_on_or_before
from app.intelligence.validation import HorizonMetrics, wilson_interval

logger = logging.getLogger(__name__)

HORIZON_DAYS = (30, 90)
THIN_SAMPLE_MIN = 10
BENCHMARK_TICKER = "SPY"
PERIOD_SPLIT = date(2021, 1, 1)  # P1 = trades before this date, P2 = on/after
PERSISTENCE_MIN_N = 10

LAG_BUCKETS = ("0-9", "10-19", "20-29", "30-44", "45+")
SIZE_BUCKETS = ("<=15k", "15k-50k", "50k-100k", "100k-250k", "250k-500k", "500k+", "unknown")


@dataclass(frozen=True)
class TradeSignal:
    """One PTR purchase with both dates usable."""

    member_id: int
    member_name: str
    ticker: str
    trade_date: date
    filing_date: date
    amount_min: Decimal | None
    amount_max: Decimal | None


@dataclass
class AlphaDecayReport:
    """The full M8 analysis report (serialized to JSON + Markdown)."""

    generated_at: str
    data_as_of: str
    scope: str
    caveats: list[str]
    lag_decay: dict
    entry_vs_copy: dict[str, list[HorizonMetrics]]
    persistence: dict
    size_buckets: dict = field(default_factory=dict)


def bucket_for_lag(days: int) -> str:
    """Disclosure-lag bucket label for a non-negative lag in days."""
    if days < 10:
        return "0-9"
    if days < 20:
        return "10-19"
    if days < 30:
        return "20-29"
    if days < 45:
        return "30-44"
    return "45+"


def bucket_for_amount(amount_min: Decimal | None) -> str:
    """STOCK Act amount-bracket bucket from the bracket's lower bound."""
    if amount_min is None:
        return "unknown"
    if amount_min < 15_001:
        return "<=15k"
    if amount_min < 50_001:
        return "15k-50k"
    if amount_min < 100_001:
        return "50k-100k"
    if amount_min < 250_001:
        return "100k-250k"
    if amount_min < 500_001:
        return "250k-500k"
    return "500k+"


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (1-based) with ties sharing the mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation; None when undefined (n<2 or zero variance)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov: float = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry, strict=True))
    var_x: float = sum((a - mean_x) ** 2 for a in rx)
    var_y: float = sum((b - mean_y) ** 2 for b in ry)
    if var_x == 0 or var_y == 0:
        return None
    return cov / sqrt(var_x * var_y)


def collect_trade_signals(session: Session) -> tuple[list[TradeSignal], int]:
    """PTR purchases with a ticker, trade date, and filing date. (signals, skipped)."""
    rows = session.exec(
        select(Transaction, Asset, Filing, Member)
        .join(Asset, Transaction.asset_id == Asset.id)  # type: ignore[arg-type]
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .join(Member, Filing.member_id == Member.id)  # type: ignore[arg-type]
        .where(Transaction.transaction_type == "purchase")
    ).all()
    signals: list[TradeSignal] = []
    skipped = 0
    for tx, asset, filing, member in rows:
        if not asset.ticker or tx.transaction_date is None or filing.filing_date is None:
            skipped += 1
            continue
        signals.append(
            TradeSignal(
                member_id=member.id,  # type: ignore[arg-type]
                member_name=f"{member.first_name} {member.last_name}",
                ticker=asset.ticker.upper(),
                trade_date=tx.transaction_date,
                filing_date=filing.filing_date,
                amount_min=tx.amount_min,
                amount_max=tx.amount_max,
            )
        )
    return signals, skipped


def _window_return(series: dict[date, float], start: date, end: date) -> float | None:
    """Return from first close on/after `start` to last close on/before `end`."""
    entry = close_on_or_after(series, start)
    exit_ = close_on_or_before(series, end)
    if entry is None or exit_ is None or exit_[0] <= entry[0]:
        return None
    return exit_[1] / entry[1] - 1.0


def _excess(
    stock: dict[date, float], benchmark: dict[date, float], start: date, end: date
) -> float | None:
    """SPY-excess return over [start, end]; None when either leg is unpriced."""
    stock_ret = _window_return(stock, start, end)
    bench_ret = _window_return(benchmark, start, end)
    if stock_ret is None or bench_ret is None:
        return None
    return stock_ret - bench_ret


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


def _lag_decay(
    signals: list[TradeSignal],
    series_by_ticker: dict[str, dict[date, float]],
    benchmark: dict[date, float],
) -> dict:
    """Invisible-window vs copy-window excess, overall and by lag bucket."""
    excluded_negative = 0
    usable: list[tuple[TradeSignal, int]] = []
    for signal in signals:
        lag = (signal.filing_date - signal.trade_date).days
        if lag < 0:
            excluded_negative += 1
            continue
        usable.append((signal, lag))

    invisible: list[float] = []
    copy_30: list[float] = []
    copy_90: list[float] = []
    bucket_rows: dict[str, dict[str, list[float]]] = {
        label: {"invisible": [], "copy_30": []} for label in LAG_BUCKETS
    }
    for signal, lag in usable:
        stock = series_by_ticker.get(signal.ticker)
        if not stock:
            continue
        inv = _excess(stock, benchmark, signal.trade_date, signal.filing_date)
        c30 = _excess(
            stock, benchmark, signal.filing_date, signal.filing_date + timedelta(days=30)
        )
        c90 = _excess(
            stock, benchmark, signal.filing_date, signal.filing_date + timedelta(days=90)
        )
        bucket = bucket_rows[bucket_for_lag(lag)]
        if inv is not None:
            invisible.append(inv)
            bucket["invisible"].append(inv)
        if c30 is not None:
            copy_30.append(c30)
            bucket["copy_30"].append(c30)
        if c90 is not None:
            copy_90.append(c90)

    lag_days = [lag for _signal, lag in usable]

    def _mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "overall": {
            "signals": len(usable),
            "mean_lag_days": _mean([float(d) for d in lag_days]),
            "median_lag_days": median(lag_days) if lag_days else None,
            "invisible_signals": len(invisible),
            "mean_invisible_excess": _mean(invisible),
            "median_invisible_excess": median(invisible) if invisible else None,
            "copy_30d_signals": len(copy_30),
            "mean_copy_30d_excess": _mean(copy_30),
            "copy_90d_signals": len(copy_90),
            "mean_copy_90d_excess": _mean(copy_90),
        },
        "buckets": {
            label: {
                "signals": len(rows["invisible"]),
                "mean_invisible_excess": _mean(rows["invisible"]),
                "mean_copy_30d_excess": _mean(rows["copy_30"]),
            }
            for label, rows in bucket_rows.items()
        },
        "excluded_negative_lag": excluded_negative,
    }


def _entry_vs_copy(
    signals: list[TradeSignal],
    series_by_ticker: dict[str, dict[date, float]],
    benchmark: dict[date, float],
) -> dict[str, list[HorizonMetrics]]:
    """30/90d excess from trade-date t0 (entry) vs filing-date t0 (copy)."""
    entry_excess: dict[int, list[float]] = {h: [] for h in HORIZON_DAYS}
    copy_excess: dict[int, list[float]] = {h: [] for h in HORIZON_DAYS}
    for signal in signals:
        stock = series_by_ticker.get(signal.ticker)
        if not stock:
            continue
        for horizon in HORIZON_DAYS:
            entry = _excess(
                stock, benchmark, signal.trade_date, signal.trade_date + timedelta(days=horizon)
            )
            copy = _excess(
                stock, benchmark, signal.filing_date, signal.filing_date + timedelta(days=horizon)
            )
            if entry is not None:
                entry_excess[horizon].append(entry)
            if copy is not None:
                copy_excess[horizon].append(copy)
    return {
        "entry_t0_trade_date": [_metrics(h, entry_excess[h]) for h in HORIZON_DAYS],
        "copy_t0_filing_date": [_metrics(h, copy_excess[h]) for h in HORIZON_DAYS],
    }


def _persistence(
    signals: list[TradeSignal],
    series_by_ticker: dict[str, dict[date, float]],
    benchmark: dict[date, float],
) -> dict:
    """Per-member 30d excess split at PERIOD_SPLIT; Spearman + retention."""
    per_member: dict[str, dict[str, list[float]]] = {}
    for signal in signals:
        stock = series_by_ticker.get(signal.ticker)
        if not stock:
            continue
        excess = _excess(
            stock, benchmark, signal.trade_date, signal.trade_date + timedelta(days=30)
        )
        if excess is None:
            continue
        period = "p1" if signal.trade_date < PERIOD_SPLIT else "p2"
        per_member.setdefault(signal.member_name, {"p1": [], "p2": []})[period].append(excess)

    table: list[dict] = []
    for name, periods in sorted(per_member.items()):
        p1, p2 = periods["p1"], periods["p2"]
        if len(p1) < PERSISTENCE_MIN_N or len(p2) < PERSISTENCE_MIN_N:
            continue
        mean1 = sum(p1) / len(p1)
        mean2 = sum(p2) / len(p2)
        table.append(
            {
                "member": name,
                "p1_n": len(p1),
                "p1_mean_30d_excess": mean1,
                "p2_n": len(p2),
                "p2_mean_30d_excess": mean2,
                "delta": mean2 - mean1,
            }
        )

    means1 = [row["p1_mean_30d_excess"] for row in table]
    means2 = [row["p2_mean_30d_excess"] for row in table]
    retention: float | None = None
    if table:
        top = max(1, (len(table) + 9) // 10)  # top decile size, at least 1
        by_p1 = sorted(table, key=lambda r: -r["p1_mean_30d_excess"])
        by_p2 = sorted(table, key=lambda r: -r["p2_mean_30d_excess"])
        top_p1 = {row["member"] for row in by_p1[:top]}
        top_p2 = {row["member"] for row in by_p2[:top]}
        retention = len(top_p1 & top_p2) / top
    return {
        "members": len(table),
        "min_n_per_period": PERSISTENCE_MIN_N,
        "period_split": PERIOD_SPLIT.isoformat(),
        "spearman": spearman(means1, means2),
        "top_decile_retention": retention,
        "table": table,
    }


def _size_buckets(
    signals: list[TradeSignal],
    series_by_ticker: dict[str, dict[date, float]],
    benchmark: dict[date, float],
) -> dict:
    """30d entry-side excess bucketed by STOCK Act amount bracket."""
    rows: dict[str, list[float]] = {label: [] for label in SIZE_BUCKETS}
    for signal in signals:
        stock = series_by_ticker.get(signal.ticker)
        if not stock:
            continue
        excess = _excess(
            stock, benchmark, signal.trade_date, signal.trade_date + timedelta(days=30)
        )
        if excess is not None:
            rows[bucket_for_amount(signal.amount_min)].append(excess)
    return {
        label: {
            "signals": len(values),
            "mean_30d_excess": (sum(values) / len(values)) if values else None,
        }
        for label, values in rows.items()
        if values
    }


def run_analysis(
    session: Session,
    price_provider: Callable[[str], dict[date, float]],
    *,
    as_of: date | None = None,
) -> AlphaDecayReport:
    """Run all four M8 analyses. `price_provider(ticker) -> date->close series`."""
    effective_as_of = as_of or date.today()
    signals, skipped = collect_trade_signals(session)
    caveats = [
        "Scope: PTR purchases only; the sales side is out of scope "
        "(unobservable exits).",
        "Amount brackets are ranges, not exacts; size buckets use the bracket "
        "lower bound.",
        "Daily closes only (Tiingo adjusted); no intraday data.",
    ]
    if skipped:
        caveats.append(f"{skipped} purchase rows excluded (no ticker, trade date, or filing date).")

    tickers = sorted({s.ticker for s in signals} | {BENCHMARK_TICKER})
    series_by_ticker: dict[str, dict[date, float]] = {}
    missing: list[str] = []
    for ticker in tickers:
        series = {d: c for d, c in price_provider(ticker).items() if d <= effective_as_of}
        if series:
            series_by_ticker[ticker] = series
        else:
            missing.append(ticker)
    if missing:
        caveats.append(f"No price data for: {', '.join(missing)}.")
    benchmark = series_by_ticker.get(BENCHMARK_TICKER, {})
    if not benchmark:
        caveats.append("No SPY benchmark series — all excess metrics are empty.")

    return AlphaDecayReport(
        generated_at=datetime.now(UTC).isoformat(),
        data_as_of=effective_as_of.isoformat(),
        scope="Alpha decay + signal persistence (PTR purchases, "
        "trade-date vs filing-date t0, excess vs SPY)",
        caveats=caveats,
        lag_decay=_lag_decay(signals, series_by_ticker, benchmark),
        entry_vs_copy=_entry_vs_copy(signals, series_by_ticker, benchmark),
        persistence=_persistence(signals, series_by_ticker, benchmark),
        size_buckets=_size_buckets(signals, series_by_ticker, benchmark),
    )


def _fmt(value: float | None, pct: bool = True) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1%}" if pct else f"{value:.1f}"


def render_markdown(report: AlphaDecayReport) -> str:
    """Markdown rendering of the report (same numbers as the JSON)."""
    overall = report.lag_decay["overall"]
    lines = [
        "# Alpha decay + signal persistence — M8 analysis",
        "",
        f"Generated: {report.generated_at} · Data as of: {report.data_as_of}",
        f"Scope: {report.scope}",
        "",
        "## Caveats",
        *[f"- {caveat}" for caveat in report.caveats],
        "",
        "## 1. Lag decay (invisible window vs copy window)",
        "",
        f"Signals: {overall['signals']} (excluded negative lag: "
        f"{report.lag_decay['excluded_negative_lag']}) · "
        f"median lag: {_fmt(overall['median_lag_days'], pct=False)} days",
        "",
        "| window | signals | mean excess |",
        "|---|---|---|",
        f"| invisible (trade -> filing) | {overall['invisible_signals']} "
        f"| {_fmt(overall['mean_invisible_excess'])} |",
        f"| copy +30d (filing -> +30d) | {overall['copy_30d_signals']} "
        f"| {_fmt(overall['mean_copy_30d_excess'])} |",
        f"| copy +90d (filing -> +90d) | {overall['copy_90d_signals']} "
        f"| {_fmt(overall['mean_copy_90d_excess'])} |",
        "",
        "| lag bucket | signals | mean invisible excess | mean copy 30d excess |",
        "|---|---|---|---|",
    ]
    for label, bucket in report.lag_decay["buckets"].items():
        lines.append(
            f"| {label} | {bucket['signals']} "
            f"| {_fmt(bucket['mean_invisible_excess'])} "
            f"| {_fmt(bucket['mean_copy_30d_excess'])} |"
        )
    lines += ["", "## 2. Entry vs copy edge"]
    for cohort, metrics in report.entry_vs_copy.items():
        lines += [
            "",
            f"### t0 = {cohort}",
            "| horizon | signals | mean excess | hit rate |",
            "|---|---|---|---|",
        ]
        for m in metrics:
            lines.append(
                f"| {m.horizon_days}d | {m.signals} "
                f"| {_fmt(m.mean_excess_return)} | {_fmt(m.hit_rate)} |"
            )
    persistence = report.persistence
    lines += [
        "",
        "## 3. Walk-forward persistence",
        "",
        f"Qualifying members (n>={persistence['min_n_per_period']} per period): "
        f"{persistence['members']} · Spearman rank correlation: "
        f"{_fmt(persistence['spearman'], pct=False)} · top-decile retention: "
        f"{_fmt(persistence['top_decile_retention'])}",
        "",
        "| member | P1 n | P1 mean | P2 n | P2 mean | delta |",
        "|---|---|---|---|---|---|",
    ]
    for row in persistence["table"]:
        lines.append(
            f"| {row['member']} | {row['p1_n']} | {_fmt(row['p1_mean_30d_excess'])} "
            f"| {row['p2_n']} | {_fmt(row['p2_mean_30d_excess'])} | {_fmt(row['delta'])} |"
        )
    lines += [
        "",
        "## 4. Size weighting (entry-side 30d excess by amount bracket)",
        "",
        "| bracket | signals | mean 30d excess |",
        "|---|---|---|",
    ]
    for label, bucket in report.size_buckets.items():
        lines.append(
            f"| {label} | {bucket['signals']} | {_fmt(bucket['mean_30d_excess'])} |"
        )
    lines += [
        "",
        "_Entry t0 = trade date (member's approximate entry); copy t0 = filing "
        "date (first public knowledge). Excess = stock return minus SPY return "
        "over the same window._",
    ]
    return "\n".join(lines) + "\n"


def write_analysis_report(report: AlphaDecayReport, exports_dir: Path) -> tuple[Path, Path]:
    """Write alpha_decay_report.json and alpha_decay_report.md."""
    exports_dir.mkdir(parents=True, exist_ok=True)
    json_path = exports_dir / "alpha_decay_report.json"
    md_path = exports_dir / "alpha_decay_report.md"
    json_path.write_text(json.dumps(asdict(report), indent=2, default=str) + "\n")
    md_path.write_text(render_markdown(report))
    return json_path, md_path
