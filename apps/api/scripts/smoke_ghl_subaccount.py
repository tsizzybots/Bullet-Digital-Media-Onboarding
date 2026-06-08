"""Throwaway smoke test for the S1-25 GHL sub-account create contract.

Hits the REAL GoHighLevel agency API to confirm our create-location
payload is accepted and our response parsing matches what GHL actually
returns. There is no separate GHL sandbox host - this creates a real
sub-account in whatever agency the supplied key belongs to, so by default
the script DELETES the location it creates straight afterwards (pass
`--keep` to retain it for manual inspection).

Not part of the test suite (no `test_` prefix, lives in scripts/). Run it
once when the agency credentials land, then again per environment if the
GHL contract is ever in doubt. Mirrors the S1-25a
`inspect_pandadoc_document.py` precedent.

Credentials are read from the environment so they never enter a command
line or the chat transcript:

    GHL_AGENCY_API_KEY  - agency Bearer token (required)
    GHL_COMPANY_ID      - agency companyId   (required)
    GHL_API_BASE_URL    - override host       (optional)
    GHL_API_VERSION     - override version    (optional)

Usage (from apps/api, with the two vars set in your shell or apps/api/.env):

    set -a && source .env && set +a            # if using .env
    uv run python scripts/smoke_ghl_subaccount.py
    uv run python scripts/smoke_ghl_subaccount.py --keep      # don't delete
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import httpx

from bullet_api.ghl.client import (
    GHL_API_BASE_URL,
    GHL_API_VERSION,
    GhlClientError,
    GhlServerError,
    HttpGhlClient,
)


def _mask(secret: str) -> str:
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}...{secret[-4:]}"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"ERROR: {name} is not set. Export it (or put it in apps/api/.env) and retry.")
    return value


async def _delete_location(*, base_url: str, version: str, api_key: str, location_id: str) -> None:
    """Best-effort cleanup of the location we just created.

    GHL deletes a sub-account via DELETE /locations/{id}; deleteTwilioAccount
    is required by the API. Failures here are reported but not fatal - if
    cleanup fails the script prints the id so it can be removed by hand.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(
            f"{base_url.rstrip('/')}/locations/{location_id}",
            params={"deleteTwilioAccount": "false"},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Version": version,
            },
        )
    print(f"  DELETE status: {resp.status_code}")
    if resp.status_code >= 300:
        print(f"  cleanup body: {resp.text}")
        print(f"  !! could not auto-delete - remove location {location_id} manually.")
    else:
        print(f"  cleaned up location {location_id}.")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test GHL sub-account creation.")
    parser.add_argument(
        "--keep", action="store_true", help="Keep the created location (skip cleanup)."
    )
    args = parser.parse_args()

    api_key = _require("GHL_AGENCY_API_KEY")
    company_id = _require("GHL_COMPANY_ID")
    base_url = os.environ.get("GHL_API_BASE_URL", GHL_API_BASE_URL)
    version = os.environ.get("GHL_API_VERSION", GHL_API_VERSION)

    # A clearly-labelled, obviously-disposable test location so it is never
    # mistaken for a real client if cleanup fails.
    suffix = f"{int(time.time())}-{os.getpid()}"
    payload = {
        "name": f"IZZYAGENTS SMOKE TEST {suffix} (delete me)",
        "companyId": company_id,
        "phone": "+447000000000",
        "prospectInfo": {
            "firstName": "Smoke",
            "lastName": "Test",
            "email": f"smoke+{suffix}@izzyagents.ai",
        },
    }

    print("=== GHL sub-account create smoke test ===")
    print(f"  base_url : {base_url}")
    print(f"  version  : {version}")
    print(f"  api_key  : {_mask(api_key)}")
    print(f"  company  : {company_id}")
    print(f"  payload  : {json.dumps(payload)}")
    print()

    # Step 1: exercise the production client exactly as the worker does.
    client = HttpGhlClient(api_key=api_key, base_url=base_url, version=version)
    print("--> POST /locations/ via HttpGhlClient.create_location ...")
    try:
        location = await client.create_location(payload)
    except GhlClientError as exc:
        print(f"  4xx (non-retriable) {exc.status_code}: {exc.body}")
        print("  => GHL REJECTED our payload. The body above lists the fields it wants.")
        return 1
    except GhlServerError as exc:
        print(f"  5xx/429 (retriable) {exc.status_code}: {exc.body}")
        print("  => transient GHL error; re-run.")
        return 1

    print("  SUCCESS - our parser extracted:")
    print(f"    id         : {location.id}")
    print(f"    name       : {location.name}")
    print(f"    company_id : {location.company_id}")
    print(f"  raw response keys: {sorted(location.raw.keys())}")
    print()

    # Step 2: cleanup unless --keep.
    if args.keep:
        print(f"--keep set: leaving location {location.id} in place.")
    else:
        print("--> cleaning up ...")
        await _delete_location(
            base_url=base_url, version=version, api_key=api_key, location_id=location.id
        )

    print("\nDONE. The S1-25 create contract is confirmed against the live GHL API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
