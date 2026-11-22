"""Shared dashboard plumbing: database access, a static bar chart and safe-rendering helpers.

Model-written or scraped text reaches `st.markdown` only through `plain()` (D41), and a link is
rendered only for an http(s) URL.
"""

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import Engine, create_engine
from sqlmodel import Session

from sprout.config import get_settings
from sprout.db import get_engine, init_db
from sprout.queries import NOT_DISCLOSED

_MARKDOWN_PUNCTUATION = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$&:])")
_CONFIDENCE_COLORS = {"high": "green", "medium": "orange", "low": "red"}


def bar_chart(
    counts: dict[Any, float],
    *,
    category_title: str,
    value_title: str,
    percent_of: float | None = None,
    horizontal: bool = False,
    full_labels: bool = False,
    height: int | None = None,
    label_angle: int | None = None,
    details: dict[Any, dict[str, Any]] | None = None,
) -> alt.Chart:
    """A static bar chart: unlike `st.bar_chart`, this never calls Altair's `.interactive()`, so
    the mouse wheel does not pan or zoom it. `percent_of` adds a "% of total" tooltip field;
    `horizontal` puts the category on the y-axis (for long labels); `full_labels` never truncates
    the category axis labels (Vega-Lite's default limit is 180px); `height` fixes the plot height
    in pixels, so two horizontal charts with a different row count still end at the same y
    position instead of each sizing itself to its own row count; `label_angle` fixes the category
    axis label rotation, so charts with different label lengths still render at the same total
    height instead of Vega-Lite auto-rotating some and not others. `details` maps a key to extra
    fields shown only in its tooltip (the same field names for every key)."""
    rows = []
    detail_names = list(next(iter(details.values()))) if details else []
    for key, value in counts.items():
        row = {category_title: key, value_title: value}
        row.update((details or {}).get(key, {}))
        if percent_of:
            row["percent"] = 100 * value / percent_of
        rows.append(row)
    tooltip = [alt.Tooltip(category_title), alt.Tooltip(value_title)]
    tooltip.extend(alt.Tooltip(name) for name in detail_names)
    if percent_of:
        tooltip.append(alt.Tooltip("percent:Q", title="% of enriched companies", format=".1f"))
    axis_kwargs = {}
    if full_labels:
        axis_kwargs["labelLimit"] = 0
    if label_angle is not None:
        axis_kwargs["labelAngle"] = label_angle
    axis = alt.Axis(**axis_kwargs)
    if rows:
        columns = [category_title, value_title] + (["percent"] if percent_of else []) + detail_names
        df = pd.DataFrame(rows, columns=columns)
    else:
        # An empty object-dtype column infers as pandas dtype "empty", which Altair doesn't
        # recognise and warns about; give explicit dtypes so an empty chart renders quietly.
        df = pd.DataFrame(
            {category_title: pd.Series(dtype="str"), value_title: pd.Series(dtype="float")}
        )
        for name in ["percent"] * bool(percent_of) + detail_names:
            df[name] = pd.Series(dtype="float")
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


def _is_writable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK) and os.access(path.parent, os.W_OK)
    return os.access(path.parent, os.W_OK)


def open_engine(db_path: str) -> Engine:
    """A writable path gets the normal engine and `init_db` (creates or migrates the schema). A
    read-only one (the committed WAL snapshot on a read-only deployment) is opened immutable and
    left untouched: a normal connection would try to write even on a read."""
    path = Path(db_path)
    if _is_writable(path):
        engine = get_engine(path)
        init_db(engine)
        return engine
    return create_engine(
        f"sqlite:///file:{quote(str(path.resolve()))}?mode=ro&immutable=1&uri=true"
    )


@st.cache_resource
def _engine(db_path: str) -> Engine:
    return open_engine(db_path)


def current_db_path() -> str:
    return st.session_state.get("db_path", str(get_settings().db_path))


def session() -> Session:
    return Session(_engine(current_db_path()))


def db_version(db_path: str) -> float:
    """Latest modification time of the database file or its WAL, part of every cache key so a
    refreshed database is not served from a stale cache."""
    path = Path(db_path)
    mtimes = [p.stat().st_mtime for p in (path, path.with_name(path.name + "-wal")) if p.exists()]
    return max(mtimes, default=0.0)


def plain(text: str | None) -> str:
    """Neutralise Markdown and HTML so model-written or scraped text renders as text (D41)."""
    return _MARKDOWN_PUNCTUATION.sub(r"\\\1", text or "")


def source_link(url: str | None, label: str = "Source", *, key: str | None = None) -> bool:
    """A fixed-label link button; nothing is rendered unless the URL is http(s), and the return
    value says whether it was. Pass a unique `key` when the same label and URL can appear twice
    on a page."""
    if not (url and url.lower().startswith(("http://", "https://"))):
        return False
    st.link_button(label, url, key=key, type="tertiary", icon=":material/open_in_new:")
    return True


def confidence_badge(level: str | None) -> None:
    st.badge(
        level if level in _CONFIDENCE_COLORS else "unknown confidence",
        color=_CONFIDENCE_COLORS.get(level or "", "gray"),
    )


def money(value_million_usd: int | float | None) -> str:
    """US$ millions as text; a missing amount is "Not disclosed", never 0."""
    if value_million_usd is None:
        return NOT_DISCLOSED
    if value_million_usd >= 1000:
        return f"US${value_million_usd / 1000:g}bn"
    return f"US${value_million_usd:g}m"
