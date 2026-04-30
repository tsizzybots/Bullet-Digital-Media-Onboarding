# Phase 1 Proposal: Onboarding Process Automation

**Prepared for**: Bullet Digital Media
**Prepared by**: IzzyAgents
**Date**: 30/04/2026 (v3.2 - refreshed after Stephen's 30/04/2026 reply: kick-off email pricing block now scoped to low-ticket / checkout campaigns only; consultation-booking clients get a prose-only confirmation)

---

## The Opportunity

Your current onboarding process takes around 3 weeks from sales call to campaign go-live, spanning roughly a dozen platforms with manual handoffs between each step.

Phase 1 compresses this to **a single day** - without sacrificing quality - by automating the movement of information between your tools and using AI to handle the repetitive drafting and summarising work your team does today.

---

## What You Get

### 1. One-Click Client Onboarding

The moment a client signs their agreement, the system automatically:

- Notifies the right team in Slack
- Creates the Asana task list from your onboarding template
- Sets up the client's Google Drive folders and Sheet row
- Creates records in Stripe, Xero, and Timely
- Sends the technical requirements email (with your existing conditional logic preserved)
- Books the kick-off call in Google Calendar

No more manual copying between platforms. No more "did someone set up their Drive folder?" No more missed steps.

### 2. Live Onboarding Dashboard (The Central Source of Truth)

A single screen showing every client in your pipeline:

- Which step they are on (1 to 6)
- How long they have been in each step
- Direct links into every platform (HubSpot, GHL, Asana, Drive, Stripe, Xero, Timely, Slack, Calendar)
- Any failed or pending actions, flagged loudly so nothing slips
- A live checklist of required assets per client (headshots, brand guidelines, ad account access, registration docs) - replacing the current 16-branch tech follow-up workflow

Critically, the dashboard is backed by a database that is the single source of truth for every client. Google Sheets, Google Docs, and GoHighLevel custom fields become optional mirrors rather than the primary record. This is the foundation - not a later polish item - because everything else (AI sales summaries, the knowledge profile, the kick-off email generator, the research agent, and every future AI feature) reads from and writes to it.

The dashboard goes live in Sprint 1, alongside the AI sales summaries.

### 3. AI Sales Call Summaries

Every sales call transcript is automatically turned into a structured summary - business type, goals, budget, red flags, next steps - and added to the client's record. Your team sees this in the dashboard immediately after each call, replacing the current "paste transcript into Claude" workflow.

### 4. AI Kick-Off Follow-Up Emails

After every kick-off call, AI drafts the detailed follow-up email your Performance Director currently writes by hand. The agreed offer, campaign structure, budget, creative requirements, timeline - all pulled together from the call transcript and the client's onboarding data.

**The pause you have today stays.** AI does the heavy lifting; the human review step does not go away.

How it lands in the dashboard:

1. AI generates the draft email and (for low-ticket / checkout campaigns) suggests 2-3 offer-name options and works the pricing maths.
2. The client appears in a `Ready for Performance Director Review` state. Your PD sees the suggested names, the worked numbers (with the calculation visible, not a black box) or the prose draft, and the full email body.
3. PD adjusts anything that was not 100% locked in on the call (offer name, last-minute pricing tweaks, missing details) and confirms.
4. The client moves to `Ready for Account Manager to Send`. AM hits send.

**Two variants, matched to how Bullet actually sells** (confirmed on 30/04/2026):

- **Low-ticket / checkout campaigns** (large group class facilities) - full pricing-maths block: subscription anchor (`monthly_price / 30.4 × offer_days`) or class-pack anchor (`drop_in_rate × class_count`), plus 1, 2 or 3 body scans, plus an optional consultation. The email shows total value, savings, and % off, with the working visible to the PD for sanity-checking.
- **Higher-ticket / consultation-booking campaigns** (smaller, more expensive clients) - the email confirms the agreed offer, ad budget, creative plan, setup timeline and consultation booking mechanics. **No pricing-maths block.** That framing is not how those campaigns are sold.

Your Performance Director sets the campaign type (`low-ticket` vs `high-ticket`) on the kick-off call with one click in the dashboard, and the email generator picks the right variant. The dashboard also pre-fills a default suggestion from the OB-survey data so the PD has something to confirm rather than start from scratch.

### 5. Pre-Kick-Off Client Research

Before each kick-off call, the system automatically prepares:

- A summary of the client's website (services, pricing, USPs, offers)
- Competing gyms and fitness studios in their area
- Meta audience size for their region
- Initial offer angle suggestions

Your campaign manager walks into kick-off calls with the groundwork already done.

### 6. Per-Client Knowledge Profile

Every piece of information gathered during onboarding - sales call notes, agreement details, portal answers, research, kick-off outcomes - accumulates into a single client profile. Anyone on the team can open a client's record and see everything known about them in one place.

This also becomes the foundation for future AI features (Steve AI, client-facing bot, etc.) in later phases.

---

## Your Onboarding Process - Before vs After

| Step | Today | After Phase 1 |
|------|-------|---------------|
| Sales call | Manual transcript paste into Claude for summary | Auto-transcribed, auto-summarised, visible in dashboard |
| Agreement signed | Trigger partial manual actions across platforms | Single event fans out to all platforms automatically |
| Portal & intake | Client fills form, team manually chases gaps | Dashboard shows progress in real-time |
| Kick-off prep | Manual research across website, competitors, Meta | Research brief ready before the call |
| Kick-off follow-up | Specialist writes detailed email from scratch | AI drafts email, specialist reviews and sends |
| Payment activation | Manual Stripe trigger after email sign-off | Automatic trigger once follow-up is confirmed |
| Campaign build | ~14 days manual build | ~30 seconds via GHL's new AI builder (already in your toolkit) |

**Result**: 3 weeks compressed toward 1 day, with fewer gaps, less rework, and live visibility across the team.

---

## Timeline

An 8-week build delivered in four 2-week sprints, with a working MVP demo at the 4-week mark.

| Sprint | Weeks | What You See |
|--------|-------|--------------|
| Sprint 1 | Weeks 1-2 | AI sales call summaries live in the dashboard for every signed client |
| Sprint 2 | Weeks 3-4 | **MVP demo**: signed agreement triggers all non-financial setup + AI kick-off emails |
| Sprint 3 | Weeks 5-6 | Stripe, Xero, Timely, and Gmail/GHL email automation added |
| Sprint 4 | Weeks 7-8 | Research agent live + pilot with 3 to 5 real new clients |

---

## Decisions Confirmed on the 21/04/2026 Call

Three previously open decisions are now settled:

1. **Agreement platform**: Staying on PandaDoc (HubSpot does not offer the document handling you need, and PandaDoc is already natively integrated with HubSpot). We build directly against the PandaDoc signing webhook with no mid-project switch planned.

2. **Client onboarding portal**: The current GoHighLevel portal stays for Phase 1. The custom-branded portal is scoped as a Phase 2 engagement deliverable, giving you a polished, customer-facing product experience once the backend it sits on top of is stable and the internal time saving is locked in.

3. **Process documentation**: Steve's Loom walkthroughs (OB-Phase-1 and OB-Phase-2) gave us the full mechanics of today's Zapier chains, the Pabbly workaround for GoHighLevel sub-accounts, the 16-branch tech follow-up workflow, and the Monday manual sync Steve runs to keep Asana finance dates in line. We plan to replace those with a database-driven equivalent that removes the manual steps and the brittle branching.

## What We Still Need From You

1. **API access and credentials** (Chris is already chasing): PandaDoc, HubSpot, GoHighLevel, Stripe, Xero, Timely, Asana, Google Workspace, Slack, Meta Marketing. Each sprint needs its own subset - full list in the internal plan.

2. **A sample sales-call recording**: So we can validate the transcript pipeline in Sprint 1.

3. **A few small confirmations from Steve** on the remaining mechanics - Asana template IDs, Xero chart-of-accounts routing for UK vs International, and a handful of edge-case decisions (returning clients signing for a second site, the `SaaS Mode` column in the status sheet, how sales handover notes reach the kickoff call). We have the full list ready to walk through on the next call.

---

## Success Criteria

### At the MVP milestone (end of Week 4)

- AI-generated sales summaries visible in the dashboard for every client
- A signed agreement triggers Slack, Asana, Sheets, Drive, and Calendar setup with zero manual intervention
- AI drafts kick-off follow-up emails from call transcripts
- Live dashboard view of every active client's status
- Partial failures are surfaced and recoverable - nothing fails silently

### At Phase 1 completion (end of Week 8)

- Full financial automation live (Stripe, Xero, Timely)
- Stripe subscriptions activate automatically after kick-off sign-off
- Research brief ready before every kick-off call
- Per-client knowledge profile populated from all onboarding sources
- 3 to 5 pilot clients taken through the automated flow end-to-end
- Measured time saving vs your current 2-week agreement-to-go-live baseline

---

## How This Sets Up Future Phases

Phase 1 builds the two things every future phase needs: a clean database that holds everything known about every client, and a dashboard that is the single source of truth for the team. Everything else on your roadmap sits on top of that:

- **Phase 2**: Custom-branded client onboarding portal (the "Perplexity for gyms and fitness" front-end experience John described on 21/04/2026), plus the internal knowledge bank and client-facing Telegram bot
- **Phase 3**: "Steve AI" digital twin queries the same database to answer team questions in your voice
- **Phase 4**: Productised AI tools and the "AI agent conveyor belt" - individual agents for sales, onboarding, build, reporting, all coordinated by one orchestrator agent - reuse the same data model

The long-term vision discussed on the 21/04/2026 call - an agnostic interface that uses the best available tools in the background so you can swap Meta, GoHighLevel, and others without your clients ever feeling the churn - is architected from the ground up. Phase 1 is the foundation; every subsequent phase inherits it.

---

## Next Steps

1. Walk through this updated plan together and confirm scope
2. Book a follow-up call with Steve to close out the remaining operational questions
3. Continue API access and credential gathering (Chris already in motion after the 21/04/2026 call)
4. Share a sample sales-call recording so we can validate the transcript pipeline in Sprint 1
5. Identify 3 to 5 pilot clients for Sprint 4

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
