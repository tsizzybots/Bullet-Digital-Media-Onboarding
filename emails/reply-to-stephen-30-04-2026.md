# Reply to Stephen Taylor - 30/04/2026

**To**: Stephen Taylor <stephen@bulletdigitalmedia.com>
**Cc**: Chris, John, Joshua
**From**: team@izzyagents.ai
**Subject**: Re: [previous thread subject - Phase 1 plan / kick-off follow-up email + Stripe one-pager]
**Attachments**: `Stripe Restricted Key Setup.pdf`

---

Hi Steve,

Thanks - both noted, and the second point is a useful clarification. Pulling it straight into the plan.

**Stripe**: one-pager attached. It covers the restricted-key scopes (customers, payment methods, subscriptions, prices, invoices read/write + events read), exactly what to leave on `None` so the long permissions list does not slow you down, the naming convention we will use, and how to share the key back to us securely. Webhook signing secret is a separate value we will request when we register the endpoint in Sprint 3 - flagged in the doc.

**Pricing / discount calculation scope**: confirmed and locked in.

- Low-ticket / checkout campaigns (large group class facilities) - full pricing-maths block: subscription or class-pack anchor + 1, 2 or 3 body scans + optional consultation, with total value, savings, and % off shown to the PD for sanity-check.
- Higher-ticket / consultation-booking campaigns (smaller, more expensive clients) - prose-only confirmation of the agreed offer, ad budget, creative plan, setup timeline, and the consultation booking mechanic. No anchor maths, no savings/% off.

The generator branches at the top on a `campaign_flow_type` flag (`low_ticket_checkout` vs `high_ticket_consultation`) that your PD sets on the kick-off call with one click in the dashboard. We pre-fill a default suggestion from the OB-survey data (group size + consultation price) so the PD is confirming rather than starting from scratch.

We have refreshed the plan to v3.2 reflecting this - happy to walk through the diff on next week's call if useful, otherwise it is just the kick-off email section that has changed.

Cheers,
Tim

---

*Prepared 30/04/2026 by IzzyAgents | AI Solutions Consultancy*
