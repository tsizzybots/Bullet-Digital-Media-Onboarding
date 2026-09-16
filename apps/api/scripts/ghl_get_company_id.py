"""Read-only GHL agency reachability + contract probe (S1-34a smoke, step 1a).

Confirms, WITHOUT creating anything, that:

1. `GHL_AGENCY_API_KEY` authenticates against the live LeadConnector API.
2. `GHL_COMPANY_ID` is the value the create-location body needs (echoed back
   so a human can eyeball it against Bullet's agency).
3. The `find_location_by_email` search contract (`GET /locations/search`) that
   S1-26's returning-client check depends on actually matches the real API.
   That method carries a `CONFIRM PRE-PROD` marker in `ghl/client.py` - this
   probe is how we clear it: it prints the raw HTTP status + response envelope
   for a deliberately non-existent email so we can see the real shape (expected
   `{"locations": []}` or a 200 with an empty list) before trusting the parse.

Read-only: the only calls are GETs against the agency. No sub-account is
created, so this is safe to run against Bullet's live agency.

Run (with the staging env loaded - GHL_AGENCY_API_KEY + GHL_COMPANY_ID):
    uv run python scripts/ghl_get_company_id.py
"""

from __future__ import annotations

import asyncio
import json

import httpx

from bullet_api.config import get_settings
from bullet_api.ghl.client import GHL_API_BASE_URL, GHL_API_VERSION

# A search email that must not exist in the agency, so the probe never matches a
# real client and the response is the "no location" shape.
_PROBE_EMAIL = "s1-34a-nonexistent-probe@izzyagents.invalid"


async def main() -> int:
    settings = get_settings()
    api_key = settings.ghl_agency_api_key
    company_id = settings.ghl_company_id

    if not api_key:
        print("FAIL: GHL_AGENCY_API_KEY is empty. Set it on the env group before probing.")
        return 1
    print(f"GHL_AGENCY_API_KEY present (len={len(api_key)}, prefix={api_key[:4]}...)")
    print(f"GHL_COMPANY_ID = {company_id or '(EMPTY - required for create-location body)'}")

    base = (settings.ghl_api_base_url or GHL_API_BASE_URL).rstrip("/")
    version = settings.ghl_api_version or GHL_API_VERSION
    headers = {"Authorization": f"Bearer {api_key}", "Version": version}

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Probe the search contract (the CONFIRM PRE-PROD unknown). Read-only.
        print(f"\n--- GET {base}/locations/search (probe, email={_PROBE_EMAIL}) ---")
        try:
            resp = await client.get(
                f"{base}/locations/search",
                headers=headers,
                params={"companyId": company_id, "email": _PROBE_EMAIL, "limit": 1},
            )
        except httpx.HTTPError as exc:
            print(f"FAIL: transport error hitting search endpoint: {exc!r}")
            return 1
        print(f"HTTP {resp.status_code}")
        _dump_body(resp)
        if resp.status_code == 401 or resp.status_code == 403:
            print("\nFAIL: auth rejected. The agency key/version pairing is wrong.")
            return 1
        if resp.status_code == 404:
            print(
                "\nNOTE: 404 on /locations/search - the real search path/params likely "
                "differ from our best guess. Adjust find_location_by_email accordingly."
            )
        elif 200 <= resp.status_code < 300:
            body = _safe_json(resp)
            if isinstance(body, dict) and "locations" in body:
                print('\nPASS: search contract matches our parse (`{"locations": [...]}`).')
            else:
                print(
                    '\nNOTE: 200 but the envelope is NOT `{"locations": [...]}` - '
                    "update find_location_by_email to the real shape shown above."
                )

    print("\nDone. This was read-only; nothing was created.")
    return 0


def _safe_json(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except ValueError:
        return None


def _dump_body(resp: httpx.Response) -> None:
    body = _safe_json(resp)
    if body is None:
        print(f"(non-JSON body) {resp.text[:500]}")
    else:
        print(json.dumps(body, indent=2)[:1500])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
