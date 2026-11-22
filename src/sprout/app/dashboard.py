"""Read-only Streamlit dashboard for Sprout's progress and data.

Run locally: `uv run streamlit run streamlit_app.py`.

Sidebar: database picker, then filters for the first three tabs. Tabs: Overview, Companies,
News feed, Candidate pool, Progress (phases + data counts), Observability.
"""

from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from sqlmodel import Session

from sprout import queries
from sprout.app import observability_queries as obs
from sprout.app import ui
from sprout.app.companies import render_companies
from sprout.app.filters import render_sidebar
from sprout.app.news_feed import render_news_feed
from sprout.app.overview import render_overview
from sprout.config import DEFAULT_EVAL_DB_PATH, get_settings
from sprout.db import RunKind

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
STATE_ICONS = {"Done": "✅", "In progress": "🟡", "Not started": "⬜"}


def _render_phases() -> None:
    plan_file, notes_file = DOCS_DIR / "project-plan.md", DOCS_DIR / "implementation-notes.md"
    if not (plan_file.exists() and notes_file.exists()):
        st.info("Phase progress needs the project docs (docs/), which are not available here.")
        return
    phases = obs.parse_phases(plan_file.read_text(), notes_file.read_text())

    done = sum(p.state == "Done" for p in phases)
    st.progress(done / len(phases) if phases else 0, text=f"{done} of {len(phases)} phases done")
    for p in phases:
        criteria = f" · acceptance {p.criteria_done}/{p.criteria_total}" if p.criteria_total else ""
        st.markdown(
            f"{STATE_ICONS[p.state]} **Phase {p.number} - {p.title}** "
            f"({p.hours}h) · {p.state}{criteria}"
        )
        if p.status_line:
            st.caption(p.status_line)


def render_progress() -> None:
    _render_phases()
    st.subheader("Data in the database")
    with ui.session() as session:
        counts = queries.count_rows(session)
        runs = obs.recent_runs(session)
        keywords = queries.keyword_counts(session)
        years = queries.incorporation_year_counts(session)
    cols = st.columns(4)
    cols[0].metric("Candidate companies", counts["companies"])
    cols[1].metric("Facts (with sources)", counts["facts"])
    cols[2].metric("News items", counts["news"])
    cols[3].metric("Logged runs", counts["runs"])
    if counts["companies"] == 0 and counts["news"] == 0:
        st.info("The database is empty. Run `uv run python -m sprout.ingest` to fill it.")
        return

    left, right = st.columns(2)
    left.caption("Candidates by matched keyword")
    left.altair_chart(
        ui.bar_chart(keywords, category_title="keyword", value_title="companies"), width="stretch"
    )
    right.caption("Candidates by incorporation year")
    right.altair_chart(
        ui.bar_chart(years, category_title="year", value_title="companies"), width="stretch"
    )

    st.subheader("Recent runs")
    st.dataframe(
        pd.DataFrame(
            {
                "id": r.id,
                "kind": r.kind,
                "status": r.status,
                "started": r.started_at,
                "finished": r.finished_at,
                "stats": str(r.stats),
                "error": r.error_message,
            }
            for r in runs
        ),
        hide_index=True,
        width="stretch",
    )


