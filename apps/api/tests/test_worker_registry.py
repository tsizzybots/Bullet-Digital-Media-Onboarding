"""Guard tests for the Inngest function registry (`worker.client.FUNCTIONS`).

Inngest allows a MAX of 2 concurrency constraints per function, and registration
is ALL-OR-NOTHING: one invalid function fails the whole `/fn/register` sync, so
NONE of the fan-out workers register (create_client_record, create_ghl_subaccount,
store_signed_pdf, capture_meet_transcript, summarise_sales_call, ...). This is a
latent prod-blocker that unit tests of the individual cores never catch, because
they call the core directly and never exercise registration. S1-26a fixed a
`create_ghl_subaccount` that declared 3; this guard stops any function from
regressing past 2.
"""

from __future__ import annotations

from bullet_api.worker.client import FUNCTIONS

_SERVE_ORIGIN = "http://localhost:8000/api/inngest"

# Inngest's per-function maximum number of concurrency constraints.
INNGEST_MAX_CONCURRENCY_CONSTRAINTS = 2


def test_every_function_within_inngest_concurrency_limit() -> None:
    """Every registered function declares <= 2 concurrency constraints, so the
    all-or-nothing Inngest registration can never fail on this account."""
    offenders = {}
    for fn in FUNCTIONS:
        caps = fn.get_config(_SERVE_ORIGIN).main.concurrency or []
        if len(caps) > INNGEST_MAX_CONCURRENCY_CONSTRAINTS:
            offenders[fn.get_id()] = len(caps)

    assert offenders == {}, (
        "these functions exceed Inngest's "
        f"{INNGEST_MAX_CONCURRENCY_CONSTRAINTS}-constraint concurrency limit and would "
        f"fail ALL function registration: {offenders}"
    )
