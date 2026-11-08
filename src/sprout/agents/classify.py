"""Classification agent: assigns one taxonomy category to a company that already has a profile.

It is an independent second opinion: the prompt carries the profile facts but never the category
enrichment chose. It may `fetch_page` (a thin profile can be classified from the company's own
site) but not search. The result overwrites `company.category`; the run row's `stats` keep the
rationale and the category it replaced.
"""

import json
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, col, select

from sprout.agents.common import AgentFailed, Deps, run_recorded
from sprout.agents.enrich import CATEGORY_GUIDANCE, UNTRUSTED_CONTENT_RULE
from sprout.agents.loop import Budget, LoopResult, Tool, ToolOutcome
from sprout.agents.tools import (
    ALLOWED_CLASSIFY_TOOLS,
    SourceLedger,
    assert_tools_allowed,
    build_tools,
    wrap_untrusted,
)
from sprout.db import Company, Fact, RunKind, RunLog, winning_facts
from sprout.models import Category

CLASSIFY_BUDGET = Budget(max_iterations=4, max_usd=Decimal("0.01"), max_repairs=1)
MAX_OUTPUT_TOKENS = 1_000
RATIONALE_MAX_CHARS = 300
PROFILE_FIELDS = ("one_liner", "business_model", "target_user", "website", "hq")
_CATEGORY_VALUES = [c.value for c in Category]

SYSTEM_PROMPT = f"""\
You classify ONE UK company into a fixed taxonomy, using only the profile facts you are given and, \
if they are too thin to decide, the company's own website.

Process
1. Read the facts. If they do not say what the company does, call fetch_page once on its website \
(if one is given) before deciding.
2. Call submit_classification exactly once. Do not write a prose answer.

What to submit
- category: {CATEGORY_GUIDANCE}
- rationale: one sentence of at most {RATIONALE_MAX_CHARS} characters saying what the company does \
and why that category fits.

Untrusted content
{UNTRUSTED_CONTENT_RULE}
"""


class Classification(BaseModel):
    category: Category
    rationale: str = Field(min_length=1, max_length=RATIONALE_MAX_CHARS)


class ClassificationFailed(AgentFailed):
    label = "classification"


def _submit_tool() -> Tool:
    repair_given = False

    def handle(args: dict[str, Any]) -> ToolOutcome:
        nonlocal repair_given
        category = args.get("category")
        if category not in _CATEGORY_VALUES:
            if not repair_given:
                repair_given = True
                return ToolOutcome(
                    f"category {category!r} is not in the taxonomy. Call submit_classification "
                    f"again with exactly one of: {'; '.join(_CATEGORY_VALUES)}.",
                    is_error=True,
                )
            category = Category.OTHER.value  # the repair is spent: same rule as enrichment
        rationale = args.get("rationale")
        if isinstance(rationale, str):
            rationale = rationale[:RATIONALE_MAX_CHARS]  # cosmetic field: trim, never fail on it
        try:
            classification = Classification(category=category, rationale=rationale)
        except ValidationError:
            return ToolOutcome("rationale must be a non-empty string.", is_error=True)
        return ToolOutcome("Classification accepted.", payload=classification)

    return Tool(
        spec={
            "name": "submit_classification",
            "description": "Submit the company's category. Call exactly once, as the last step.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": _CATEGORY_VALUES},
                    "rationale": {"type": "string"},
                },
                "required": ["category", "rationale"],
                "additionalProperties": False,
            },
        },
        handler=handle,
        terminal=True,
    )


def _user_message(session: Session, company: Company) -> str:
    lines = [f"Company: {company.name}"]
    if company.sic_codes:
        lines.append(f"SIC codes: {', '.join(company.sic_codes)}")
    if company.locality:
        lines.append(f"Registered office locality: {company.locality}")
    facts = winning_facts(session.exec(select(Fact).where(Fact.company_id == company.id)).all())
    blocks = []
    for name in PROFILE_FIELDS:
        fact = facts.get(name)
        if fact is not None and fact.value_json is not None and fact.source_url:
            value = json.loads(fact.value_json)
            blocks.append(wrap_untrusted(fact.source_url, f"{name}: {value}"))
    lines.append("Profile facts, extracted from web pages:")
    lines.extend(blocks or ["(none)"])
    lines.append("\nClassify this company and call submit_classification.")
    return "\n".join(lines)


def _enrichment_category(session: Session, company: Company) -> str | None:
    """The category enrichment chose. Normally `company.category`; once a classification has
    already overwritten it, the first classification after the latest enrichment remembers it, so
    re-classifying never reports the classifier as its own predecessor."""
    runs = session.exec(
        select(RunLog)
        .where(
            RunLog.company_id == company.id,
            RunLog.status == "ok",
            col(RunLog.failure_category).is_(None),
            col(RunLog.kind).in_(
                [RunKind.COMPANY_ENRICH.value, RunKind.COMPANY_CLASSIFICATION.value]
            ),
        )
        .order_by(col(RunLog.id).desc())
    ).all()
    for run in runs:
        if run.kind == RunKind.COMPANY_ENRICH.value:
            break
        earlier = run.stats.get("previous_category")
        if earlier is not None:
            return str(earlier)
    return company.category


def classify_company(
    session: Session, company: Company, deps: Deps, budget: Budget = CLASSIFY_BUDGET
) -> Classification:
    """Classify one enriched company and store the category. Raises `AgentFailed` on non-success."""
    return classify_with_result(session, company, deps, budget)[0]


def classify_with_result(
    session: Session, company: Company, deps: Deps, budget: Budget = CLASSIFY_BUDGET
) -> tuple[Classification, LoopResult]:
    """Like `classify_company`, also returning the loop result (for cost)."""
    ledger = SourceLedger()
    tools = [*build_tools(deps.fetcher, None, ledger, max_searches=0), _submit_tool()]
    assert_tools_allowed(tools, ALLOWED_CLASSIFY_TOOLS, "ALLOWED_CLASSIFY_TOOLS")
    previous = _enrichment_category(session, company)

    def persist(classification: Classification) -> dict[str, Any]:
        company.category = classification.category.value
        session.add(company)
        session.flush()
        return {
            "category": classification.category.value,
            "previous_category": previous,
            "rationale": classification.rationale,
        }

    result = run_recorded(
        session,
        company,
        RunKind.COMPANY_CLASSIFICATION,
        deps,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _user_message(session, company)}],
        tools=tools,
        budget=budget,
        ledger=ledger,
        max_tokens=MAX_OUTPUT_TOKENS,
        persist=persist,
        failed=ClassificationFailed,
    )
    return result.payload, result
