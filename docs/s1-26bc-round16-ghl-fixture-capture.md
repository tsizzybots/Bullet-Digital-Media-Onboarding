# S1-26b/c round 16, item 4: capturing a real GHL location-search response

> **What this is:** the capture method and redaction rules behind
> `apps/api/tests/golden/ghl_location_search_response.json`, written for S1-26b/c
> review round 16 on 10/09/2026. It records how that fixture was obtained, what
> was redacted and what was deliberately not, and the reading of each possible
> outcome committed to BEFORE the data was seen. Re-run the curl here if the
> fixture ever needs refreshing.

The reviewer, verbatim:

> can you grab a real GHL location-search response for an existing location and
> paste it into the PR? We've never confirmed the results include the postcode.
> If they don't, the reuse path is inert in production and every client gets
> flagged.

## Can this session make the call? NO.

Checked, not assumed:

```
$ sed -E 's/=.*//' .env | grep -v '^#' | grep -v '^$'
POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_PORT / DATABASE_URL /
DATABASE_SSL_MODE / INNGEST_DEV_PORT / INNGEST_DEV_SERVER_URL /
NEXT_PUBLIC_API_URL / CORS_ALLOW_ORIGINS / SENTRY_DSN / SENTRY_ENVIRONMENT /
NEXT_PUBLIC_SENTRY_DSN / PANDADOC_WEBHOOK_SECRET_UK / PANDADOC_WEBHOOK_SECRET_INT /
PANDADOC_API_KEY_UK / PANDADOC_API_KEY_INT / PANDADOC_API_BASE_URL /
SLACK_WEBHOOK_URL / RECONCILIATION_LOOKBACK_DAYS

$ env | grep -i ghl
(no output)
```

No `GHL_AGENCY_API_KEY` and no `GHL_COMPANY_ID` locally. Both live only in the
Render staging and prod env groups (`config.py:251-268` says so, and
`render.yaml` wires them via `fromGroup`). So the curl below is for Josh, and
the fixture lands in a follow-up commit once the body comes back.

Nothing was invented in the meantime. There is no fixture file in this commit
and no assertion about what the response contains, because no command has been
run that would justify one.

## The curl

One minute, read-only, no side effects. Substitute the two values from the
Render staging env group (`bullet-staging-env`), and any email belonging to a
location that ALREADY EXISTS in the agency (a test location if there is one).

```bash
GHL_AGENCY_API_KEY='<paste from bullet-staging-env>'
GHL_COMPANY_ID='<paste from bullet-staging-env>'
EMAIL='<email of a location that already exists>'

curl -sS -G 'https://services.leadconnectorhq.com/locations/search' \
  -H "Authorization: Bearer ${GHL_AGENCY_API_KEY}" \
  -H 'Version: 2021-07-28' \
  --data-urlencode "companyId=${GHL_COMPANY_ID}" \
  --data-urlencode "email=${EMAIL}" \
  --data-urlencode 'limit=1' \
  | python3 -m json.tool
```

That is the request `HttpGhlClient.find_location_by_email` makes, field for
field: method, path, both headers and all three query params are pinned by
`tests/test_ghl_client.py::test_find_location_by_email_sends_expected_url_and_params`.

If it returns `{"locations": []}` the email has no location and the answer is
not usable; try another. A 404 also means no match, not an error.

## Redaction instructions

**The question is WHICH KEYS EXIST, not what is in them.** So redact values and
keep every key, every nesting level, and every type.

Replace, in place:

| Field | Replace with |
|---|---|
| `name`, `businessName`, any company name | `"REDACTED GYM"` |
| `email`, any address-like string | `"redacted@example.com"` |
| `phone` | `"+44 7700 900000"` (Ofcom drama range, never assignable) |
| `address`, `city`, `state` | `"REDACTED"` |
| `id`, `locationId`, `companyId` | `"REDACTED_ID"` |
| `apiKey`, any token or secret | DELETE the key entirely and say you did |

**Do NOT redact:**

- `postalCode` (or wherever a postcode appears). Replace the value with
  `"E8 1AA"` if you would rather not paste a real one, but the KEY and its
  position must survive verbatim. This is the whole question.
- Any key whose value is `null` or `""`. An absent postcode is the finding; a
  postcode redacted into existence would hide it.
- The nesting. If the postcode sits under a `business` object rather than at the
  top level, that structure is load-bearing (`_location_postcode` reads both).

## What the fixture will be checked for

`_classify_ghl_hit` (`worker/ghl_subaccount.py:810`) judges a hit on four
signals, each read from the search-result item by a helper that looks TOP LEVEL
FIRST and then under a nested `business` object:

| Signal | Read by | Keys tried |
|---|---|---|
| name | `GhlLocation.name` | `name` |
| postcode | `_location_postcode` (`:733`) | `postalCode`, then `business.postalCode` |
| phone | `_location_phone` (`:761`) | `phone`, then `business.phone` |
| address | `_location_address` (`:781`) | `address`, then `business.address` |

The `business`-object fallback exists because the 21/07/2026 live CREATE
response carried one. That is a create response, not a search response, and the
two have never been confirmed to have the same shape. That gap is the reviewer's
point.

## What each outcome means, stated in advance

Committing to the reading before seeing the data, so the fixture cannot be read
to suit us:

**If `postalCode` is present** (top level or under `business`): the reuse path
works as designed and the fixture becomes a golden file with one test asserting
those four keys are reachable, so a GHL response-shape change breaks a test
instead of silently disabling corroboration.

**If `postalCode` is ABSENT: that is a FINDING, disclosed, not fixed this
round.** `_location_postcode` returns `""`, which `_classify_ghl_hit` reads as
`postcode unknown`. Per its own table:

- name agrees + postcode unknown -> `undecidable` -> provision own + FLAG
- name differs + postcode unknown -> `different_business` -> provision own, no flag

So every returning client whose name matches a found location gets a possible-
duplicate flag and its own sub-account, and the GHL reuse leg never fires in
production. The direction is SAFE: it splits rather than merges, which is the
side this whole ticket has been enumerating toward. But the leg is inert, the
flag fires on the normal path, and the module's own comment says a flag that
fires on the normal path is one people learn to ignore.

The reviewer pre-authorised the handling: disclose it in the body, do not change
classifier behaviour on the strength of the fixture in this diff. The remedy, if
it comes to it, is a `GET /locations/{id}` after the search hit, which is a new
call on the hot path and belongs on its own card, not in a merge-ready diff.

**If some OTHER key carries the postcode** (a `postal_code`, a nested `address`
object): also a finding, same disclosure, and the one-line reader change goes on
the card with the fixture as its evidence.
