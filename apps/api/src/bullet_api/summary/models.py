"""PRD section 7.1 sales-call summary contract (S1-29).

`SalesCallSummary` is the structured output the Claude summariser produces and
validates against (via `messages.parse(output_format=SalesCallSummary)`), and
the payload handed to S1-30 to write into `client_knowledge`. The seven
top-level field names are byte-identical to the §7.1 keys the dashboard
(`schemas.py:KnowledgeEntry`) and S1-30 read/write - changing them breaks that
contract.

Kept structured-output-safe: `extra="forbid"` (-> `additionalProperties: false`)
and no numeric `min`/`max`/length constraints (the Anthropic structured-output
schema cannot enforce them; the SDK strips them and validates client-side via
this model anyway). Fields a call may not surface default to empty / None, so a
sparse transcript still produces a valid object with all seven keys present.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BudgetRange(BaseModel):
    """The prospect's stated budget band. §7.1 `budget_range_usd`."""

    model_config = ConfigDict(extra="forbid")

    min: float
    max: float
    currency: str


class NotableQuote(BaseModel):
    """A verbatim quote worth surfacing. §7.1 `notable_quotes[]`."""

    model_config = ConfigDict(extra="forbid")

    speaker: str
    quote: str
    timestamp_seconds: int


class SalesCallSummary(BaseModel):
    """The full §7.1 structured summary of a sales call.

    One instance maps to seven `client_knowledge` rows (one per top-level field,
    `source='sales_call'`) when S1-30 writes it.
    """

    model_config = ConfigDict(extra="forbid")

    business_type: str = ""
    business_goals: list[str] = []
    budget_range_usd: BudgetRange | None = None
    pain_points: list[str] = []
    red_flags: list[str] = []
    next_steps: list[str] = []
    notable_quotes: list[NotableQuote] = []


__all__ = ["BudgetRange", "NotableQuote", "SalesCallSummary"]
