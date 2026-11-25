"""Sidebar filters shared by the Overview, Companies and News feed pages.

`Filters` is frozen and hashable so it can be part of a cache key. A filter left at its default is
`None` and is not passed to `queries.search_companies`.
"""

from dataclasses import dataclass
from datetime import date

import streamlit as st

from sprout import queries
from sprout.app import ui
from sprout.models import Stage

# Steps of the "Latest round" slider in US$m; the last step means "no upper bound".
FUNDING_STEPS = (0, 1, 5, 10, 25, 50, 100, 250, 500, 1000)
_SEARCH_LIMIT = 10_000  # above the pool size, so ids and coverage describe the same companies


@dataclass(frozen=True)
class Filters:
    category: str | None = None
    stage: str | None = None
    funding_min: int | None = None  # latest round, US$m
    funding_max: int | None = None
    founded_from: int | None = None
    founded_to: int | None = None
    text: str | None = None
    run_from: date | None = None  # window: first ingested or enriched on these days
    run_to: date | None = None


@dataclass(frozen=True)
class FilteredCompanies:
    """Plain, cacheable result of a filtered search (no ORM rows)."""

    company_ids: tuple[int, ...]
    coverage: queries.Coverage
    enriched_total: int


@dataclass(frozen=True)
class _Options:
    categories: dict[str, int]
    stages: list[str]
    founded_range: tuple[int, int] | None
    run_range: tuple[date, date] | None


@st.cache_data(show_spinner=False)
def _options(db_path: str, version: float) -> _Options:
    with ui.session() as session:
        categories = queries.category_counts(session)
        stage_values = set(queries.stage_counts(session)) - {queries.NOT_DISCLOSED}
        years = list(queries.founded_year_counts(session))
        run_range = queries.run_date_range(session)
    known = [s.value for s in Stage if s.value in stage_values]
    stages = known + sorted(stage_values - set(known))
    return _Options(categories, stages, (min(years), max(years)) if years else None, run_range)


@st.cache_data(show_spinner=False)
def _filtered(db_path: str, version: float, filters: Filters) -> FilteredCompanies:
    with ui.session() as session:
        search = queries.search_companies(
            session,
            enriched_only=True,
            category=filters.category,
            stage=filters.stage,
            min_funding_million_usd=filters.funding_min,
            max_funding_million_usd=filters.funding_max,
            founded_from=filters.founded_from,
            founded_to=filters.founded_to,
            run_from=filters.run_from,
            run_to=filters.run_to,
            text=filters.text or "",
            limit=_SEARCH_LIMIT,
        )
        return FilteredCompanies(
            company_ids=tuple(c.id for c in search.companies if c.id is not None),
            coverage=search.coverage,
            enriched_total=queries.enriched_company_count(session),
        )


def filtered(filters: Filters) -> FilteredCompanies:
    """Enriched companies matching `filters`, cached per database file version."""
    db_path = ui.current_db_path()
    return _filtered(db_path, ui.db_version(db_path), filters)


def _funding_label(step: int) -> str:
    return f"{step}+" if step == FUNDING_STEPS[-1] else str(step)


def render_sidebar() -> Filters:
    db_path = ui.current_db_path()
    options = _options(db_path, ui.db_version(db_path))
    with st.sidebar:
        st.subheader("Filters")
        st.caption("Apply to Overview, Companies, News feed and Candidate pool.")
        category = st.selectbox(
            "Category",
            [None, *options.categories],
            format_func=lambda c: "Any" if c is None else f"{c} ({options.categories[c]})",
        )
        stage = st.selectbox(
            "Stage", [None, *options.stages], format_func=lambda s: "Any" if s is None else s
        )
        funding_low, funding_high = st.select_slider(
            "Latest round (US$m)",
            options=FUNDING_STEPS,
            value=(FUNDING_STEPS[0], FUNDING_STEPS[-1]),
            format_func=_funding_label,
        )
        founded_from = founded_to = None
        if options.founded_range and options.founded_range[0] < options.founded_range[1]:
            first, last = options.founded_range
            year_low, year_high = st.slider("Founded", first, last, (first, last))
            founded_from = year_low if year_low > first else None
            founded_to = year_high if year_high < last else None
        run_from = run_to = None
        if options.run_range and options.run_range[0] < options.run_range[1]:
            first_day, last_day = options.run_range
            picked = st.date_input(
                "Run date",
                value=(first_day, last_day),
                min_value=first_day,
                max_value=last_day,
                help="Keep companies first ingested or enriched (by a run) in this date range.",
            )
            if isinstance(picked, tuple) and len(picked) == 2:
                run_from = picked[0] if picked[0] > first_day else None
                run_to = picked[1] if picked[1] < last_day else None
        text = st.text_input("Search name or one-liner").strip()

        filters = Filters(
            category=category,
            stage=stage,
            funding_min=funding_low if funding_low > FUNDING_STEPS[0] else None,
            funding_max=funding_high if funding_high < FUNDING_STEPS[-1] else None,
            founded_from=founded_from,
            founded_to=founded_to,
            text=text or None,
            run_from=run_from,
            run_to=run_to,
        )
        result = filtered(filters)
        coverage = result.coverage
        st.caption(
            f"{coverage.matched} of {result.enriched_total} enriched companies match. "
            f"Funding known for {coverage.funding_known} of {coverage.matched}, "
            f"stage known for {coverage.stage_known} of {coverage.matched}. "
            "A company with an unknown value never matches a filter on it."
        )
    return filters
