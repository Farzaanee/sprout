# Phase 1: Data foundations

Status: done. Part of the [Sprout architecture docs](README.md).

```mermaid
flowchart TB
    subgraph EXT["External services"]
        CH(["Companies House API"])
        RSSF(["6 RSS feeds"])
        WEB(["Public websites, robots.txt"])
        CH ~~~ RSSF
        RSSF ~~~ WEB
    end
    subgraph SRC["sources/ adapters (synchronous)"]
        CHC["companies_house.py"]
        RSS["rss.py"]
        WF["web.py: WebFetcher, robots, public-IP only, redirect checks"]
        HTTP["http.py: HttpClient with DiskCache, rate limits, retries, size cap"]
        CHC ~~~ RSS
        RSS ~~~ WF
        WF ~~~ HTTP
    end
    CAND["candidates.py: SIC and keyword score"]
    ING["ingest.py"]
    DBM["db.py: upsert_company, save_fact, add_news_items, logged_run"]
    SQL[("SQLite: company, fact, news_item, run_log")]
    CACHE[("Disk cache .cache/http")]
    CH --> CHC
    RSSF --> RSS
    WEB --> WF
    CHC --> HTTP
    RSS --> HTTP
    WF --> HTTP
    HTTP <--> CACHE
    ING --> CHC
    ING --> RSS
    ING --> CAND
    ING --> DBM
    DBM --> SQL
```

`WebFetcher` is not used by ingest; since Phase 2 it is called by the enrichment agent's `fetch_page` tool (see the [Phase 2 page](phase-2-enrichment.md)). Ingest writes two registry-sourced facts per company (`hq`, `founded`, confidence medium).

`candidates.py`'s `assess()` rejects non-active companies and anything incorporated before 2012-01-01, then scores the rest from the company name and SIC codes: 2 points per strong keyword match (`artificial intelligence`, `neural`, `robot*`, etc.), 1 point per weak keyword match (`ai`, `analytics`, `cognitive`, etc., which only qualify a company at all alongside a tech SIC code), plus 1 if any SIC code is in the tech set. The result is a low-cardinality score (most candidates land on 2, 3 or 5), so ties across companies are expected, not a bug.
