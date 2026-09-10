"""Live GHL create-location smoke (S1-34a smoke, step 1b).

Creates ONE clearly-labelled throwaway sub-account against Bullet's live agency,
verifies the real request/response shape matches what `create_location` and the
S1-25 worker assume, then DELETES it again. This is the check the S1-34a card
flags as "confirm the GHL contract before production".

OUTWARD-FACING: this creates a real location in Bullet's live GHL agency
(deleted at the end). It refuses to run unless explicitly confirmed, so it can
never fire by accident:

    GHL_SMOKE_CONFIRM=yes uv run python scripts/smoke_ghl_subaccount.py

If create succeeds but delete fails, the script prints the location id LOUDLY so
it can be removed by hand - a leaked throwaway location is the one thing to avoid.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx

from bullet_api.config import get_settings
from bullet_api.ghl.client import (
    GHL_API_BASE_URL,
    GHL_API_VERSION,
    GhlError,
    HttpGhlClient,
)

_LABEL = "ZZZ S1-34a SMOKE - DELETE ME"


async def main() -> int:
    if os.environ.get("GHL_SMOKE_CONFIRM") != "yes":
        print(
            "REFUSING to run: this creates a real location in Bullet's live GHL agency.\n"
            "Re-run with GHL_SMOKE_CONFIRM=yes once you have approved the live create."
        )
        return 2

    settings = get_settings()
    api_key = settings.ghl_agency_api_key
    company_id = settings.ghl_company_id
    if not api_key or not company_id:
        print("FAIL: GHL_AGENCY_API_KEY and GHL_COMPANY_ID must both be set.")
        return 1

    base = (settings.ghl_api_base_url or GHL_API_BASE_URL).rstrip("/")
    version = settings.ghl_api_version or GHL_API_VERSION
    client = HttpGhlClient(api_key=api_key, base_url=base, version=version)

    payload = {"name": _LABEL, "companyId": company_id}
    print(f"--- POST {base}/locations/ ---\n{json.dumps(payload, indent=2)}")

    try:
        location = await client.create_location(payload)
    except GhlError as exc:
        print(f"\nFAIL (create): {exc!r}")
        return 1
    except Exception as exc:  # transport-level
        print(f"\nFAIL (transport): {exc!r}")
        return 1

    print("\nPASS (create). Projected location:")
    print(f"  id         = {location.id}")
    print(f"  name       = {location.name}")
    print(f"  company_id = {location.company_id}")
    # The worker persists `raw` onto platform_actions.response - eyeball it for
    # any field we mis-mapped.
    print("  raw keys   =", sorted(location.raw.keys()))

    # Cleanup: DELETE the throwaway. The client has no delete method, so do it
    # raw. VERIFIED 21/07 against the live agency: the endpoint is
    # DELETE /locations/{id}?deleteTwilioAccount=true and it REJECTS a companyId
    # query param (422 "property companyId should not exist") - unlike the
    # create/search endpoints, which require companyId.
    print(f"\n--- DELETE {base}/locations/{location.id} (cleanup) ---")
    async with httpx.AsyncClient(timeout=10.0) as raw:
        try:
            resp = await raw.delete(
                f"{base}/locations/{location.id}",
                headers={"Authorization": f"Bearer {api_key}", "Version": version},
                params={"deleteTwilioAccount": "true"},
            )
        except httpx.HTTPError as exc:
            print(f"\n!!! DELETE transport error: {exc!r}")
            print(f"!!! LEAKED throwaway location id={location.id} - delete it by hand.")
            return 1

    if 200 <= resp.status_code < 300:
        print(f"PASS (delete). HTTP {resp.status_code}. Throwaway removed.")
        return 0
    print(f"\n!!! DELETE returned HTTP {resp.status_code}: {resp.text[:400]}")
    print(f"!!! LEAKED throwaway location id={location.id} - delete it by hand.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
