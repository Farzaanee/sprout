"""Overview tab: coverage strip, category counts, funding by round stage and recent rounds.

Everything is computed over the companies matching the sidebar filters. Model-written text (round
notes, investors) reaches the page only through `ui.plain()` or a dataframe cell (D41).
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from sprout import queries
from sprout.app import ui
from sprout.app.filters import Filters, filtered

_RECENT_LIMIT = 10
_ALL_ROUNDS = 100_000  # above any realistic round count; the table lists every round
DATE_NOT_FOUND = "Date not found"


@dataclass(frozen=True)
class RoundView:
    """A funding round as plain data (no ORM row), safe to cache."""

    company: str
    stage: str
    amount_million_usd: int | None
    announced: date | None
    investors: tuple[str, ...]
    source_url: str | None
    confidence: str
    note: str | None


@dataclass(frozen=True)
class OverviewData:
    matched: int
    stage_known: int
    funding_known: int
    categories: dict[str, int]
    funding: queries.FundingByStage
    rounds: tuple[RoundView, ...]  # newest announced first, undated last


@st.cache_data(show_spinner=False)
def _load(db_path: str, version: float, filters: Filters) -> OverviewData:
    result = filtered(filters)
    ids = list(result.company_ids)
    with ui.session() as session:
        cards = queries.company_cards(session, ids)
        funding = queries.funding_by_stage(session, ids)
        rounds = queries.recent_rounds(session, ids, limit=_ALL_ROUNDS)
    categories: dict[str, int] = {}
    for card in cards:
        if card.company.category:
            categories[card.company.category] = categories.get(card.company.category, 0) + 1
    return OverviewData(
        matched=len(ids),
        stage_known=result.coverage.stage_known,
        funding_known=result.coverage.funding_known,
        categories=dict(sorted(categories.items(), key=lambda item: -item[1])),
        funding=funding,
        rounds=tuple(
            RoundView(
                company=r.company_name,
                stage=r.round.stage,
                amount_million_usd=r.round.amount_million_usd,
                announced=r.round.announced,
                investors=tuple(r.round.investors),
                source_url=r.round.source_url,
                confidence=r.round.confidence,
                note=r.round.note,
            )
            for r in rounds
        ),
    )


def _http_url(url: str | None) -> str | None:
    return url if url and url.lower().startswith(("http://", "https://")) else None


def _announced(value: date | None) -> str:
    return value.isoformat() if value else DATE_NOT_FOUND


def _render_coverage(data: OverviewData) -> None:
    amounts = sum(stage.amount_known for stage in data.funding.stages)
    total = sum(stage.rounds for stage in data.funding.stages)
    for left, right in (
        (
            ("Companies matched", str(data.matched)),
            ("Stage known", f"{data.stage_known} of {data.matched}"),
        ),
        (
            ("Funding known", f"{data.funding_known} of {data.matched}"),
            ("Rounds with an amount", f"{amounts} of {total}"),
        ),
    ):
        for column, (label, value) in zip(st.columns(2), (left, right), strict=True):
            column.metric(label, value)


def _render_categories(data: OverviewData) -> None:
    st.subheader("Companies per category")
    st.caption(
        "Category is a model classification, not a sourced fact (D6). "
        "Long labels are truncated; hover a bar for the full name."
    )
    st.altair_chart(
        ui.bar_chart(
            data.categories,
            category_title="category",
            value_title="companies",
            horizontal=True,
            height=max(len(data.categories), 1) * 28,
        ),
        width="stretch",
    )


def _render_funding(data: OverviewData) -> None:
    funding = data.funding
    st.subheader("Funding by round stage")
    st.caption(
        f"Funding known for {data.funding_known} of {data.matched} companies. "
        f"{sum(s.amount_known for s in funding.stages)} of "
        f"{sum(s.rounds for s in funding.stages)} rounds have an amount; "
        "totals and medians leave the others out."
    )
    if not funding.stages:
        st.info("No funding rounds found for these companies.")
        return
    drawn = {
        s.stage: s.total_million_usd for s in funding.stages if s.total_million_usd is not None
    }
    chart_col, median_col = st.columns([3, 2])
    with chart_col:
        if drawn:
            st.altair_chart(
                ui.bar_chart(
                    drawn,
                    category_title="stage",
                    value_title="total US$m",
                    details={
                        s.stage: {"rounds": s.rounds, "amount_known": s.amount_known}
                        for s in funding.stages
                        if s.stage in drawn
                    },
                ),
                width="stretch",
            )
        else:
            st.info("No round has a disclosed amount, so there is nothing to chart.")
    with median_col:
        st.markdown("**Median round per stage**")
        for s in funding.stages:
            text = (
                f"{s.stage}: {ui.money(s.median_million_usd)} "
                f"({s.amount_known} of {s.rounds} with an amount)"
            )
            st.markdown(f"- {ui.plain(text)}")
    _render_rounds_table(data)


def _render_rounds_table(data: OverviewData) -> None:
    st.markdown("**Rounds behind this chart**")
    st.dataframe(
        pd.DataFrame(
            {
                "company": r.company,
                "stage": r.stage,
                "amount": ui.money(r.amount_million_usd),
                "announced": _announced(r.announced),
                "investors": ", ".join(r.investors) or "not listed",
                "source": _http_url(r.source_url),
                "confidence": r.confidence,
                "note": r.note,
            }
            for r in data.rounds
        ),
        column_config={"source": st.column_config.LinkColumn("source", display_text="Source")},
        hide_index=True,
        width="stretch",
    )


def _render_recent_rounds(data: OverviewData) -> None:
    st.subheader("Top recent rounds")
    st.caption("Newest announced first. Rounds with no date are listed last.")
    if not data.rounds:
        st.info("No funding rounds found for these companies.")
        return
    for index, r in enumerate(data.rounds[:_RECENT_LIMIT]):
        with st.container(border=True):
            amount = ui.money(r.amount_million_usd)
            st.markdown(
                f"**{ui.plain(r.company)}** · {ui.plain(r.stage)} · {amount} · "
                f"{_announced(r.announced)}"
            )
            investors = ", ".join(r.investors) or "not listed"
            st.markdown(f"Investors: {ui.plain(investors)}")
            link, badge = st.columns(2)
            with link:
                if not ui.source_link(r.source_url, key=f"recent_round_{index}"):
                    st.caption("No source link")
            with badge:
                ui.confidence_badge(r.confidence)


def render_overview(filters: Filters) -> None:
    db_path = ui.current_db_path()
    data = _load(db_path, ui.db_version(db_path), filters)
    if data.matched == 0:
        st.info("No companies match the filters. Widen or reset them in the sidebar.")
        return
    _render_coverage(data)
    _render_categories(data)
    _render_funding(data)
    _render_recent_rounds(data)
