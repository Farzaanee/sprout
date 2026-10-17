"""Streamlit app to check Sprout's progress and try the pipeline.

Run locally: `uv run streamlit run streamlit_app.py`.

Tabs: Progress (phases + data counts), Companies, News, Run & test. The Run & test tab can write
to the database and spend API quota, so it is hidden unless `SPROUT_ENABLE_ACTIONS=1`.
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
            f"{STATE_ICONS[p.state]} **Phase {p.number} — {p.title}** "
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
    st.caption("UK AI startup discovery — progress and test console")

    names = ["Progress", "Companies", "News"]
    if actions_enabled():
        names.append("Run & test")
    tabs = st.tabs(names)
    renderers = [render_progress, render_companies, render_news, render_run_and_test]
    for tab, render in zip(tabs, renderers, strict=False):
        with tab:
            render()
    if not actions_enabled():
        st.sidebar.info("Read-only mode. Set SPROUT_ENABLE_ACTIONS=1 to enable the Run & test tab.")
