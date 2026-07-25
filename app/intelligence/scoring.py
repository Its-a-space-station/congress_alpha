"""Policy-edge scoring engine (M4). Deterministic and decomposable.

For each member, three first-class components are computed from curated
mapping files (never guessed: unmapped committees/assets contribute nothing):

- committee_sector_holdings_overlap: the member sits on a committee mapped
  to sector S and holds Position assets mapped to S.
- committee_sector_trading_overlap: the same via parsed PTR transactions,
  recency-weighted (full weight within `recency_full_days`, half within
  `recency_half_days`, ignored beyond).
- repeat_pattern: flat weight when the count of overlapping trades meets
  `repeat_min_trades` — conservative by construction.

Every component is stored as a ScoreComponent row with a human-readable
`details` string naming its exact contributors; the composite row equals
the sum of the component rows (asserted by tests). All knobs live in
ScoringConfig — bump CONFIG_VERSION on any change. No evolutionary tuning.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.core.enums import ScoreLabel, TransactionType
from app.db.models import (
    Asset,
    Committee,
    CommitteeMembership,
    Filing,
    Member,
    NetWorthEstimate,
    Position,
    ScoreComponent,
    ScoreRun,
    Transaction,
)
from app.intelligence.mappings import Mappings
from app.intelligence.validation import forward_return

logger = logging.getLogger(__name__)

CONFIG_VERSION = "policy-edge-0.2"
PARSER_VERSION_TAG = "house-ptr-0.1+house-fd-0.1"


@dataclass(frozen=True)
class ScoringConfig:
    """Every weight, window, threshold, and band for the scoring engine."""

    holdings_sector_weight: float = 2.0  # per distinct overlapping sector (holdings)
    holdings_asset_weight: float = 1.0  # per distinct overlapping asset (holdings)
    trading_sector_weight: float = 2.0  # per distinct overlapping sector (trading)
    trading_asset_weight: float = 1.0  # per distinct overlapping asset (trading)
    recency_full_days: int = 90  # trades this recent get full weight
    recency_half_days: int = 365  # trades this recent get half weight; older ignored
    repeat_min_trades: int = 3  # overlapping trades needed for the repeat component
    repeat_weight: float = 2.0  # flat weight once the threshold is met
    # relative_size: biggest single buy as a share of net worth (conservative
    # ratio amount_min / net_worth_max), counted up to this cap.
    relative_size_window_days: int = 365
    relative_size_cap: float = 0.02
    relative_size_weight: float = 2.0
    # track_record: per-member mean 30d excess vs SPY, clamped to ±max_excess,
    # gated on at least this many completed signals.
    track_record_min_signals: int = 5
    track_record_weight: float = 20.0
    track_record_max_excess: float = 0.10
    track_record_horizon_days: int = 30
    band_moderate: float = 3.0
    band_elevated: float = 6.0
    band_high: float = 10.0


DEFAULT_CONFIG = ScoringConfig()


def label_for(score: float, config: ScoringConfig = DEFAULT_CONFIG) -> ScoreLabel:
    """Map a composite score to its band."""
    if score >= config.band_high:
        return ScoreLabel.HIGH
    if score >= config.band_elevated:
        return ScoreLabel.ELEVATED
    if score >= config.band_moderate:
        return ScoreLabel.MODERATE
    return ScoreLabel.LOW


@dataclass(frozen=True)
class _Overlap:
    """Shared overlap facts for one member."""

    sectors_by_committee: dict[str, list[str]]  # committee code -> mapped sectors
    committee_names: dict[str, str]  # committee code -> verbatim name
    held_assets: dict[int, tuple[str, str]]  # asset id -> (sector, asset name)
    traded: list[tuple[str, str, str, date | None]]  # (sector, asset name, kind, date)


def _member_overlap(
    session: Session, member_id: int, mappings: Mappings
) -> _Overlap:
    memberships = session.exec(
        select(CommitteeMembership).where(CommitteeMembership.member_id == member_id)
    ).all()
    sectors_by_committee: dict[str, list[str]] = {}
    committee_names: dict[str, str] = {}
    for membership in memberships:
        committee = session.get(Committee, membership.committee_id)
        if committee is None or committee.code not in mappings.committee_sectors:
            continue
        sectors_by_committee[committee.code] = mappings.committee_sectors[committee.code]
        committee_names[committee.code] = committee.name

    positions = session.exec(select(Position).where(Position.member_id == member_id)).all()
    held_assets: dict[int, tuple[str, str]] = {}
    for position in positions:
        asset = session.get(Asset, position.asset_id)
        if asset and asset.id is not None and asset.ticker:
            sector = mappings.ticker_sectors.get(asset.ticker.upper())
            if sector:
                held_assets[asset.id] = (sector, asset.name)

    transactions = session.exec(
        select(Transaction)
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .where(Filing.member_id == member_id, Filing.filing_type_raw == "P")
    ).all()
    traded: list[tuple[str, str, str, date | None]] = []
    for tx in transactions:
        asset = session.get(Asset, tx.asset_id) if tx.asset_id else None
        if asset and asset.ticker and asset.ticker.upper() in mappings.ticker_sectors:
            traded.append(
                (
                    mappings.ticker_sectors[asset.ticker.upper()],
                    asset.name,
                    tx.transaction_type.value,
                    tx.transaction_date,
                )
            )
    return _Overlap(
        sectors_by_committee=sectors_by_committee,
        committee_names=committee_names,
        held_assets=held_assets,
        traded=traded,
    )


def _holdings_component(overlap: _Overlap, config: ScoringConfig) -> tuple[float, str]:
    sectors_hit: dict[str, list[str]] = {}  # sector -> contributing committees
    assets_hit: dict[str, set[str]] = {}  # sector -> contributing asset names
    for code, sectors in overlap.sectors_by_committee.items():
        for _asset_id, (sector, name) in overlap.held_assets.items():
            if sector in sectors:
                sectors_hit.setdefault(sector, []).append(code)
                assets_hit.setdefault(sector, set()).add(name)
    value = config.holdings_sector_weight * len(sectors_hit) + config.holdings_asset_weight * sum(
        len(names) for names in assets_hit.values()
    )
    details = "; ".join(
        f"{sector} via {sorted(set(codes))}: {sorted(names)}"
        for sector, codes in sorted(sectors_hit.items())
        for names in [assets_hit[sector]]
    )
    return value, details or "no overlap"


def _trading_component(
    overlap: _Overlap, config: ScoringConfig, as_of: date
) -> tuple[float, str, int]:
    sectors_hit: dict[str, list[str]] = {}
    assets_hit: dict[str, set[str]] = {}
    weighted_trades = 0.0
    overlapping_trade_count = 0
    for sector, name, _kind, tx_date in overlap.traded:
        committees = [code for code, secs in overlap.sectors_by_committee.items() if sector in secs]
        if not committees:
            continue
        if tx_date is None:
            continue
        age = (as_of - tx_date).days
        if age <= config.recency_full_days:
            factor = 1.0
        elif age <= config.recency_half_days:
            factor = 0.5
        else:
            continue
        overlapping_trade_count += 1
        weighted_trades += factor
        for code in committees:
            sectors_hit.setdefault(sector, []).append(code)
        assets_hit.setdefault(sector, set()).add(name)

    base = config.trading_sector_weight * len(sectors_hit)
    base += config.trading_asset_weight * sum(len(n) for n in assets_hit.values())
    # Recency weighting: the component scales by the average freshness of the
    # overlapping trades (1.0 recent, 0.5 within the half window).
    recency_factor = weighted_trades / overlapping_trade_count if overlapping_trade_count else 0
    value = base * recency_factor
    details = "; ".join(
        f"{sector} via {sorted(set(codes))}: {sorted(names)}"
        for sector, codes in sorted(sectors_hit.items())
        for names in [assets_hit[sector]]
    )
    return value, details or "no recent overlapping trades", overlapping_trade_count


def _repeat_component(count: int, config: ScoringConfig) -> tuple[float, str]:
    if count >= config.repeat_min_trades:
        details = f"{count} overlapping trades (>= threshold {config.repeat_min_trades})"
        return config.repeat_weight, details
    return 0.0, f"{count} overlapping trades (< threshold {config.repeat_min_trades})"


def _relative_size_component(
    session: Session, member_id: int, config: ScoringConfig, as_of: date
) -> tuple[float, str]:
    """Biggest single buy as a share of net worth (conservative bounds).

    Uses the MAX single-trade ratio in the window — one outsized bet is the
    signal; summing would double-count serial buyers (covered by repeat_pattern).
    Zero with a "no basis" note when the member has no net-worth estimate.
    """
    net_worth = session.exec(
        select(NetWorthEstimate)
        .where(NetWorthEstimate.member_id == member_id)
        .order_by(NetWorthEstimate.year.desc())  # type: ignore[attr-defined]
    ).first()
    if net_worth is None or not net_worth.estimate_max or net_worth.estimate_max <= 0:
        return 0.0, "no basis (no net-worth estimate)"

    window_start = as_of - timedelta(days=config.relative_size_window_days)
    purchases = session.exec(
        select(Transaction)
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .where(
            Filing.member_id == member_id,
            Filing.filing_type_raw == "P",
            Transaction.transaction_type == TransactionType.PURCHASE,
            Transaction.transaction_date >= window_start,  # type: ignore[operator]
            Transaction.transaction_date <= as_of,  # type: ignore[operator]
        )
    ).all()

    best_ratio = 0.0
    best_amount = None
    for tx in purchases:
        if tx.amount_min is None:
            continue
        ratio = float(tx.amount_min) / float(net_worth.estimate_max)
        if ratio > best_ratio:
            best_ratio = ratio
            best_amount = tx.amount_min
    if best_amount is None:
        return 0.0, "no purchases in window"

    value = config.relative_size_weight * min(best_ratio / config.relative_size_cap, 1.0)
    details = (
        f"largest buy ${float(best_amount):,.0f} vs net-worth upper bound "
        f"${float(net_worth.estimate_max):,.0f} = {best_ratio:.2%} "
        f"(cap {config.relative_size_cap:.1%})"
    )
    return value, details


def _track_record_component(
    session: Session,
    member_id: int,
    config: ScoringConfig,
    as_of: date,
    price_provider: Callable[[str], dict[date, float]] | None,
) -> tuple[float, str]:
    """Per-member mean excess vs SPY on past purchases (point-in-time).

    Only signals with t0 before as_of and a COMPLETED horizon window count;
    price series are truncated to as_of. Gated on track_record_min_signals.
    """
    if price_provider is None:
        return 0.0, "no price provider configured"

    horizon = config.track_record_horizon_days
    window_end = as_of - timedelta(days=horizon)
    purchases = session.exec(
        select(Transaction, Asset, Filing)
        .join(Asset, Transaction.asset_id == Asset.id)  # type: ignore[arg-type]
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .where(
            Filing.member_id == member_id,
            Filing.filing_type_raw == "P",
            Transaction.transaction_type == TransactionType.PURCHASE,
            Filing.filing_date < window_end,  # type: ignore[operator]
        )
    ).all()

    tickers = sorted(
        {asset.ticker.upper() for _tx, asset, _f in purchases if asset.ticker} | {"SPY"}
    )
    series_by_ticker = {
        ticker: {d: c for d, c in price_provider(ticker).items() if d <= as_of}
        for ticker in tickers
    }
    benchmark = series_by_ticker.get("SPY", {})

    excess_returns: list[float] = []
    for _tx, asset, filing in purchases:
        if not asset.ticker or filing.filing_date is None:
            continue
        series = series_by_ticker.get(asset.ticker.upper(), {})
        stock_ret = forward_return(series, filing.filing_date, horizon)
        bench_ret = forward_return(benchmark, filing.filing_date, horizon)
        if stock_ret is not None and bench_ret is not None:
            excess_returns.append(stock_ret - bench_ret)

    n = len(excess_returns)
    if n < config.track_record_min_signals:
        return 0.0, f"insufficient history (n={n} < {config.track_record_min_signals})"
    mean_excess = sum(excess_returns) / n
    clamped = max(-config.track_record_max_excess, min(config.track_record_max_excess, mean_excess))
    value = config.track_record_weight * clamped
    details = f"n={n}, mean {horizon}d excess vs SPY {mean_excess:+.1%}"
    if clamped != mean_excess:
        details += f" (clamped to {clamped:+.1%})"
    return value, details


def score_member(
    session: Session,
    member: Member,
    mappings: Mappings,
    config: ScoringConfig = DEFAULT_CONFIG,
    *,
    as_of: date | None = None,
    price_provider: Callable[[str], dict[date, float]] | None = None,
) -> list[ScoreComponent]:
    """Compute (but do not persist) the component rows for one member."""
    assert member.id is not None
    effective_as_of = as_of or date.today()
    overlap = _member_overlap(session, member.id, mappings)

    holdings_value, holdings_details = _holdings_component(overlap, config)
    trading_value, trading_details, trade_count = _trading_component(
        overlap, config, effective_as_of
    )
    repeat_value, repeat_details = _repeat_component(trade_count, config)
    size_value, size_details = _relative_size_component(
        session, member.id, config, effective_as_of
    )
    track_value, track_details = _track_record_component(
        session, member.id, config, effective_as_of, price_provider
    )
    composite = holdings_value + trading_value + repeat_value + size_value + track_value

    rows = [
        ScoreComponent(
            score_run_id=0,  # set by the caller
            member_id=member.id,
            component="committee_sector_holdings_overlap",
            value=holdings_value,
            details=holdings_details,
        ),
        ScoreComponent(
            score_run_id=0,
            member_id=member.id,
            component="committee_sector_trading_overlap",
            value=trading_value,
            details=trading_details,
        ),
        ScoreComponent(
            score_run_id=0,
            member_id=member.id,
            component="repeat_pattern",
            value=repeat_value,
            details=repeat_details,
        ),
        ScoreComponent(
            score_run_id=0,
            member_id=member.id,
            component="relative_size",
            value=size_value,
            details=size_details,
        ),
        ScoreComponent(
            score_run_id=0,
            member_id=member.id,
            component="track_record",
            value=track_value,
            details=track_details,
        ),
        ScoreComponent(
            score_run_id=0,
            member_id=member.id,
            component="composite",
            value=composite,
            label=label_for(composite, config),
            details=(
                f"= {holdings_value} + {trading_value} + {repeat_value}"
                f" + {size_value} + {track_value}"
            ),
        ),
    ]
    return rows


def run_scoring(
    session: Session,
    mappings: Mappings,
    config: ScoringConfig = DEFAULT_CONFIG,
    *,
    as_of: date | None = None,
    price_provider: Callable[[str], dict[date, float]] | None = None,
) -> ScoreRun:
    """Score every member and persist one ScoreRun with all component rows."""
    run = ScoreRun(
        run_at=datetime.now(),
        config_version=f"{CONFIG_VERSION} ({mappings.version})",
        parser_version=PARSER_VERSION_TAG,
    )
    session.add(run)
    session.flush()
    assert run.id is not None

    members = session.exec(select(Member)).all()
    for member in members:
        for row in score_member(
            session, member, mappings, config, as_of=as_of, price_provider=price_provider
        ):
            row.score_run_id = run.id
            session.add(row)
    return run