def render_pool() -> None:
    with ui.session() as session:
        total = queries.count_rows(session)["companies"]
        last_ingest = queries.latest_companies_ingest_run(session)
        enriched = queries.enriched_company_count(session)
        category_counts = queries.category_counts(session)
        stage_counts = queries.stage_counts(session)
        business_status_counts = queries.business_status_counts(session)
        valuation_counts = queries.valuation_bin_counts(session)
        pool_founded_counts = queries.incorporation_year_counts(session)
        enriched_founded_counts = queries.founded_year_counts(session)
        locality_counts = queries.locality_counts(session)
        officer_counts = queries.officer_count_distribution(session)
        officer_age_counts = queries.officer_age_distribution(session)
        officer_nationality_counts = queries.officer_nationality_counts(session)

    st.subheader("Pool overview")
    st.caption(
        "Candidate companies are UK Companies House entities that match an AI "
        "name/SIC-code keyword and pass the shell-company pre-screen.\n Enrichment is run "
        "manually, per company, so only a hand-picked subset is enriched so far."
    )
    if last_ingest is not None:
        st.caption(
            f"Last ingest run (#{last_ingest.id}, {last_ingest.started_at:%Y-%m-%d %H:%M}): "
            f"searched {last_ingest.stats['searched']:,} companies, "
            f"{total:,} candidate companies in the pool."
        )
    cols = st.columns([1, 1, 1, 1, 1])
    cols[0].metric("Candidate companies", total)
    cols[1].metric("Enriched (classified)", enriched)
    cols[2].metric("Enrichment rate", f"{100 * enriched / total:.1f}%" if total else "n/a")
    if total:
        pie_df = pd.DataFrame(
            [
                {"status": "Enriched", "companies": enriched},
                {"status": "Not yet enriched", "companies": total - enriched},
            ]
        )
        pie_df["percent"] = 100 * pie_df["companies"] / total
        pie = (
            alt.Chart(pie_df)
            .mark_arc(outerRadius=60)
            .encode(
                theta=alt.Theta("companies:Q"),
                color=alt.Color(
                    "status:N",
                    title=None,
                    scale=alt.Scale(
                        domain=["Enriched", "Not yet enriched"], range=["#4C78A8", "#B3B3B3"]
                    ),
                ),
                tooltip=[
                    alt.Tooltip("status:N"),
                    alt.Tooltip("companies:Q"),
                    alt.Tooltip("percent:Q", title="% of candidates", format=".1f"),
                ],
            )
            .properties(height=150)
        )
        cols[3].caption("Enriched vs. not yet enriched")
        cols[3].altair_chart(pie, width="stretch")

        officer_total = sum(officer_counts.values())
        officer_df = pd.DataFrame(
            {
                "officers": key,
                "companies": value,
                "percent": 100 * value / officer_total if officer_total else 0.0,
            }
            for key, value in officer_counts.items()
        )
        officer_pie = (
            alt.Chart(officer_df)
            .mark_arc(outerRadius=60)
            .encode(
                theta=alt.Theta("companies:Q"),
                color=alt.Color("officers:N", title=None, sort=list(officer_counts)),
                tooltip=[
                    alt.Tooltip("officers:N", title="officers"),
                    alt.Tooltip("companies:Q"),
                    alt.Tooltip("percent:Q", title="% of pool", format=".1f"),
                ],
            )
            .properties(height=150)
        )
        cols[4].caption("Active officer count (pool)")
        cols[4].altair_chart(officer_pie, width="stretch")

    if total:
        left, right = st.columns(2)
        # Same pixel height for both horizontal charts, sized to the taller one's row count, so
        # their x-axes end at the same vertical position instead of each fitting its own rows.
        row_count = max(len(category_counts), len(locality_counts), 1)
        shared_height = row_count * 28
        left.caption("Category distribution (classified candidates only, hover for % of enriched)")
        left.altair_chart(
            ui.bar_chart(
                category_counts,
                category_title="category",
                value_title="companies",
                percent_of=enriched,
                horizontal=True,
                full_labels=True,
                height=shared_height,
            ),
            width="stretch",
        )
        right.caption(
            "HQ locality distribution (top 12, enriched companies, hover for % of enriched)"
        )
        right.altair_chart(
            ui.bar_chart(
                locality_counts,
                category_title="locality",
                value_title="companies",
                percent_of=enriched,
                horizontal=True,
                full_labels=True,
                height=shared_height,
            ),
            width="stretch",
        )

        left, right = st.columns(2)
        left.caption("Founded year (Companies House incorporation year, all candidates)")
        left.altair_chart(
            ui.bar_chart(pool_founded_counts, category_title="year", value_title="companies"),
            width="stretch",
        )
        right.caption("Founded year (enriched companies only, hover for % of enriched)")
        right.altair_chart(
            ui.bar_chart(
                enriched_founded_counts,
                category_title="year",
                value_title="companies",
                percent_of=enriched,
            ),
            width="stretch",
        )

        left, right = st.columns(2)
        left.caption("Officer age distribution (all candidates, from Companies House birth year)")
        left.altair_chart(
            ui.bar_chart(officer_age_counts, category_title="age", value_title="officers"),
            width="stretch",
        )
        n = len(officer_nationality_counts)
        right.caption(f"Officer nationality (top {n}, all candidates)")
        right.altair_chart(
            ui.bar_chart(
                officer_nationality_counts,
                category_title="nationality",
                value_title="officers",
                horizontal=True,
                full_labels=True,
                # Altair's default vertical-chart height (300px), so it matches the age chart
                # on the left instead of sizing itself to its own (shorter) row count.
                height=300,
            ),
            width="stretch",
        )

    if enriched:
        left, right = st.columns(2)
        left.caption("Stage distribution (enriched companies, hover for % of enriched)")
        left.altair_chart(
            ui.bar_chart(
                stage_counts, category_title="stage", value_title="companies", percent_of=enriched
            ),
            width="stretch",
        )
        right.caption(
            "Valuation distribution, US$ million (enriched companies, hover for % of enriched)"
        )
        right.altair_chart(
            ui.bar_chart(
                valuation_counts,
                category_title="valuation",
                value_title="companies",
                percent_of=enriched,
            ),
            width="stretch",
        )

        left, right = st.columns(2)
        left.caption("Business status (enriched companies, hover for % of enriched)")
        left.altair_chart(
            ui.bar_chart(
                business_status_counts,
                category_title="business status",
                value_title="companies",
                percent_of=enriched,
            ),
            width="stretch",
        )
    else:
        st.info("No company has been enriched yet, so stage and valuation have nothing to show.")

    st.divider()

    with ui.session() as session:
        top = st.columns([3, 1, 2, 2, 2])
        text = top[0].text_input("Name contains")
        min_score = top[1].number_input("Min score", min_value=0, max_value=10, value=0)
        keyword = top[2].selectbox(
            "Keyword",
            [""] + list(queries.keyword_counts(session)),
            format_func=lambda k: k or "any",
        )
        year = top[3].number_input("Incorporated from", 2012, date.today().year, 2012)
        business_status = top[4].selectbox(
            "Business status",
            [""] + queries.business_status_options(),
            format_func=lambda s: s or "any",
        )
        rows = queries.search_companies(
            session,
            text=text,
            min_score=int(min_score),
            keyword=keyword or None,
            incorporated_from=date(int(year), 1, 1),
            business_status=business_status or None,
        ).companies
        st.caption(f"{len(rows)} companies (max 500 shown). Candidates, not verified startups.")
        st.dataframe(
            pd.DataFrame(
                {
                    "name": r.name,
                    "number": r.company_number,
                    "score": r.candidate_score,
                    "keywords": ", ".join(r.matched_keywords),
                    "incorporated": r.incorporated,
                    "locality": r.locality,
                    "SIC": ", ".join(r.sic_codes),
                    "source": queries.companies_house_url(r.company_number),
                }
                for r in rows
            ),
            hide_index=True,
            width="stretch",
            column_config={"source": st.column_config.LinkColumn("source", display_text="open")},
        )


