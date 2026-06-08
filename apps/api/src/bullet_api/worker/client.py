"""Inngest function registry for bullet-api.

The shared ``inngest_client`` itself lives in ``bullet_api.worker._inngest``
so every function-registering module (this one, ``client_record``, future
GHL / Stripe / etc. handlers) can import it without closing a cycle. This
module aggregates every registered function into ``FUNCTIONS`` for the
FastAPI Inngest serve adapter in ``bullet_api.main``.

Every handler imported here decorates itself against the shared
``inngest_client`` at module-load time; collecting the function objects
into ``FUNCTIONS`` is what makes the FastAPI adapter expose them. To add a
new handler: import it here and append it to ``FUNCTIONS``.
"""

from __future__ import annotations

import inngest

from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.client_record import create_client_record
from bullet_api.worker.ghl_subaccount import create_ghl_subaccount
from bullet_api.worker.signed_pdf import store_signed_pdf


@inngest_client.create_function(
    fn_id="noop-test",
    trigger=inngest.TriggerEvent(event="bullet/noop"),
)
async def noop_function(ctx: inngest.Context, step: inngest.Step) -> str:
    """No-op function registered to verify Inngest connectivity."""
    return "noop"


FUNCTIONS = [noop_function, create_client_record, create_ghl_subaccount, store_signed_pdf]

# `inngest_client` is intentionally NOT re-exported here - it is sourced
# from `bullet_api.worker._inngest` and re-exported from
# `bullet_api.worker.__init__` so the public single home is the package
# root, not this aggregator. Importers should write
# `from bullet_api.worker import inngest_client` (or `_inngest` directly).
__all__ = [
    "FUNCTIONS",
    "create_client_record",
    "create_ghl_subaccount",
    "noop_function",
    "store_signed_pdf",
]
