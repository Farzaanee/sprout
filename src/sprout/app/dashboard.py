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

import pandas as pd
import streamlit as st
from sqlmodel import Session

from sprout import hello, ingest
from sprout.app import queries
from sprout.config import get_settings
from sprout.db import get_engine, init_db

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
STATE_ICONS = {"Done": "✅", "In progress": "🟡", "Not started": "⬜"}


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
    left.bar_chart(pd.Series(keywords, name="companies"))
    right.caption("Candidates by incorporation year")
    right.bar_chart(pd.Series(years, name="companies"))

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
                "error": r.error,
            }
            for r in runs
        ),
        hide_index=True,
        width="stretch",
    )


def render_companies() -> None:
    with _session() as session:
        top = st.columns([3, 1, 2, 2])
        text = top[0].text_input("Name contains")
        min_score = top[1].number_input("Min score", min_value=0, max_value=10, value=0)
        keyword = top[2].selectbox(
            "Keyword",
            [""] + list(queries.keyword_counts(session)),
            format_func=lambda k: k or "any",
        )
        year = top[3].number_input("Incorporated from", 2012, date.today().year, 2012)
        rows = queries.search_companies(
            session,
            text=text,
            min_score=int(min_score),
            keyword=keyword or None,
            incorporated_from=date(int(year), 1, 1),
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
        "company": run.company_name,
        "status": run.status,
        "stop": run.stop,
        "category": run.failure_category,
        "error type": run.error_type,
        "iterations": run.iterations if has_stats else None,
        "cost (US$)": run.cost_usd if has_stats else None,
        "searches": run.web_searches if has_stats else None,
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
    for label, text in (("Run error", run.error), ("Stats error", run.stats.get("error"))):
        if isinstance(text, str) and text and (label == "Run error" or text != run.error):
            st.caption(label)
            st.code(text, language=None, wrap_lines=True)


def render_observability() -> None:
    with _session() as session:
        runs = queries.enrich_runs(session)
        if not runs:
            st.info(
                "No enrichment runs yet. Run `uv run python -m sprout.enrich NAME_OR_NUMBER` "
                "to create one."
            )
            return
        counts = queries.failure_counts(session)
        trend = queries.cost_trend(session)
        days = queries.daily_outcomes(session)

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

        st.subheader("Failures by category")
        if counts:
            st.bar_chart(pd.Series(counts, name="runs"))
            st.dataframe(
                pd.DataFrame({"category": list(counts), "runs": list(counts.values())}),
                hide_index=True,
                width="stretch",
            )
        else:
            st.success("No failed runs")

        st.subheader("Trends")
        left, right = st.columns(2)
        left.caption("Cost per iteration (US$), per run")
        left.line_chart(
            pd.DataFrame(
                {"run": p.run_id, "cost per iteration": p.cost_per_iteration} for p in trend
            ),
            x="run",
            y="cost per iteration",
        )
        right.caption("Failed runs per day")
        right.bar_chart(
            pd.DataFrame({"day": d.day, "failures": d.failures} for d in days),
            x="day",
            y="failures",
        )

        st.subheader("Runs")
        filters = st.columns([2, 1])
        category = filters[0].selectbox(
            "Failure category", [""] + list(counts), format_func=lambda c: c or "any"
        )
        failures_only = filters[1].toggle("Failures only")
        shown = queries.enrich_runs(
            session, failure_category=category or None, failures_only=failures_only
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
