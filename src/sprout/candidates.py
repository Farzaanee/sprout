"""Heuristic filter: is a Companies House entity plausibly an AI startup?

This only produces *candidates*. Companies House describes legal entities, and most AI
companies register under generic software SIC codes, so the signal is the company name plus
a tech SIC code. Expect false positives and misses; later phases verify.
"""

import re
from dataclasses import dataclass
from datetime import date

MIN_INCORPORATION_DATE = date(2012, 1, 1)

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
WEAK_KEYWORDS = ("ai", "intelligen\\w*", "cognitive", "autonomous", "algorithm\\w*", "analytics")

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
    has_tech_sic = any(code in TECH_SIC_CODES for code in sic_codes)

    if not (strong or (weak and has_tech_sic)):
        return Assessment(False, 0, ())
    score = 2 * len(strong) + len(weak) + (1 if has_tech_sic else 0)
    return Assessment(True, score, tuple(strong + weak))
