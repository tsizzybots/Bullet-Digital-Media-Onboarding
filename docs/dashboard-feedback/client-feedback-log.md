# Client Feedback Log

Ongoing record of client feedback, questions, and suggestions, along with our analysis and responses.

---

## FB-001: Kick-off follow-up workflow - human review point + offer pricing scope

**Date:** 24/04/2026
**Last Updated:** 28/04/2026 11:00 NZST
**Source:** Client (Stephen Taylor, Bullet Digital Media)
**Status:** Responded

### Client Request

> Currently, after the kick off call the process is as follows...
>
> - Performance Director (who leads the kick off call) will...
> - Come up with some offer name options (based on the client / brand / whats working currently / historically for similar clients / offers
> - If it's a checkout page (lower ticket offer), they will work out the "total value" and £/$ savings based on the following...
>   - Most relative membership rate to anchor from
>     - i.e. if its £200 a month for unlimited classes (say a crossfit membership) and we are running a 21 day offer we'd run £200/30.4 x 21 to get the daily rate.
>     - or if we are doing a pilates studio and running a 5 class pack - we may anchor to their drop in rate, say £30 per class. If the offer is 5 for £59 we'd run £30x5 to calculate total value then derive % off and £ savings.
>   - Offer Addition 1 - Consultation Value - If theres a consulation as part of the offer (agreed on kick off call) we would add the value of that on to the total value.
>   - Offer Addition 2 - Body Scans - Client may have 1, 2 oe 3 body scans as part of their offer. We get the value of a single body scan in the OB survey and then would add this into the total value calculation prior to determining £ savings / % off.
> - Performance director would then put them into to the Onboarding Survey Google Doc at the bottom and let the Account Manager know its ready to be sent / for them to finalise.
> - They then take the relevant email template (bottom of OB doc) and slap in the info in Gmail to send to the client.
>
> ***The main point here - is currently, there's a pause for human intervention between kick off call and sending that email, as not absolutely everything is agreed or 100% locked in on the kick off call.***
>
> So what I am looking to understand is ultimately...
>
> - Is this currently considered in the scope and the goal from your side is to get AI to do all of the above?
> - or, do we need to put a human intervention point in between the KOC and that email getting sent to the client?
>
> Your Final Question - 1 Email with coditional blocks sounds great.

### Current State Assessment

#### What exists today (in the v3 plan, prior to this feedback):

| Capability | Status | Details |
|-----------|--------|---------|
| AI follow-up email generator | Scoped (Section 3.5) | Prose generation from transcript + knowledge profile; "specialist reviews, adjusts, and sends" |
| Offer pricing calculator | Scoped (Section 3.5) | "75% of monthly anchor + consultation + body_scan × 2" - too narrow vs Bullet's actual method |
| Subscription vs class-pack anchor | Not scoped | Plan only modelled monthly subscription anchor |
| Variable body-scan count (1, 2, 3) | Not scoped | Plan fixed N at 2 |
| Offer-name suggestions | Not scoped | Plan generated prose and pricing only |
| Explicit human review state in dashboard | Implied only | Review pause was in the prose, not a visible workflow state |
| Single-email-with-conditional-blocks (Outstanding Elements replacement) | Scoped (Section 3.10) | Now client-confirmed |

#### What we are adding/refining (rolled into v3.1 of the plan):

1. **Make the human review step explicit** - dashboard exposes `Ready for Performance Director Review` and `Ready for Account Manager to Send` states at Step 4. (Effort: Low.)
2. **Two anchor-rate variants** in the pricing calculator - subscription (`monthly_price / 30.4 × offer_days`) and class-pack (`drop_in_rate × class_count`), switched on a portal field. (Effort: Low.)
3. **Variable body-scan multiplier** N in {1, 2, 3}, sourced from the OB survey. (Effort: Low.)
4. **Offer-name suggestions** - AI produces 2-3 candidate names from client/brand/portal/historical data; PD picks one or writes their own. (Effort: Medium - prompt design + retrieval over historical offers.)
5. **Calculation transparency** - dashboard shows the full working alongside the result so the PD can sanity-check. (Effort: Low.)

### Our Response

> Hi Stephen,
>
> Thanks for the detailed walk-through of the kick-off follow-up - that is exactly the level of specificity we need, and it is a great prompt to make sure the plan calls this out loudly rather than implying it.
>
> Short answer to your two questions:
>
> Yes, all of the work the Performance Director does after the kick-off (offer naming, total-value maths, savings calculation) is in scope and is what the AI generator produces. **And** yes, there is a deliberate human review point between the call and the email going out - exactly the pause you described. The two are not in conflict; they are the same workflow.
>
> Here is how it lands in the system:
>
> 1. The kick-off call ends. The AI generates the draft email, suggests 2-3 offer names (based on the client, brand, portal answers, and what has worked for similar gyms), and works the pricing maths.
> 2. The dashboard flips that client into a `Ready for Performance Director Review` state. Your PD sees the suggested names, the worked numbers (with the calculation visible - e.g. "£200 / 30.4 × 21 = £138.16, +£X consult, +£Y × 2 body scans = £Z total value, £W savings, X% off"), and the full prose.
> 3. PD adjusts anything that was not 100% locked in on the call (offer name, last-minute pricing tweaks, missing details) and confirms.
> 4. The dashboard moves it to `Ready for Account Manager to Send`. AM hits send.
>
> The pause stays. The grunt work goes away.
>
> A couple of refinements we are pulling into the plan from your detail:
>
> - Two anchor-rate variants, not one - subscription (£200 / 30.4 × offer_days) and class-pack (drop-in rate × class count). The earlier draft was too narrow on this.
> - Body scans can be 1, 2 or 3 - we will pull the count from the OB survey and feed it into the calculation, rather than assuming 2.
> - Offer-name suggestions are now explicitly part of the AI output, not just the prose.
> - The `Ready for Review` -> `Ready to Send` workflow becomes a visible state in the dashboard so the team can see, at a glance, what is sitting with the PD and what is sitting with the AM.
>
> We will refresh the v3 plan with these and resend - happy to walk you through it on the next call if helpful.
>
> On the 16-branch replacement: great, locked in. We will build the single conditional template against the live client-assets checklist in the dashboard.
>
> On the API access sheet: thanks for kicking that off - we are in the Sheet and have what we need to start Sprint 1. We will flag any specific gaps platform-by-platform as each sprint hits them, rather than chasing the full list up front.
>
> On Stripe specifically (since you flagged the read/write decisions): we will use a restricted key (not the master secret), scoped to read/write on customers, payment methods, subscriptions, prices, invoices, plus events for webhook verification. You can generate that ahead of Sprint 3 - happy to send a one-page screenshot guide for the Stripe dashboard if useful.
>
> Cheers,
> Tim

### Open Questions (added to plan Section 11)

1. Confirm the **30.4-day month** divisor as Bullet's standard for the subscription anchor (vs 28 or 30).
2. Where do **historical offers** for the offer-name suggester live today (Asana, Drive, PD's head)? Needed to seed the suggester.
3. For **class-pack offers**, is the offer always `N classes for £X` (so total value = drop-in × N), or are there variants (e.g. "10 classes / 30 days")?

### Implementation Scope

- **Effort:** Low-to-Medium overall. Most of the substance was already in Section 3.5; this widens it rather than rebuilding.
- **Sprint:** Stays in Sprint 2 (MVP milestone).
- **Related plan sections:** 3.3 (Dashboard - review/send states added), 3.5 (Follow-up email - rewritten with hand-off, two anchor variants, variable body scans, offer-name suggestions, calc transparency), 3.10 (Outstanding Elements - confirmed), 11 (Open Questions Q2 and Q11 resolved; new Q15-Q17 added).
- **Dependencies:** Sample sales-call recording (already on the pre-development list); access to historical offers for the suggester (new dependency from this feedback).
- **Plan version:** v3 -> v3.1 (28/04/2026).

---
