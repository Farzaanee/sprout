# Sprout: Learnings - Phase 7 - Dashboard

**Planned:** data-app development with Streamlit (state, caching), presenting uncertain data honestly, reusing a single service layer across UI and MCP.

**Actual (done):** the optional "Ask" box was deliberately skipped (Phase 8 covers natural-language access). The narrow-window layout is only partly met, see [implementation-notes.md](../implementation-notes.md#phase-7---dashboard).

| Topic | Key learning |
| --- | --- |
| Design for the thin data, not the schema | The snapshot has 84 enriched companies of 1,672, a stage for 5 and rounds for 8. A stage-by-category heatmap would be mostly empty, so the Overview uses bars over known totals plus a coverage strip, and every filter caption says how many companies actually have the field. |
| Missing is not zero | A NULL winning fact is "Not disclosed" and no fact at all is "Not researched"; these are different statements and `get_profile` keeps them apart (`disclosed=False` versus an absent key). A stage whose rounds have no amount is listed, never charted as 0 (`funding_by_stage` returns `None`, not 0). |
| Unknown filter values never match | A company whose value is unknown is excluded by a filter on it, so the sidebar must show the `Coverage` counts or a filter silently hides most of the data. |
| Funding is the latest round | Total raised is nearly always unknown, so the funding filter and column use the latest round (D22) and are labelled "Latest round" rather than "funding raised". |
| Model text is untrusted | Scraped or model-written text reaches `st.markdown` only through `ui.plain()` (backslash-escaping Markdown punctuation); links are fixed-label buttons over http(s) URLs only. `plain()` is only tested at string level, whether a bare URL still autolinks in a browser was not checked. |
| Read-only snapshot | `db.get_engine` turns on WAL, so even a read tries to write; on a read-only deployment the snapshot must be opened with `mode=ro&immutable=1` and no `init_db` (`ui.open_engine`). The check is on both the file and its directory. |
| Cache keys need a DB version | Pages cache plain dataclasses on `(db_path, db_version, Filters)`; `db_version` is the latest mtime of the file or its `-wal`, otherwise a refreshed database serves stale results. `Filters` is frozen so it hashes, and ORM rows are never cached. |
| Selection state in a dataframe | A single-row selection is an index into the shown rows, so after a filter change it would point at another company. The dataframe key includes a hash of the shown ids, which resets it. |
| Repeated link buttons need keys | `st.link_button` with the same label and URL twice on a page raises a duplicate-widget error, so `source_link` takes a `key`. |
| Altair charts pan on scroll | `st.bar_chart` is interactive, so the mouse wheel zooms it; `ui.bar_chart` is a plain Altair chart with fixed height and label angle so adjacent charts line up. |
| One service layer for UI and MCP | New `queries.py` parameters are keyword-only with defaults preserving earlier behaviour, and `queries.py` imports only `db`, `models` and `status` (a test enforces it), so Phase 8 reuses the functions unchanged. |
| Performance was not the constraint | A filter change reran all six tabs in 0.19 to 0.26 s on the snapshot and 0.57 to 0.87 s on a 40x synthetic copy, so the one-second budget needed no query tuning. |
| Narrow windows | With the sidebar open at 800 px the Companies dataframe shows only its name column; a dataframe cannot reflow, so the fix would be a leaner list or a collapsed sidebar, not CSS. |

Page structure, module responsibilities and rendering rules are in [architecture/phases/phase-7-dashboard.md](../architecture/phases/phase-7-dashboard.md).
