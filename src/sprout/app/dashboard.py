"""Streamlit app to check Sprout's progress and try the pipeline.

Run locally: `uv run streamlit run streamlit_app.py`.

Tabs: Progress (phases + data counts), Companies, News, Observability, Run & test. The Run & test
tab can write to the database and spend API quota, so it is hidden unless `SPROUT_ENABLE_ACTIONS=1`.
"""

import contextlib
import io
import os
from datetime import date
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from sqlmodel import Session

from sprout import hello, ingest
from sprout.app import queries
from sprout.config import get_settings
from sprout.db import RunKind, get_engine, init_db

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
STATE_ICONS = {"Done": "✅", "In progress": "🟡", "Not started": "⬜"}


def _bar_chart(
    counts: dict[Any, float],
    *,
    category_title: str,
    value_title: str,
    percent_of: float | None = None,
    horizontal: bool = False,
    full_labels: bool = False,
    height: int | None = None,
) -> alt.Chart:
    """A static bar chart: unlike `st.bar_chart`, this never calls Altair's `.interactive()`, so
    the mouse wheel does not pan or zoom it. `percent_of` adds a "% of total" tooltip field;
    `horizontal` puts the category on the y-axis (for long labels); `full_labels` never truncates
    the category axis labels (Vega-Lite's default limit is 180px); `height` fixes the plot height
    in pixels, so two horizontal charts with a different row count still end at the same y
    position instead of each sizing itself to its own row count."""
    rows = []
    for key, value in counts.items():
        row = {category_title: key, value_title: value}
        if percent_of:
            row["percent"] = 100 * value / percent_of
        rows.append(row)
    tooltip = [alt.Tooltip(category_title), alt.Tooltip(value_title)]
    if percent_of:
        tooltip.append(alt.Tooltip("percent:Q", title="% of enriched companies", format=".1f"))
    axis = alt.Axis(labelLimit=0) if full_labels else alt.Axis()
    if rows:
        columns = [category_title, value_title] + (["percent"] if percent_of else [])
        df = pd.DataFrame(rows, columns=columns)
    else:
        # An empty object-dtype column infers as pandas dtype "empty", which Altair doesn't
        # recognise and warns about; give explicit dtypes so an empty chart renders quietly.
        df = pd.DataFrame(
            {category_title: pd.Series(dtype="str"), value_title: pd.Series(dtype="float")}
        )
        if percent_of:
            df["percent"] = pd.Series(dtype="float")
    chart = alt.Chart(df).mark_bar()
    if height is not None:
        chart = chart.properties(height=height)
    if horizontal:
        return chart.encode(
            y=alt.Y(f"{category_title}:N", sort="-x", title=None, axis=axis),
            x=alt.X(f"{value_title}:Q", title=value_title),
            tooltip=tooltip,
        )
    return chart.encode(
        x=alt.X(f"{category_title}:N", sort=None, title=None, axis=axis),
        y=alt.Y(f"{value_title}:Q", title=value_title),
        tooltip=tooltip,
    )


def actions_enabled() -> bool:
    return os.environ.get("SPROUT_ENABLE_ACTIONS") == "1"


@st.cache_resource
def _engine(db_path: str):
    engine = get_engine(db_path)
    init_db(engine)
    return engine


def _session() -> Session:
    return Session(_engine(str(get_settings().db_path)))


