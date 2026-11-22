"""News feed tab: big-news items for the filtered companies, latest first, undated last.

Titles and summaries are scraped or model-written, so they reach `st.markdown` only through
`ui.plain()` (D41). A news row has no confidence; its article URL is the source.
"""

from dataclasses import dataclass

import streamlit as st

from sprout import queries
from sprout.app import ui
from sprout.app.filters import Filters, filtered
from sprout.app.overview import DATE_NOT_FOUND

ANY_KIND = "Any kind"


@dataclass(frozen=True)
class FeedItem:
    """One news row as plain data, safe to cache."""

    company: str
    kind: str | None
    published: str | None
    title: str
    summary: str | None
    url: str


@st.cache_data(show_spinner=False)
def _load_feed(
    db_path: str, version: float, filters: Filters, include_not_big: bool
) -> tuple[FeedItem, ...]:
    ids = filtered(filters).company_ids
    if not ids:
        return ()
    with ui.session() as session:
        rows = queries.search_news(session, big_only=not include_not_big, company_ids=ids)
    return tuple(
        FeedItem(
            company=name,
            kind=row.news_kind,
            published=row.published.isoformat() if row.published else None,
            title=row.title,
            summary=row.summary,
            url=row.url,
        )
        for row, name in rows
    )


def _render_item(index: int, item: FeedItem) -> None:
    with st.container(border=True):
        st.markdown(f"**{ui.plain(item.company)}** · {item.published or DATE_NOT_FOUND}")
        if item.kind:
            st.badge(ui.plain(item.kind), color="blue")
        st.markdown(ui.plain(item.title))
        if item.summary:
            st.caption("Summary (model-written)")
            st.markdown(ui.plain(item.summary))
        ui.source_link(item.url, "Read article", key=f"feed_{index}")


def render_news_feed(filters: Filters) -> None:
    if not filtered(filters).company_ids:
        st.info("No companies match the filters, so there is no news. Widen them in the sidebar.")
        return
    db_path = ui.current_db_path()
    include_not_big = st.toggle("Include items that are not big news", value=False)
    items = _load_feed(db_path, ui.db_version(db_path), filters, include_not_big)
    kinds = sorted({i.kind for i in items if i.kind})
    kind = st.selectbox("News kind", [ANY_KIND, *kinds])
    shown = [i for i in items if kind == ANY_KIND or i.kind == kind]
    if not shown:
        st.info(
            "No news items for these filters. News is collected only for enriched companies "
            "and few items are big news, so the feed is short."
        )
        return
    noun = "items" if include_not_big else "big-news items"
    companies = len({i.company for i in shown})
    undated = sum(i.published is None for i in shown)
    st.caption(
        f"{len(shown)} {noun} across {companies} {'company' if companies == 1 else 'companies'}; "
        f"{undated} undated. "
        "Latest first, undated last. Each item links to its own article."
    )
    for index, item in enumerate(shown):
        _render_item(index, item)
