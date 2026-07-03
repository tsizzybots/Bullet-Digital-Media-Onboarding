"""Guard tests for the Inngest function registry (`worker.client.FUNCTIONS`).

Inngest allows a MAX of 2 concurrency constraints per function, and registration
is ALL-OR-NOTHING: one function exceeding it makes the whole `/fn/register` sync
fail, so NONE of the fan-out workers register. That exact breakage was S1-26a
(`create_ghl_subaccount` declared 3). This guard catches ONLY that regression -
a function declaring more than 2 concurrency constraints. It is a PROXY for the
specific failure, not a proof that registration as a whole succeeds; the live
`/fn/register` returning 200 (vs 400 with 3 constraints) was verified separately
by booting the app against the Inngest dev server (see the S1-26a CHANGELOG entry).
"""

from __future__ import annotations

from types import SimpleNamespace

from bullet_api.worker.client import FUNCTIONS

# Inngest's per-function maximum number of concurrency constraints.
INNGEST_MAX_CONCURRENCY_CONSTRAINTS = 2


def _functions_over_concurrency_limit(functions) -> dict[str, int]:
    """Return `{fn_id: constraint_count}` for every function declaring MORE than
    the Inngest per-function concurrency-constraint limit. Empty == all within.
    `get_config("")` matches the sibling config tests' call convention."""
    offenders: dict[str, int] = {}
    for fn in functions:
        caps = fn.get_config("").main.concurrency or []
        if len(caps) > INNGEST_MAX_CONCURRENCY_CONSTRAINTS:
            offenders[fn.get_id()] = len(caps)
    return offenders


def test_every_registered_function_within_concurrency_limit() -> None:
    """No registered function declares more than 2 concurrency constraints, so
    none can trip the all-or-nothing `/fn/register` limit (the S1-26a regression)."""
    offenders = _functions_over_concurrency_limit(FUNCTIONS)
    assert offenders == {}, (
        "these functions exceed Inngest's "
        f"{INNGEST_MAX_CONCURRENCY_CONSTRAINTS}-constraint concurrency limit and would "
        f"fail ALL function registration: {offenders}"
    )


def test_guard_fires_on_an_over_limit_function() -> None:
    """Negative self-test: the guard actually FLAGS a function with 3 constraints,
    so the assertion above is not a vacuous pass. Uses a stub exposing the same
    `get_config(origin).main.concurrency` shape the real functions do."""

    def _stub(fn_id: str, n: int):
        caps = [SimpleNamespace(key=None, limit=1) for _ in range(n)]
        return SimpleNamespace(
            get_id=lambda: fn_id,
            get_config=lambda _origin: SimpleNamespace(main=SimpleNamespace(concurrency=caps)),
        )

    assert _functions_over_concurrency_limit([_stub("within", 2)]) == {}
    assert _functions_over_concurrency_limit([_stub("over", 3)]) == {"over": 3}