def _run_row(run: obs.EnrichRun) -> dict[str, object]:
    """One table row; a run with no stats (still running or crashed) keeps blanks."""
    has_stats = bool(run.stats)
    return {
        "run": run.run_id,
        "started": run.started_at,
        "kind": run.kind,
        "company": run.company_name,
        "status": run.status,
        "stop": run.stop,
        "category": run.failure_category,
        "iterations": run.iterations if has_stats else None,
        "cost (US$)": run.cost_usd if has_stats else None,
        "searches": run.web_searches if has_stats else None,
        "tools": ", ".join(run.tools) if run.tools else None,
        "steps logged": run.steps_logged,
    }


def _render_run_detail(session: Session, run: obs.EnrichRun) -> None:
    # Logged text (response_text, errors) is untrusted: st.code and dataframes only, never markdown.
    st.dataframe(pd.DataFrame([_run_row(run)]), hide_index=True, width="stretch")
    steps = obs.run_steps(session, run.run_id)
    if steps:
        st.dataframe(
            pd.DataFrame(
                {
                    "step": s.iteration,
                    "time": s.created_at,
                    "stop reason": s.stop_reason,
                    "tools": s.tool_name,
                    "tool errors": s.tool_errors,
                    "input": s.input_tokens,
                    "output": s.output_tokens,
                    "cache read": s.cache_read_tokens,
                    "cache write": s.cache_write_tokens,
                    "searches": s.web_searches,
                    "cost (US$)": s.cost_usd,
                }
                for s in steps
            ),
            hide_index=True,
            width="stretch",
        )
        for s in steps:
            if s.response_text:
                with st.expander(f"Step {s.iteration} response"):
                    st.code(s.response_text, language=None, wrap_lines=True)
            if s.tool_error_text:
                with st.expander(f"Step {s.iteration} tool error"):
                    st.code(s.tool_error_text, language=None, wrap_lines=True)
    elif run.iterations > 0:
        st.info("This run predates step logging (Phase 3), run-level stats only.")
    else:
        st.info("No model response was received.")
    if run.error_message:
        st.caption("Error")
        st.code(run.error_message, language=None, wrap_lines=True)


