"""Companies tab: the filtered list and a company detail (profile facts, funding, news, officers).

The list is cached plain data; the detail reads `queries.get_profile` live. Model-written or
scraped text (fact values, notes, investors, news titles and summaries, officer names) reaches
`st.markdown` only through `ui.plain()`, or a dataframe cell (D41). A NULL winning fact reads
"Not disclosed"; a field with no fact reads "Not researched".
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from sprout import queries
from sprout.app import ui
from sprout.app.filters import Filters, filtered
from sprout.app.overview import DATE_NOT_FOUND
from sprout.queries import NOT_DISCLOSED, ProfileFact

NOT_RESEARCHED = "Not researched"
# Fixed display order; the allowlist also keeps legacy `verification:*` facts off the page.
FIELDS = (
    ("one_liner", "One-liner"),
    ("business_model", "Business model"),
    ("target_user", "Target user"),
    ("hq", "Headquarters"),
    ("founded", "Founded"),
    ("stage", "Stage"),
    ("total_raised_million_usd", "Total raised"),
    ("valuation_million_usd", "Valuation"),
    ("business_status", "Business status"),
)
_MONEY_FIELDS = {"total_raised_million_usd", "valuation_million_usd"}
_NOTE_FIELDS = _MONEY_FIELDS  # the note records the original currency and conversion
_LIST_KEY = "companies_list"


@dataclass(frozen=True)
class CardRow:
    """One list row as display text (no ORM rows), safe to cache."""

    company_id: int
    name: str
    category: str
    one_liner: str
    stage: str
    latest_round: str
    founded: str


def _fact_text(fact: ProfileFact | None) -> str:
    return NOT_DISCLOSED if fact is None else str(fact.display_value)


@st.cache_data(show_spinner=False)
def _load_list(db_path: str, version: float, filters: Filters) -> tuple[CardRow, ...]:
    ids = list(filtered(filters).company_ids)
    with ui.session() as session:
        cards = queries.company_cards(session, ids)
    return tuple(
        CardRow(
            company_id=card.company.id,
            name=card.company.name,
            category=card.company.category or NOT_DISCLOSED,
            one_liner=_fact_text(card.one_liner),
            stage=_fact_text(card.stage),
            latest_round=ui.money(
                card.latest_round.amount_million_usd if card.latest_round else None
            ),
            founded=_fact_text(card.founded),
        )
        for card in cards
        if card.company.id is not None
    )


def _render_list(rows: tuple[CardRow, ...]) -> int | None:
    """The list; returns the selected company id, if any. The key includes the shown ids so a
    filter change clears the selection instead of pointing it at a different company."""
    key = f"{_LIST_KEY}_{hash(tuple(r.company_id for r in rows))}"
    event = st.dataframe(
        pd.DataFrame(
            {
                "name": r.name,
                "category": r.category,
                "one-liner": r.one_liner,
                "stage": r.stage,
                "latest round": r.latest_round,
                "founded": r.founded,
            }
            for r in rows
        ),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    selected = event.selection.rows
    return rows[selected[0]].company_id if selected and selected[0] < len(rows) else None


def _fact_block(field: str, label: str, fact: ProfileFact | None) -> None:
    with st.container(border=True):
        if fact is None:
            st.markdown(f"**{label}**")
            st.caption(NOT_RESEARCHED)
            return
        if not fact.disclosed:
            value = NOT_DISCLOSED
        elif field in _MONEY_FIELDS:
            value = ui.money(fact.value)
        else:
            value = str(fact.value)
        st.markdown(f"**{label}**")
        st.markdown(ui.plain(value))
        link, badge = st.columns(2)
        with link:
            ui.source_link(fact.source_url, key=f"fact_{field}")
            if fact.disclosed and not (fact.source_url or "").lower().startswith(
                ("http://", "https://")
            ):
                st.caption("No source link")
        with badge:
            ui.confidence_badge(fact.confidence)
        st.caption(f"Retrieved {fact.retrieved_at:%Y-%m-%d}")
        if fact.note and field in _NOTE_FIELDS:
            st.caption(f"Note: {ui.plain(fact.note)}")


def _render_header(profile: queries.Profile) -> None:
    company = profile.company
    st.subheader(company.name)
    st.caption(f"Category: {ui.plain(company.category or NOT_DISCLOSED)}")
    website = profile.facts.get("website")
    link, site = st.columns(2)
    with link:
        ui.source_link(
            queries.companies_house_url(company.company_number),
            "Companies House",
            key="header_companies_house",
        )
    with site:
        if website is not None and website.disclosed:
            ui.source_link(str(website.value), "Website", key="header_website")


def _render_funding(profile: queries.Profile) -> None:
    st.markdown("**Funding history**")
    if not profile.funding_rounds:
        st.info("No funding rounds found.")
        return
    rounds = sorted(profile.funding_rounds, key=lambda r: r.announced or date.min, reverse=True)
    for index, funding_round in enumerate(rounds):
        with st.container(border=True):
            announced = funding_round.announced.isoformat() if funding_round.announced else None
            amount = ui.plain(
                ui.money(funding_round.amount_million_usd)
            )  # `$` pairs render as math
            st.markdown(
                f"**{ui.plain(funding_round.stage)}** · {amount} · {announced or DATE_NOT_FOUND}"
            )
            investors = ", ".join(funding_round.investors) or "not listed"
            st.markdown(f"Investors: {ui.plain(investors)}")
            link, badge = st.columns(2)
            with link:
                if not ui.source_link(funding_round.source_url, key=f"round_{index}"):
                    st.caption("No source link")
            with badge:
                ui.confidence_badge(funding_round.confidence)
            if funding_round.note:
                st.caption(f"Note: {ui.plain(funding_round.note)}")


def _render_news(profile: queries.Profile) -> None:
    st.markdown("**News timeline**")
    if not profile.news:
        st.info("No news found.")
        return
    st.caption("Summaries are model-written. Each item links to its own article.")
    for index, item in enumerate(profile.news):
        with st.container(border=True):
            published = item.published.isoformat() if item.published else DATE_NOT_FOUND
            st.markdown(f"**{ui.plain(item.title)}**")
            st.caption(published)
            if item.news_kind:
                st.badge(ui.plain(item.news_kind), color="blue")
            if item.summary:
                st.markdown(ui.plain(item.summary))
            ui.source_link(item.url, "Read article", key=f"news_{index}")


def _render_officers(officers: list) -> None:
    st.markdown("**Officers**")
    if not officers:
        st.info("No officers recorded.")
        return
    st.dataframe(
        pd.DataFrame(
            {
                "role": o.role,
                "name": o.name,
                "born": str(o.birth_year) if o.birth_year else NOT_DISCLOSED,
                "nationality": o.nationality or NOT_DISCLOSED,
                "source": o.source_url
                if (o.source_url or "").lower().startswith(("http://", "https://"))
                else None,
            }
            for o in officers
        ),
        column_config={"source": st.column_config.LinkColumn("source", display_text="Source")},
        hide_index=True,
        width="stretch",
    )


def _render_detail(company_id: int) -> None:
    with ui.session() as session:
        profile = queries.get_profile(session, company_id)
        officers = queries.company_officers(session, company_id)
    if profile is None:
        st.warning("That company is no longer in the database.")
        return
    _render_header(profile)
    for field, label in FIELDS:
        _fact_block(field, label, profile.facts.get(field))
    _render_funding(profile)
    _render_news(profile)
    _render_officers(officers)


def render_companies(filters: Filters) -> None:
    db_path = ui.current_db_path()
    rows = _load_list(db_path, ui.db_version(db_path), filters)
    if not rows:
        st.info("No companies match the filters. Widen or reset them in the sidebar.")
        return
    st.caption(
        f"{len(rows)} companies. Select a row to open its profile and the sources behind "
        f'each value. Unknown values read "{NOT_DISCLOSED}".'
    )
    selected = _render_list(rows)
    if selected is None:
        st.caption("No company selected.")
        return
    _render_detail(selected)
