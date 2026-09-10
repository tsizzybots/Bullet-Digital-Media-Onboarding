"""Live smoke for the S1-29 -> S1-30 back half: summary + embeddings (S1-34a).

Exercises the two "Fake -> Http swaps purely on config" seams against the REAL
provider APIs, which is the part the unit suite (built on FakeSummaryClient /
FakeEmbeddingClient) cannot cover:

1. `HttpAnthropicClient.summarise(transcript_text)` - real Anthropic
   `messages.parse(output_format=SalesCallSummary)` structured-output call. Also
   reveals whether the configured `ANTHROPIC_MODEL` supports structured outputs
   with the installed SDK (if not, this 400s and the fix is bumping the model).
2. `render_knowledge_fields(summary)` -> `HttpOpenAIEmbeddingClient.embed(...)` -
   real OpenAI `text-embedding-3-small` batch call; confirms every vector is
   `EMBEDDING_DIM` (1536), matching the `client_knowledge.embedding vector(1536)`
   column.

Deliberately skips R2 / the DB / Inngest: the production worker reads the
transcript from R2 (`sales_summary.py`) and writes rows to Postgres, but the
LIVE-API risk lives entirely in the two client seams, which take/return plain
Python objects. So this runs with just the Anthropic + OpenAI keys - no R2, no
staging DB, no Inngest.

SPENDS REAL API CREDITS on IzzyAgents' own Anthropic + OpenAI accounts (one
summary + a handful of embeddings - cents). Run with the keys in the env:

    ANTHROPIC_API_KEY=... OPENAI_API_KEY=... uv run python scripts/smoke_summary_embeddings.py
"""

from __future__ import annotations

import asyncio

from bullet_api.config import get_settings
from bullet_api.worker.embedding_client import EMBEDDING_DIM, get_embedding_client
from bullet_api.worker.sales_knowledge import render_knowledge_fields
from bullet_api.worker.summary_client import get_summary_client

# A short, realistic gym-owner discovery call so the summariser has each PRD
# §7.1 field to populate (business type, goals, budget, pain points, next steps,
# a quote). Kept compact to keep the token spend tiny.
_SAMPLE_TRANSCRIPT = """
Steve (Bullet): Thanks for hopping on, Dana. Tell me about the gym.
Dana (Iron Valley Fitness): We're a boutique strength gym in Leeds, been open
three years. Two locations now, about 180 members total. We mostly do small-group
personal training and a barbell club.
Steve: Nice. What's the goal for the next 6-12 months?
Dana: Honestly we want to fill the second location - it's running at maybe 40%
capacity. And we'd love to launch a nutrition-coaching upsell but we've never
marketed it properly. Realistically we can put about 2,000 pounds a month into
ads, maybe a bit more if it's working.
Steve: Got it. What's been the frustration with marketing so far?
Dana: We tried Facebook ads ourselves and it was a disaster - burnt about 3k with
almost no leads, and the ones we got were tyre-kickers who never showed up to the
consult. Our follow-up is also all over the place; leads sit in a spreadsheet and
nobody chases them. And the second location has almost no local awareness.
Steve: Understood. Anything I should flag internally - timing, decision-makers?
Dana: My business partner needs to sign off on anything over 2k a month, and
we're going on holiday in three weeks so I'd want things live before then. To be
blunt, if we don't see booked consults in the first month we'll pull the plug.
Steve: Fair. Next step from my side is to put together an offer and a lead-gen
plan for the second location plus the nutrition upsell, and get you a proposal by
Friday.
Dana: "If you can actually fill that second gym, I'll happily be a case study."
"""


async def main() -> int:
    settings = get_settings()
    print("Config:")
    print(f"  ANTHROPIC_MODEL         = {settings.anthropic_model}")
    print(f"  OPENAI_EMBEDDING_MODEL  = {settings.openai_embedding_model}")
    print(f"  anthropic key present   = {bool(settings.anthropic_api_key)}")
    print(f"  openai key present      = {bool(settings.openai_api_key)}")
    if not settings.anthropic_api_key or not settings.openai_api_key:
        print("\nFAIL: both ANTHROPIC_API_KEY and OPENAI_API_KEY must be set.")
        return 1

    # ---- 1. Summary (live Anthropic structured output) --------------------
    print("\n=== 1. Anthropic summarise() (structured output) ===")
    try:
        summary = await get_summary_client().summarise(_SAMPLE_TRANSCRIPT)
    except Exception as exc:  # SummaryConfigError / Refused / Truncated / API 4xx
        print(f"FAIL (summarise): {type(exc).__name__}: {exc}")
        print(
            "  If this is a 400/invalid_request about structured outputs, the "
            "configured ANTHROPIC_MODEL may not support them - try claude-opus-4-8."
        )
        return 1
    print("PASS (summarise). SalesCallSummary validated. Fields:")
    print(f"  business_type   = {summary.business_type!r}")
    print(f"  business_goals  = {summary.business_goals}")
    print(f"  budget_range    = {summary.budget_range_usd}")
    print(f"  pain_points     = {summary.pain_points}")
    print(f"  red_flags       = {summary.red_flags}")
    print(f"  next_steps      = {summary.next_steps}")
    print(f"  notable_quotes  = {len(summary.notable_quotes)} quote(s)")

    # ---- 2. Render §7.1 fields + embed (live OpenAI) ----------------------
    print("\n=== 2. render_knowledge_fields() + OpenAI embed() ===")
    fields = render_knowledge_fields(summary)
    print(f"Rendered {len(fields)} field rows (expect 7). Non-empty value_texts get embedded:")
    non_empty = [(k, vt) for (k, _v, vt) in fields if vt.strip()]
    for k, _v, vt in fields:
        marker = "embed" if vt.strip() else "NULL "
        print(f"  [{marker}] {k}: {vt[:70]!r}")

    if not non_empty:
        print("\nWARN: no non-empty value_texts to embed (summary was empty). Nothing to smoke.")
        return 1

    try:
        vectors = await get_embedding_client().embed([vt for _k, vt in non_empty])
    except Exception as exc:  # EmbeddingConfigError / API 4xx
        print(f"\nFAIL (embed): {type(exc).__name__}: {exc}")
        return 1

    print(f"\nPASS (embed). Got {len(vectors)} vectors for {len(non_empty)} inputs.")
    dims = {len(v) for v in vectors}
    print(f"  vector dimensions = {dims} (expect {{{EMBEDDING_DIM}}})")
    if dims != {EMBEDDING_DIM}:
        print(f"\nFAIL: dimension mismatch - column is vector({EMBEDDING_DIM}).")
        return 1
    if len(vectors) != len(non_empty):
        print("\nFAIL: vector count != input count (one-vector-per-input invariant).")
        return 1

    print("\nALL PASS: live summary + embeddings work end to end (minus R2/DB/Inngest).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