def _run_captured(func, *args) -> tuple[int, str]:
    """Run a CLI-style `main` and return (exit code, everything it printed)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        try:
            code = func(*args)
        except Exception as exc:  # shown to the tester, not swallowed
            print(f"{type(exc).__name__}: {exc}")
            code = 1
    return code or 0, buffer.getvalue()


def render_progress() -> None:
    plan = (DOCS_DIR / "project-plan.md").read_text()
    notes = (DOCS_DIR / "implementation-notes.md").read_text()
    phases = queries.parse_phases(plan, notes)

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

    st.subheader("Data in the database")
    with _session() as session:
        counts = queries.count_rows(session)
        runs = queries.recent_runs(session)
        keywords = queries.keyword_counts(session)
        years = queries.incorporation_year_counts(session)
    cols = st.columns(4)
    cols[0].metric("Candidate companies", counts["companies"])
    cols[1].metric("Facts (with sources)", counts["facts"])
    cols[2].metric("News items", counts["news_items"])
    cols[3].metric("Logged runs", counts["runs"])
    if counts["companies"] == 0 and counts["news_items"] == 0:
        st.info(
            "The database is empty. Use the Run & test tab (or `uv run python -m sprout.ingest`)."
        )
        return

    left, right = st.columns(2)
    left.caption("Candidates by matched keyword")
    left.altair_chart(
        _bar_chart(keywords, category_title="keyword", value_title="companies"), width="stretch"
    )
    right.caption("Candidates by incorporation year")
    right.altair_chart(
        _bar_chart(years, category_title="year", value_title="companies"), width="stretch"
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


def render_companies() -> None:
    with _session() as session:
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
            _bar_chart(
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
            _bar_chart(
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
            _bar_chart(pool_founded_counts, category_title="year", value_title="companies"),
            width="stretch",
        )
        right.caption("Founded year (enriched companies only, hover for % of enriched)")
        right.altair_chart(
            _bar_chart(
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
            _bar_chart(officer_age_counts, category_title="age", value_title="officers"),
            width="stretch",
        )
        n = len(officer_nationality_counts)
        right.caption(f"Officer nationality (top {n}, all candidates)")
        right.altair_chart(
            _bar_chart(
                officer_nationality_counts,
                category_title="nationality",
                value_title="officers",
                horizontal=True,
                full_labels=True,
            ),
            width="stretch",
        )

    if enriched:
        left, right = st.columns(2)
        left.caption("Stage distribution (enriched companies, hover for % of enriched)")
        left.altair_chart(
            _bar_chart(
                stage_counts, category_title="stage", value_title="companies", percent_of=enriched
            ),
            width="stretch",
        )
        right.caption(
            "Valuation distribution, US$ million (enriched companies, hover for % of enriched)"
        )
        right.altair_chart(
            _bar_chart(
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
            _bar_chart(
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

    with _session() as session:
        top = st.columns([3, 1, 2, 2, 2])
        text = top[0].text_input("Name contains")
        min_score = top[1].number_input("Min score", min_value=0, max_value=10, value=0)
        keyword = top[2].selectbox(
            "Keyword",
            [""] + list(queries.keyword_counts(session)),
            format_func=lambda k: k or "any",
        )
        year = top[3].number_input("Incorporated from", 2012, date.today().year, 2012)
        status = top[4].selectbox(
            "Business status",
            [""] + queries.status_options(session),
            format_func=lambda s: s or "any",
        )
        rows = queries.search_companies(
            session,
            text=text,
            min_score=int(min_score),
            keyword=keyword or None,
            incorporated_from=date(int(year), 1, 1),
            status=status or None,
        )
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

        if not rows:
            return
        chosen = st.selectbox("Company detail", rows, format_func=lambda r: r.name)
        assert chosen.id is not None
        facts = queries.company_facts(session, chosen.id)
        st.dataframe(
            pd.DataFrame(
                {
                    "field": f.field,
                    "value": f.value_json if f.value_json is not None else "not disclosed",
                    "confidence": f.confidence,
                    "retrieved": f.retrieved_at,
                    "note": f.note,
                    "source": f.source_url,
                }
                for f in facts
            ),
            hide_index=True,
            width="stretch",
            column_config={"source": st.column_config.LinkColumn("source", display_text="open")},
        )

        rounds = queries.company_funding_rounds(session, chosen.id)
        st.caption("Funding rounds")
        if not rounds:
            st.caption("No funding rounds found.")
        else:
            st.dataframe(
                pd.DataFrame(
                    {
                        "stage": r.stage,
                        "amount (US$m)": r.amount_million_usd,
                        "announced": r.announced,
                        "investors": ", ".join(r.investors),
                        "confidence": r.confidence,
                        "note": r.note,
                        "source": r.source_url,
                    }
                    for r in rounds
                ),
                hide_index=True,
                width="stretch",
                column_config={
                    "source": st.column_config.LinkColumn("source", display_text="open")
                },
            )

        officers = queries.company_officers(session, chosen.id)
        st.caption("Officers")
        if not officers:
            st.caption("No officers found.")
        else:
            st.dataframe(
                pd.DataFrame(
                    {
                        "name": o.name,
                        "role": o.role,
                        "birth year": o.birth_year,
                        "nationality": o.nationality,
                        "confidence": o.confidence,
                        "note": o.note,
                        "source": o.source_url,
                    }
                    for o in officers
                ),
                hide_index=True,
                width="stretch",
                column_config={
                    "source": st.column_config.LinkColumn("source", display_text="open")
                },
            )


def render_news() -> None:
    text = st.text_input("Search title or summary")
    with _session() as session:
        items = queries.search_news(session, text=text)
    st.caption(f"{len(items)} items")
    for item in items:
        st.markdown(f"**[{item.title}]({item.url})**")
        st.caption(f"{item.source or 'unknown source'} · {item.published or 'no date'}")
        if item.summary:
            st.write(item.summary)


def _run_row(run: queries.EnrichRun) -> dict[str, object]:
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


def _render_run_detail(session: Session, run: queries.EnrichRun) -> None:
    # Logged text (response_text, errors) is untrusted: st.code and dataframes only, never markdown.
    st.dataframe(pd.DataFrame([_run_row(run)]), hide_index=True, width="stretch")
    steps = queries.run_steps(session, run.run_id)
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
    elif run.iterations > 0:
        st.info("This run predates step logging (Phase 3), run-level stats only.")
    else:
        st.info("No model response was received.")
    if run.error_message:
        st.caption("Error")
        st.code(run.error_message, language=None, wrap_lines=True)


def render_observability() -> None:
    with _session() as session:
        if not queries.enrich_runs(session):
            st.info(
                "No enrichment runs yet. Run `uv run python -m sprout.enrich NAME_OR_NUMBER` "
                "to create one."
            )
            return

        date_bounds = queries.run_date_range(session)
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
        runs = queries.enrich_runs(session, **date_kwargs)
        counts = queries.failure_counts(session, **date_kwargs)
        rate_by_kind = queries.failure_rate_by_kind(session, **date_kwargs)
        tool_failures = queries.failures_by_tool(session, **date_kwargs)
        cost_tool = queries.cost_by_tool(session, **date_kwargs)
        calls_tool = queries.calls_by_tool(session, **date_kwargs)
        cost_kind = queries.cost_by_kind(session, **date_kwargs)
        trend = queries.cost_trend(session, **date_kwargs)
        days = queries.daily_outcomes(session, **date_kwargs)

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
            .encode(
                x=alt.X("day:T", title="day", axis=alt.Axis(format="%Y-%m-%d")),
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
            _bar_chart(cost_kind, category_title="kind", value_title="cost (US$)"),
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
                .encode(
                    x=alt.X("tool:N", title="tool", sort="-y"),
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
                _bar_chart(counts, category_title="category", value_title="runs"),
                width="stretch",
            )
        else:
            cat_col.success("No failed runs")

        kind_col.caption("Failure rate by kind (%)")
        kind_col.altair_chart(
            _bar_chart(rate_by_kind, category_title="kind", value_title="failure rate (%)"),
            width="stretch",
        )

        tool_col.caption("Failures by tool call")
        if tool_failures:
            tool_col.altair_chart(
                _bar_chart(tool_failures, category_title="tool", value_title="failed runs"),
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
                    scale=alt.Scale(
                        domain=["successful", "failed"], range=["#1f77b4", "#d62728"]
                    ),
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
            "Tool call", [""] + queries.all_tools(session), format_func=lambda t: t or "any"
        )
        failures_only = filters[4].toggle("Failures only", value=True)
        shown = queries.enrich_runs(
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


def render_run_and_test() -> None:
    settings = get_settings()
    st.write("Keys configured (values are never shown):")
    st.markdown(
        f"- Anthropic: {'✅' if settings.anthropic_api_key else '❌'}\n"
        f"- Companies House: {'✅' if settings.companies_house_api_key else '❌'}\n"
        f"- Database: `{settings.db_path}`"
    )

    st.subheader("Ingestion")
    st.caption("Safe to repeat: no duplicates, and cached URLs are not fetched again.")
    max_per_term = st.number_input("Companies House results per search term", 10, 1000, 50)
    cols = st.columns(3)
    jobs = {
        "News only": ["--skip-companies"],
        "Companies only": ["--skip-news", "--max-per-term", str(int(max_per_term))],
        "Both": ["--max-per-term", str(int(max_per_term))],
    }
    for col, (label, argv) in zip(cols, jobs.items(), strict=True):
        if col.button(label, width="stretch"):
            with st.spinner(f"Running ingestion: {label.lower()}…"):
                code, output = _run_captured(ingest.main, argv)
            (st.success if code == 0 else st.error)(f"exit code {code}")
            st.code(output)

    st.subheader("Claude connectivity")
    if st.button("Send one tiny 'hello' request (costs a fraction of a penny)"):
        code, output = _run_captured(hello.main)
        (st.success if code == 0 else st.error)(f"exit code {code}")
        st.code(output)


def main() -> None:
    st.set_page_config(page_title="Sprout", page_icon="🌱", layout="wide")
    st.title("🌱 Sprout")
    st.caption("UK AI startup discovery: progress and test console")

    tabs = [
        ("Progress", render_progress),
        ("Companies", render_companies),
        ("News", render_news),
        ("Observability", render_observability),
    ]
    if actions_enabled():
        tabs.append(("Run & test", render_run_and_test))
    for tab, (_, render) in zip(st.tabs([name for name, _ in tabs]), tabs, strict=True):
        with tab:
            render()
