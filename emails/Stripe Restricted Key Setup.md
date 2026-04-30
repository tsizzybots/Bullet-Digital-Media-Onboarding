# Stripe Restricted Key Setup

**Prepared for**: Bullet Digital Media (Stephen Taylor)

**Prepared by**: IzzyAgents

**Date**: 30/04/2026

**For**: Phase 1 - Sprint 3 financial integrations (Stripe, Xero, Timely)

---

## Why a restricted key, not the master secret

Restricted keys are scoped to only the resources we need, can be revoked or rotated independently, and give us a clean audit trail per integration. The master secret key has full account access, which is more risk than this integration warrants.

## Resources and permissions to grant

Toggle each of these to the listed permission inside the restricted-key creation screen. Leave **everything else on `None`**.

| Resource | Permission | Why we need it |
|---|---|---|
| `Customers` | Read + Write | Create the Stripe customer when an agreement is signed; update contact details if they change |
| `Payment methods` | Read + Write | Attach the card captured in PandaDoc to the customer; update if the client adds a new card |
| `Subscriptions` | Read + Write | Create the recurring subscription after kick-off sign-off; pause / cancel if the client churns |
| `Prices` | Read + Write | Look up existing pricing tiers; create one-off prices for bespoke offers when needed |
| `Invoices` | Read + Write | Issue and read invoices for retainer billing and proration adjustments |
| `Events` | Read | Verify webhook payloads (subscription updated, payment succeeded, payment failed) and reconcile state |

## What you can leave on `None`

You will see a long list of resources in the create-key screen. The following are **not** required for Phase 1, so leaving them on `None` is the right call:

`Connect`, `Identity`, `Issuing`, `Terminal`, `Treasury`, `Reporting`, `Files`, `Tax`, `Radar`, `Sigma`, `Webhook endpoints` (read-only access to webhook configuration is not needed - we register endpoints from our side).

## Step-by-step setup

1. Stripe Dashboard -> **Developers** -> **API keys** -> **Create restricted key**.
2. Name the key `IzzyAgents_Bullet_Phase1_RW_v1`. (Naming convention: `<vendor>_<engagement>_<phase>_<scope>_<version>`. Lets you rotate without breaking integrations.)
3. Toggle each resource above to its listed permission. Confirm everything else is on `None`.
4. Click **Create key**. Stripe will reveal the key value **once** - copy it immediately.
5. Store the key in your password manager (1Password, Bitwarden, or your team's standard) **before** closing the modal. Stripe will not show it again.
6. Share with IzzyAgents via a secure channel - your password manager's secure share, a one-time-secret link, or a shared vault item. **Never paste the key into plain email or Slack.**

## Rotation and revocation

Rotate every 12 months as standard, and immediately if it is suspected of leaking (e.g. accidentally committed to a repo, shared in a screenshot, used by a departing team member). Rotation is the same flow: create a new key, hand it over, confirm the integration is using it, then click **Roll** on the old key in the Stripe dashboard. Revocation is one click on the same screen if the key needs to be killed instantly.

## Webhook signing secret (separate value)

The webhook signing secret is **not** the same as the restricted API key. We register the webhook endpoint from our side in Sprint 3, at which point Stripe gives us a `whsec_...` value. We will request that one alongside the API key when Sprint 3 starts, but it is captured at endpoint-registration time, not when the API key is created.

## What we need from you

- The restricted key value, shared via a secure channel.
- Confirmation of which Stripe account / mode the key is for (Live vs Test). Sprint 3 wants both - we develop against Test mode and switch to Live for the pilot.
