"""Read-only helper: discover the GHL agency `companyId` from a location.

The S1-25 create-sub-account call needs the agency `companyId`, which is
not one of the private-integration tokens. Every GHL location object
carries its parent `companyId`, so a single READ-ONLY
`GET /locations/{id}` with the agency token returns it. This script
creates / writes / deletes NOTHING - it is safe to run against the live
agency.

It doubles as an auth check: a 200 confirms the agency token + headers
work before we attempt anything that writes.

Credentials are read from the environment (never the command line / chat):

    GHL_AGENCY_API_KEY  - agency Bearer token (required)
    GHL_API_BASE_URL    - override host    (optional)
    GHL_API_VERSION     - override version (optional)

Usage (from apps/api, agency token set in your shell or apps/api/.env):

    set -a && source .env && set +a
    uv run python scripts/ghl_get_company_id.py                       # uses the known location id
    uv run python scripts/ghl_get_company_id.py <some-other-location-id>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

from bullet_api.ghl.client import GHL_API_BASE_URL, GHL_API_VERSION

# Bullet's master/template sub-account id, shared in the S1-25 discussion.
# Any location under the agency works - it just needs to be one the agency
# token can read.
DEFAULT_LOCATION_ID = "gdyA9mQE9Q7JZxHnPNCe"


def _mask(secret: str) -> str:
    return "****" if len(secret) <= 8 else f"{secret[:4]}...{secret[-4:]}"


async def main() -> int:
    api_key = os.environ.get("GHL_AGENCY_API_KEY", "").strip()
    if not api_key:
        sys.exit("ERROR: GHL_AGENCY_API_KEY is not set. Put it in apps/api/.env and retry.")
    base_url = os.environ.get("GHL_API_BASE_URL", GHL_API_BASE_URL).rstrip("/")
    version = os.environ.get("GHL_API_VERSION", GHL_API_VERSION)
    location_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOCATION_ID

    print("=== GHL companyId discovery (read-only) ===")
    print(f"  base_url : {base_url}")
    print(f"  version  : {version}")
    print(f"  api_key  : {_mask(api_key)}")
    print(f"  location : {location_id}")
    print()
    print(f"--> GET /locations/{location_id} ...")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{base_url}/locations/{location_id}",
            headers={"Authorization": f"Bearer {api_key}", "Version": version},
        )

    print(f"  status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"  body: {resp.text}")
        if resp.status_code in (401, 403):
            print(
                "  => agency token rejected. Check it's the AGENCY token, "
                "not the sub-account one."
            )
        return 1

    body = resp.json()
    # GHL returns either {"location": {...}} or the location object directly.
    location = body.get("location", body)
    company_id = location.get("companyId")

    print(f"  location keys: {sorted(location.keys())}")
    print()
    if company_id:
        print(f"  >>> companyId: {company_id}")
        print("\nAdd this to apps/api/.env as:")
        print(f"  GHL_COMPANY_ID={company_id}")
    else:
        print("  companyId not found in the response. Full body for inspection:")
        print(json.dumps(body, indent=2)[:2000])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
