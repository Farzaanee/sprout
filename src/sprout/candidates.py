"""Heuristic filter: is a Companies House entity plausibly an AI startup?

This only produces *candidates*. Companies House describes legal entities, and most AI
companies register under generic software SIC codes, so the signal is the company name plus
a tech SIC code. Expect false positives and misses; later phases verify.
"""

import re
from dataclasses import dataclass
from datetime import date

MIN_INCORPORATION_DATE = date(2012, 1, 1)

# D34: a single-officer company is overwhelmingly a shell or trade body in the pilot sample
# (12/12 `Other`-category companies had exactly one active officer, vs. 13/19 real companies
# having two or more). Catches no false positives in that sample at a much better recall than
# filtering on accounts type.
MIN_ACTIVE_OFFICERS = 2

# 2007 SIC codes: software, IT services, data processing, web portals, other info
# services, and R&D in natural sciences/engineering.
TECH_SIC_CODES = frozenset({"62011", "62012", "62020", "62090", "63110", "63120", "63990", "72190"})

# Unambiguous on their own.
STRONG_KEYWORDS = (
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "neural",
    "generative",
    "computer vision",
    "llm",
    "robot\\w*",
)
# Common words that only count alongside a tech SIC code ("Ai Restaurant Ltd" is not AI).
# "ai" and "intelligen*" stand on their own with a tech SIC code (e.g. "Faculty Ai Ltd"). The
# rest are too generic to trust alone, a data-analytics agency, an algorithmic trading firm or
# an autonomous-vehicle-parts supplier can equally match one of them with a tech SIC code, so
# D34 found these let shell-style and non-AI candidates into the pool; they need a second
# keyword (weak or strong) to corroborate.
WEAK_KEYWORDS_SELF_SUFFICIENT = ("ai", "intelligen\\w*")
WEAK_KEYWORDS_NEEDS_CORROBORATION = ("cognitive", "autonomous", "algorithm\\w*", "analytics")
WEAK_KEYWORDS = WEAK_KEYWORDS_SELF_SUFFICIENT + WEAK_KEYWORDS_NEEDS_CORROBORATION

# Name fragments Companies House is asked to match during discovery.
DISCOVERY_TERMS = (
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "neural",
    "generative",
    "computer vision",
    "robotics",
    "intelligence",
    "ai",
    "cognitive",
    "autonomous",
    "analytics",
)


def _compile(keywords: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(keywords)
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


_STRONG = _compile(STRONG_KEYWORDS)
_WEAK = _compile(WEAK_KEYWORDS)
_WEAK_SELF_SUFFICIENT = _compile(WEAK_KEYWORDS_SELF_SUFFICIENT)


@dataclass(frozen=True)
class Assessment:
    is_candidate: bool
    score: int
    matched_keywords: tuple[str, ...]


def assess(
    name: str,
    sic_codes: list[str] | tuple[str, ...] = (),
    *,
    status: str | None = "active",
    incorporated: date | None = None,
) -> Assessment:
    """Score a company. Strong keyword = 2, weak keyword = 1, tech SIC code = +1."""
    if status is not None and status != "active":
        return Assessment(False, 0, ())
    if incorporated is not None and incorporated < MIN_INCORPORATION_DATE:
        return Assessment(False, 0, ())

    normalised = name.lower().replace(".", "")  # "A.I." -> "ai"
    strong = sorted({m.group(0) for m in _STRONG.finditer(normalised)})
    weak = sorted({m.group(0) for m in _WEAK.finditer(normalised)})
    weak_self_sufficient = bool(_WEAK_SELF_SUFFICIENT.search(normalised))
    has_tech_sic = any(code in TECH_SIC_CODES for code in sic_codes)

    weak_qualifies = (weak_self_sufficient or len(weak) >= 2) and has_tech_sic
    if not (strong or weak_qualifies):
        return Assessment(False, 0, ())
    score = 2 * len(strong) + len(weak) + (1 if has_tech_sic else 0)
    return Assessment(True, score, tuple(strong + weak))


def passes_prescreen(active_officers: int | None, has_active_director: bool) -> bool:
    """Shell/non-AI pre-screen (D34): reject candidates with fewer than `MIN_ACTIVE_OFFICERS`,
    or with no active officer in the `director` role. A company run entirely by a secretary or
    nominee, with no named director, is the shell pattern officer count alone let through."""
    return (
        active_officers is not None
        and active_officers >= MIN_ACTIVE_OFFICERS
        and has_active_director
    )