def render_observability() -> None:
    with ui.session() as session:
        if not obs.enrich_runs(session):
            st.info(
                "No enrichment runs yet. Run `uv run python -m sprout.enrich NAME_OR_NUMBER` "
                "to create one."
            )
            return

        date_bounds = obs.run_date_range(session)
        start_date, end_date = date_bounds if date_bounds else (None, None)
        if date_bounds:
            min_date, max_date = date_bounds
            selected = st.date_input(
                "Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date
            )
            if isinstance(selected, tuple) and len(selected) == 2:
                start_date, end_date = selected
            elif isinstance(selected, date):
                start_date = end_date = selected

        date_kwargs = {"start": start_date, "end": end_date}
        runs = obs.enrich_runs(session, **date_kwargs)
        counts = obs.failure_counts(session, **date_kwargs)
        rate_by_kind = obs.failure_rate_by_kind(session, **date_kwargs)
        tool_failures = obs.failures_by_tool(session, **date_kwargs)
        cost_tool = obs.cost_by_tool(session, **date_kwargs)
        calls_tool = obs.calls_by_tool(session, **date_kwargs)
        cost_kind = obs.cost_by_kind(session, **date_kwargs)
        trend = obs.cost_trend(session, **date_kwargs)
        days = obs.daily_outcomes(session, **date_kwargs)

        if not runs:
            st.info("No runs in this date range.")
            return

        failed = sum(r.failure_category is not None for r in runs)
        total_cost = sum(r.cost_usd for r in runs)
        total_iterations = sum(r.iterations for r in runs)
        cols = st.columns(4)
        cols[0].metric("Runs", len(runs))
        cols[1].metric("Failed", failed)
        cols[2].metric("Total cost (US$)", f"{total_cost:.2f}")
        cols[3].metric(
            "Mean cost per iteration (US$)",
            f"{total_cost / total_iterations:.4f}" if total_iterations else "n/a",
        )

        st.subheader("Cost")
        cost_day_col, cost_kind_col, cost_tool_col = st.columns(3)
        cost_day_col.caption("Cost per day (US$)")
        cost_day_df = pd.DataFrame({"day": d.day, "cost": d.cost_usd} for d in days)
        cost_day_col.altair_chart(
            alt.Chart(cost_day_df)
            .mark_bar()
            .properties(height=300)
            .encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(format="%Y-%m-%d", labelAngle=0)),
                y=alt.Y("cost:Q", title="cost (US$)"),
                tooltip=[
                    alt.Tooltip("day:T", title="day", format="%Y-%m-%d"),
                    alt.Tooltip("cost:Q", title="cost (US$)", format=".4f"),
                ],
            ),
            width="stretch",
        )

        cost_kind_col.caption("Cost by kind (US$)")
        cost_kind_col.altair_chart(
            ui.bar_chart(
                cost_kind,
                category_title="kind",
                value_title="cost (US$)",
                height=300,
                label_angle=0,
            ),
            width="stretch",
        )

        cost_tool_col.caption("Cost by tool call (US$)")
        if cost_tool:
            cost_tool_df = pd.DataFrame(
                {
                    "tool": tool,
                    "cost": cost,
                    "calls": calls_tool.get(tool, 0),
                    "cost_per_call": cost / calls_tool[tool] if calls_tool.get(tool) else 0.0,
                }
                for tool, cost in cost_tool.items()
            )
            cost_tool_col.altair_chart(
                alt.Chart(cost_tool_df)
                .mark_bar()
                .properties(height=300)
                .encode(
                    x=alt.X("tool:N", title=None, sort="-y", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("cost:Q", title="cost (US$)"),
                    tooltip=[
                        alt.Tooltip("tool:N", title="tool"),
                        alt.Tooltip("cost:Q", title="cost (US$)", format=".4f"),
                        alt.Tooltip("calls:Q", title="calls"),
                        alt.Tooltip("cost_per_call:Q", title="cost per call (US$)", format=".4f"),
                    ],
                ),
                width="stretch",
            )
        else:
            cost_tool_col.success("No tool calls in this range")

        st.subheader("Failures")
        cat_col, kind_col, tool_col = st.columns(3)
        cat_col.caption("Failures by category")
        if counts:
            cat_col.altair_chart(
                ui.bar_chart(
                    counts,
                    category_title="category",
                    value_title="runs",
                    height=300,
                    label_angle=0,
                ),
                width="stretch",
            )
        else:
            cat_col.success("No failed runs")

        kind_col.caption("Failure rate by kind (%)")
        kind_col.altair_chart(
            ui.bar_chart(
                rate_by_kind,
                category_title="kind",
                value_title="failure rate (%)",
                height=300,
                label_angle=0,
            ),
            width="stretch",
        )

        tool_col.caption("Failures by tool call")
        if tool_failures:
            tool_col.altair_chart(
                ui.bar_chart(
                    tool_failures,
                    category_title="tool",
                    value_title="failed runs",
                    height=300,
                    label_angle=0,
                ),
                width="stretch",
            )
        else:
            tool_col.success("No failed runs with tool calls")

        st.subheader("Trends")
        left, right = st.columns(2)
        left.caption("Cost per iteration (US$), per run")
        cost_trend_df = pd.DataFrame(
            {
                "run": p.run_id,
                "cost per iteration": p.cost_per_iteration,
                "status": "failed" if p.failed else "successful",
            }
            for p in trend
        )
        left.altair_chart(
            alt.Chart(cost_trend_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("run:O", title="run"),
                y=alt.Y("cost per iteration:Q", title="cost per iteration (US$)"),
                color=alt.Color(
                    "status:N",
                    title="status",
                    scale=alt.Scale(domain=["successful", "failed"], range=["#1f77b4", "#d62728"]),
                ),
                tooltip=["run", "cost per iteration", "status"],
            ),
            width="stretch",
        )
        right.caption("Failed runs per day (%)")
        failure_rate = pd.DataFrame(
            {
                "day": d.day,
                "failure_rate": 100 * d.failures / d.runs if d.runs else 0.0,
                "failed_runs": d.failures,
                "total_runs": d.runs,
            }
            for d in days
        )
        right.altair_chart(
            alt.Chart(failure_rate)
            .mark_bar()
            .encode(
                x=alt.X("day:T", title="day", axis=alt.Axis(format="%Y-%m-%d")),
                y=alt.Y("failure_rate:Q", title="failure rate (%)"),
                tooltip=[
                    alt.Tooltip("day:T", title="day", format="%Y-%m-%d"),
                    alt.Tooltip("failure_rate:Q", title="failure rate (%)", format=".1f"),
                    alt.Tooltip("failed_runs:Q", title="failed runs"),
                    alt.Tooltip("total_runs:Q", title="total runs"),
                ],
            ),
            width="stretch",
        )

        st.subheader("Runs")
        filters = st.columns([2, 2, 2, 2, 1])
        category = filters[0].selectbox(
            "Failure category", [""] + list(counts), format_func=lambda c: c or "any"
        )
        companies = sorted(
            {(r.company_id, r.company_name) for r in runs if r.company_id is not None},
            key=lambda c: c[1] or "",
        )
        company = filters[1].selectbox(
            "Company", [None] + companies, format_func=lambda c: "any" if c is None else c[1]
        )
        kind_options = [RunKind.COMPANY_ENRICH.value]
        kind_options += [k for k in sorted(rate_by_kind) if k not in kind_options]
        kind = filters[2].selectbox("Kind", [*kind_options, ""], format_func=lambda k: k or "any")
        tool = filters[3].selectbox(
            "Tool call", [""] + obs.all_tools(session), format_func=lambda t: t or "any"
        )
        failures_only = filters[4].toggle("Failures only", value=True)
        shown = obs.enrich_runs(
            session,
            failure_category=category or None,
            failures_only=failures_only,
            company_id=company[0] if company else None,
            kind=kind or None,
            tool=tool or None,
            **date_kwargs,
        )
        st.caption(f"{len(shown)} runs")
        if not shown:
            return
        st.dataframe(pd.DataFrame(_run_row(r) for r in shown), hide_index=True, width="stretch")

        st.subheader("Run detail")
        by_id = {r.run_id: r for r in shown}
        chosen = st.selectbox(
            "Run",
            list(by_id),
            format_func=lambda i: f"#{i} {by_id[i].started_at:%Y-%m-%d %H:%M} ({by_id[i].status})",
        )
        _render_run_detail(session, by_id[chosen])


def render_db_picker() -> None:
    default_db = str(get_settings().db_path)
    options = {default_db: "Main database"}
    if Path(DEFAULT_EVAL_DB_PATH).exists():
        options[DEFAULT_EVAL_DB_PATH] = "Eval database"
    choice = st.selectbox("Database", list(options), format_func=lambda p: options[p])
    st.session_state["db_path"] = choice


def main() -> None:
    st.set_page_config(page_title="Sprout", page_icon="🌱", layout="wide")
    st.title("🌱 Sprout")
    st.caption("UK AI startup discovery: progress and data dashboard")

    with st.sidebar:
        render_db_picker()
    filters = render_sidebar()

    tabs = [
        ("Overview", lambda: render_overview(filters)),
        ("Companies", lambda: render_companies(filters)),
        ("News feed", lambda: render_news_feed(filters)),
        ("Candidate pool", render_pool),
        ("Progress", render_progress),
        ("Observability", render_observability),
    ]
    for tab, (_, render) in zip(st.tabs([name for name, _ in tabs]), tabs, strict=True):
        with tab:
            render()
