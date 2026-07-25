"""Congress Alpha dashboard (M5a).

Streamlit UI over app.intelligence.services — no business logic here: every
number, label, and decomposition string comes from the service layer. Theme
lives in .streamlit/config.toml (dark + neon, per the user's direction).

Run: uv run streamlit run app/dashboard/app.py
"""

import streamlit as st
from sqlmodel import Session

from app.core.config import get_settings
from app.db.session import get_engine
from app.intelligence.notes import add_note
from app.intelligence.services import (
    available_sectors,
    member_detail,
    sector_clusters,
    watchlist,
)

st.set_page_config(
    page_title="Congress Alpha",
    page_icon="🏛️",
    layout="wide",
)

_NEON_LABEL_COLORS = {
    "low": "#3A7D8C",
    "moderate": "#00E5FF",
    "elevated": "#B2FF59",
    "high": "#FF4081",
}


@st.cache_resource
def _engine():  # type: ignore[no-untyped-def]
    return get_engine(get_settings().db_url)


def _session() -> Session:
    return Session(_engine())


def _label_badge(label: str | None) -> str:
    if label is None:
        return "—"
    color = _NEON_LABEL_COLORS.get(label, "#E6F1F5")
    return f"<span style='color:{color}; font-weight:700'>{label.upper()}</span>"


def _watchlist_view() -> None:
    st.header("Watchlist — policy-edge ranking")
    st.caption(
        "Conservative committee-overlap signal. Not an accusation of wrongdoing; "
        "every score decomposes into named contributors."
    )
    with _session() as session:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            chamber_choice = st.selectbox("Chamber", ["all", "house", "senate"])
        with col2:
            party_choice = st.selectbox("Party", ["all", "Democrat", "Republican", "Independent"])
        with col3:
            label_choice = st.selectbox("Label", ["all", "low", "moderate", "elevated", "high"])
        with col4:
            sector_choice = st.selectbox("Overlap sector", ["all", *available_sectors()])

        rows = watchlist(
            session,
            chamber=None if chamber_choice == "all" else chamber_choice,  # type: ignore[arg-type]
            party=None if party_choice == "all" else party_choice,
            label=None if label_choice == "all" else label_choice,  # type: ignore[arg-type]
            sector=None if sector_choice == "all" else sector_choice,
        )
        st.metric("Members ranked", len(rows))
        for row in rows:
            with st.container(border=True):
                left, right = st.columns([3, 1])
                with left:
                    st.markdown(
                        f"**{row.member_name}** ({row.party}, {row.state_label}, "
                        f"{row.chamber.value}) — composite **{row.composite:.1f}** "
                        f"{_label_badge(row.label.value if row.label else None)}",
                        unsafe_allow_html=True,
                    )
                    if row.composite > 0 and row.top_details:
                        st.caption(f"{row.top_component}: {row.top_details}")
                with right:
                    if st.button("View", key=f"view-{row.member_id}"):
                        st.session_state["member_id"] = row.member_id
                        st.session_state["view"] = "Member detail"
                        st.rerun()


def _member_detail_view() -> None:
    with _session() as session:
        options = {row.member_name: row.member_id for row in watchlist(session, limit=10000)}
        if not options:
            st.warning("No score run found. Run `uv run python -m app.cli score` first.")
            return
        default_id = st.session_state.get("member_id")
        names = list(options)
        default_idx = (
            names.index(next(n for n, i in options.items() if i == default_id))
            if default_id in options.values()
            else 0
        )
        chosen = st.selectbox("Member", names, index=default_idx)
        detail = member_detail(session, options[chosen])
        if detail is None:
            st.error("Member not found.")
            return

        st.header(detail.name)
        st.caption(f"{detail.party} — {detail.state_label} — {detail.chamber.value}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Composite policy-edge", f"{detail.composite:.1f}")
        c2.markdown(f"**Label**<br/>{_label_badge(detail.label.value if detail.label else None)}",
                    unsafe_allow_html=True)
        c3.metric(
            "Net worth (est.)",
            detail.net_worth_label or "no annual FD parsed",
        )
        if detail.net_worth_certainty:
            st.caption(f"Net-worth certainty: **{detail.net_worth_certainty.value}**")

        st.subheader("Score breakdown")
        for component in detail.components:
            with st.container(border=True):
                st.markdown(f"**{component.component}** — {component.value:.1f}")
                st.caption(component.details)

        st.subheader("Estimated open positions")
        if detail.positions:
            st.dataframe(
                [
                    {
                        "asset": p.asset_name,
                        "ticker": p.ticker,
                        "value": p.value_label,
                        "certainty": p.certainty.value,
                        "method": p.method,
                        "as of": p.as_of.isoformat(),
                    }
                    for p in detail.positions
                ],
                use_container_width=True,
            )
        else:
            st.caption("No reconstructed positions.")

        st.subheader("Recent disclosed transactions")
        if detail.transactions:
            st.dataframe(
                [
                    {
                        "date": tx.transaction_date.isoformat() if tx.transaction_date else "",
                        "asset": tx.asset_name,
                        "ticker": tx.ticker,
                        "type": tx.kind,
                        "owner": tx.owner,
                        "amount": tx.amount_label,
                        "confidence": tx.parse_confidence,
                        "doc": tx.doc_id,
                    }
                    for tx in detail.transactions
                ],
                use_container_width=True,
            )
        else:
            st.caption("No parsed transactions.")

        st.subheader("Notes")
        for note in detail.notes:
            st.caption(f"[{note.created_at.date()}] {note.body}")
        with st.form("add_note"):
            body = st.text_area("Add a note", "")
            if st.form_submit_button("Save note") and body.strip():
                add_note(session, body, member_id=detail.member_id)
                session.commit()
                st.success("Note saved.")
                st.rerun()


def _sector_view() -> None:
    st.header("Sector clusters")
    st.caption("Members whose committee sectors overlap their holdings/trades.")
    with _session() as session:
        clusters = sector_clusters(session)
        if not clusters:
            st.warning("No overlaps found. Run `score` after ingesting and parsing data.")
            return
        for cluster in clusters:
            with st.container(border=True):
                st.markdown(
                    f"### <span style='color:#00E5FF'>{cluster.sector}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"{len(cluster.member_names)} member(s), "
                    f"{len(cluster.asset_names)} asset(s) — via "
                    f"{', '.join(cluster.committee_codes)}"
                )
                left, right = st.columns(2)
                left.markdown("**Members**")
                left.write(", ".join(cluster.member_names))
                right.markdown("**Assets**")
                right.write(", ".join(cluster.asset_names))


def main() -> None:
    st.sidebar.title("Congress Alpha")
    st.sidebar.caption("Local-first disclosure research")
    view = st.sidebar.radio(
        "View",
        ["Watchlist", "Member detail", "Sector clusters"],
        index=["Watchlist", "Member detail", "Sector clusters"].index(
            st.session_state.get("view", "Watchlist")
        ),
    )
    st.session_state["view"] = view
    if view == "Watchlist":
        _watchlist_view()
    elif view == "Member detail":
        _member_detail_view()
    else:
        _sector_view()


main()
