# Phase 1 Infrastructure Setup

**Project**: Bullet Digital Media x IzzyAgents - Onboarding Process Automation
**Phase**: 1 (Months 1-2)
**Date**: 04/05/2026
**Audience**: Bullet Digital Media (John, Stephen, Chris)
**Source**: `docs/PRD.md` v1.0, `docs/phase-1-plan.md` v3.2

This document lists every third-party service we'll use to build Phase 1. Because the system runs on **your** infrastructure (your accounts, your billing, your data), you sign up for each service and then share access with us at **team@izzyagents.ai**.

The order below is grouped by sprint dependency, so you can register accounts as we approach the sprint that needs them. Items marked **Sprint 1 blocker** must be ready before development starts.

---

## How access sharing works

For every service, the pattern is the same:

1. You create the account (paid for from Bullet's billing).
2. You add **team@izzyagents.ai** as a team member, owner, or collaborator at the role specified.
3. You drop the credentials (or invitation confirmation) into the shared 1Password vault, or send them to us via secure channel (Slack DM, signed email, or Bitwarden Send). Never paste raw credentials into email plain text.

If a service does not support multi-user accounts, you provide us with the API key directly and we store it in our Render environment groups (encrypted at rest).

---

## Section A - New services Bullet must register for

These are the services that don't exist in Bullet's current stack. Each needs a fresh account in Bullet's name.

### A1. Neon (Postgres database)

- **What it is**: A managed Postgres database with branching support (per-pull-request database copies) and the `pgvector` extension for AI semantic search. This is where every client's data, knowledge profile, and platform action history lives.
- **Why we need it**: The system's source of truth. Every dashboard view, every AI agent query, every audit log row reads from here.
- **Sprint**: 1 blocker (Sprint 1).
- **Sign-up**: https://neon.tech -> "Sign up" -> use Bullet's `team@bulletdigitalmedia.com` (or similar shared address).
- **Plan**: **Launch** plan, approximately $19 USD/month. Required for: 7-day point-in-time recovery, more storage than the free tier, and branching that supports our CI workflow. Do not pick the free tier.
- **Setup we do**: Create two projects (`bullet-prod`, `bullet-staging`), enable the `pgvector` extension in both, configure connection pooling.
- **Sharing access**: Neon dashboard -> Settings -> Team -> invite `team@izzyagents.ai` as **Admin** on the Neon organisation.

### A2. Render (application hosting)

- **What it is**: A managed hosting platform where the API, background worker, scheduled jobs, and dashboard run.
- **Why we need it**: Phase 1 deploys four services per environment (api, worker, cron, dashboard) across staging and production - eight services total. Render handles all of that with a single dashboard.
- **Sprint**: 1 blocker (Sprint 1).
- **Sign-up**: https://render.com -> "Get Started" -> use Bullet's shared email.
- **Plan**: **Team** plan ($19 USD/month per seat) so multiple people can administer. Compute is billed per-service - expect roughly $7-25 USD/month per service depending on size, so a realistic Phase 1 monthly budget for hosting is $80-200 USD/month total.
- **Setup we do**: Provision the eight services, configure environment groups (where every API key lives encrypted), set up auto-deploy from `main` branch to staging only (production is manual promote).
- **Sharing access**: Render dashboard -> Workspace settings -> Members -> invite `team@izzyagents.ai` as **Admin**.

### A3. Inngest (workflow orchestration)

- **What it is**: A platform that handles the durable execution of every cross-platform action (e.g. when a PandaDoc agreement is signed, fan out to Slack + Asana + Drive + Stripe + Xero + Timely with retries and per-platform rate limiting).
- **Why we need it**: This replaces the unreliable Zapier + Pabbly chain (47+ steps, partly broken). Inngest gives us idempotency, retries with backoff, dead-letter handling, and a step-by-step replay UI for every workflow run.
- **Sprint**: 1 blocker (Sprint 1).
- **Sign-up**: https://inngest.com -> "Start building free".
- **Plan**: **Free tier** is sufficient to start (covers 50,000 function runs/month). If pilot volume exceeds that, we move to **Basic** plan ($20 USD/month for 200,000 runs).
- **Setup we do**: Create two Inngest environments (`production`, `staging`), provision signing keys, register the worker process from Render.
- **Sharing access**: Inngest dashboard -> Settings -> Team -> invite `team@izzyagents.ai` as **Owner** (Inngest only has Owner / Member; Owner is needed to manage signing keys).

### A4. Cloudflare R2 (object storage)

- **What it is**: An S3-compatible object store. We use it to hold sales-call transcripts, scraped competitor pages, generated email bodies, and other large blobs that don't belong in Postgres.
- **Why we need it**: Postgres is for structured data; R2 is for files. Cheaper than AWS S3 with no egress fees.
- **Sprint**: 1 (transcript capture starts here).
- **Sign-up**: https://dash.cloudflare.com/sign-up. If Bullet already has a Cloudflare account for `bulletdigitalmedia.com` DNS, use that one.
- **Plan**: **Pay-as-you-go**. R2 is free for up to 10 GB storage and 1 million Class A operations/month. Realistic Phase 1 cost: under $5 USD/month.
- **Setup we do**: Create two buckets (`bullet-prod-artefacts`, `bullet-staging-artefacts`), generate access keys, set lifecycle rule (transcripts auto-purge at 12 months).
- **Sharing access**: Cloudflare dashboard -> Manage Account -> Members -> invite `team@izzyagents.ai` as **Administrator** on the account (or scoped to R2 only if Cloudflare is shared with other Bullet teams).

### A5. Sentry (error monitoring)

- **What it is**: Error tracking and performance monitoring for the API and dashboard. When something breaks, Sentry tells us immediately with the full stack trace, scrubbed of customer PII (email, phone, transcript content).
- **Why we need it**: Without Sentry, errors land silently in logs and we hear about them when a client complains. With Sentry, we know within seconds.
- **Sprint**: 1.
- **Sign-up**: https://sentry.io -> "Try Sentry Free".
- **Plan**: **Team** plan, approximately $26 USD/month. Required for: source map upload (so we get readable stack traces from the dashboard), longer event retention, and unlimited projects.
- **Setup we do**: Create four projects (`api-prod`, `api-staging`, `dashboard-prod`, `dashboard-staging`), configure PII scrubbing rules, set up Slack alerting on critical errors.
- **Sharing access**: Sentry -> Settings -> Members -> invite `team@izzyagents.ai` as **Admin**.

### A6. Resend (transactional email)

- **What it is**: An email-sending API for system-generated emails (kick-off follow-up emails to clients, technical-requirements chase emails, dashboard auth confirmations, internal alerts).
- **Why we need it**: We need a reliable way to send templated emails from `onboarding@bulletdigitalmedia.com` (or similar) with full deliverability monitoring (bounces, complaints, opens). This is the assumed default per `docs/openquestions.md` Q-01; if you decide later that emails should send from individual Account Managers' Gmail mailboxes, we'll revise this section.
- **Sprint**: 1 (auth confirmation) and 2 (kick-off email).
- **Sign-up**: https://resend.com -> "Sign up".
- **Plan**: **Pro** plan, $20 USD/month. Required for: 50,000 emails/month, custom domain (`bulletdigitalmedia.com`), and webhook event delivery (bounces, complaints, replies).
- **Setup we do**: Add `bulletdigitalmedia.com` as a sending domain, configure SPF + DKIM + DMARC DNS records (we'll send Chris the exact records to add), set up webhook endpoint for bounce/complaint handling.
- **Action from Bullet**: Once we send the DNS records, Chris (or whoever manages DNS) needs to add them to Cloudflare or wherever `bulletdigitalmedia.com` DNS lives. Without this, no emails send.
- **Sharing access**: Resend -> Team -> invite `team@izzyagents.ai` as **Admin**.

### A7. Anthropic (Claude API)

- **What it is**: The AI provider behind our text-generation model calls - sales call summaries, kick-off email drafting, research agent. (Note: Anthropic has no embeddings API; the knowledge-profile **embeddings** for semantic search use OpenAI `text-embedding-3-small` - see A8.)
- **Why we need it**: Phase 1's AI features run on Claude. Specifically `claude-opus-4-7` for one-shot prompts and the Claude Agent SDK for the Sprint 4 research agent.
- **Sprint**: 1 blocker (Sprint 1 - sales call summaries).
- **Sign-up**: https://console.anthropic.com -> "Sign up".
- **Plan**: **Pay-as-you-go**. No fixed monthly fee; you pay only for tokens consumed. Realistic Phase 1 estimate: $50-200 USD/month depending on call volume. Prompt caching (which we use) cuts the cost of repeated system prompts by approximately 90%.
- **Setup we do**: Generate two API keys (one prod, one staging), enable prompt caching, configure rate-limit handling.
- **Action from Bullet**: Anthropic requires a payment method on file before any tokens can be consumed. Add a credit card to the workspace before Sprint 1 starts.
- **Sharing access**: Anthropic Console -> Settings -> Members -> invite `team@izzyagents.ai` as **Admin**. Alternatively, if you'd prefer to keep the workspace single-user, generate an API key with the **Service Account** label and share via the secure-key channel.

### A8. OpenAI (embeddings + Whisper transcription fallback)

- **What it is**: (1) OpenAI's **embeddings** API (`text-embedding-3-small`, 1536-dim) - turns each client-knowledge field into a vector for semantic search (Anthropic has no embeddings API, so embeddings run on OpenAI). (2) OpenAI's audio-transcription API (Whisper), used only when Zoom or Google Meet's native transcript is unavailable (rare but happens).
- **Why we need it**: Embeddings power the knowledge-profile search (S1-30 onward); Whisper is belt-and-braces so a single missing transcript does not block the AI summary.
- **Sprint**: 1 (embeddings land with S1-30).
- **Sign-up**: https://platform.openai.com -> "Sign up".
- **Plan**: **Pay-as-you-go**. Whisper is approximately $0.006 USD/minute. With Native transcripts as the primary path, expect $5-30 USD/month max. We've configured a soft cap of $50 USD/month with an alert at 80%.
- **Setup we do**: Generate one API key (we use the same one for prod and staging since usage is tiny), set the cost cap.
- **Action from Bullet**: Add a credit card; set a hard usage limit of $100 USD/month as a safety net.
- **Sharing access**: OpenAI Platform -> Settings -> Members -> invite `team@izzyagents.ai` as **Owner**.

### A9. Firecrawl (web scraping)

- **What it is**: A web-scraping API that returns clean markdown from JavaScript-rendered pages. Used by the Sprint 4 research agent to scrape client websites and competitor pages.
- **Why we need it**: Replaces manual research time. The research agent calls Firecrawl per client to summarise their website + the top five competitors before the kick-off call.
- **Sprint**: 4.
- **Sign-up**: https://firecrawl.dev -> "Get Started".
- **Plan**: **Hobby** plan, $16 USD/month (3,000 credits). Sufficient for Phase 1 pilot of 3-5 clients per week. If Phase 2 scales, we move to **Standard** ($83 USD/month for 100,000 credits).
- **Setup we do**: Generate one API key, configure scraping defaults (markdown output, JS rendering on).
- **Sharing access**: Firecrawl -> Team -> invite `team@izzyagents.ai` as **Admin**.

---

## Section B - Existing services where Bullet needs to add IzzyAgents access

These services Bullet already has accounts for. We just need access to integrate against them.

### B1. HubSpot

- **What we need**: API access to read contact data and (optionally) mirror onboarding stage transitions back to deals.
- **Sprint**: 1 blocker (Sprint 1).
- **Action**: HubSpot -> Settings -> Integrations -> Private Apps -> "Create a private app" called `IzzyAgents Onboarding Automation`. Grant scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.objects.deals.read`, `crm.objects.deals.write`, plus webhook subscriptions. Send the access token via the secure channel.
- **Note**: Chris is already chasing John for this (per 21/04/2026 meeting). No new sign-up needed.

### B2. PandaDoc

- **What we need**: API token + webhook subscription for `document.completed` events. This is the trigger for everything else.
- **Sprint**: 1 blocker (Sprint 1).
- **Action**: PandaDoc -> Settings -> Integrations -> API & Developer Dashboard -> create an API key. Then: Settings -> Webhooks -> add a webhook for `document_state_changed -> document.completed` pointing to the URL we'll provide once Render staging is live.
- **Note**: Chris is already chasing John for this (per 21/04/2026 meeting).

### B3. GoHighLevel

- **What we need**: Agency-level API key (for sub-account creation) plus per-sub-account OAuth (for portal webhook + workflow triggers). Workflow IDs to trigger (post-signing portal link, survey reminders).
- **Sprint**: 1 blocker (Sprint 1) - this retires Pabbly.
- **Action**: GHL -> Agency Settings -> API Keys -> generate the agency-level key. We'll also need the IDs of the existing workflows we're keeping (post-signing portal link, survey reminder cadence).
- **Note**: This is the work that retires the Pabbly middleman entirely. Without the agency-level API access, we can't create sub-accounts directly.

### B4. Asana

- **What we need**: A service-account personal access token + the IDs of: the `Bullet Clients Status` project template, the finance task template, and any per-service-tier task templates.
- **Sprint**: 2.
- **Action**: Create a dedicated Asana user (e.g. `automation@bulletdigitalmedia.com`) so the audit log shows actions as that bot user, not as a real person. Then: My Settings -> Apps -> Manage Developer Apps -> create a Personal Access Token. Send the token + the project/task template IDs.

### B5. Stripe

- **What we need**: A **restricted API key** (not the full secret key) with scoped permissions for customer + payment method + subscription management. Both UK and International account access.
- **Sprint**: 3 (financial integrations).
- **Action**: Stephen has already received the restricted-key brief on 30/04/2026 (file: `emails/Stripe Restricted Key Setup.html`). Follow that brief to provision the keys for both UK and International accounts.
- **Note**: We must use restricted keys, not full secret keys. The restricted scopes are documented in the brief.

### B6. Xero

- **What we need**: OAuth 2.0 connection (with offline access for token refresh). Plus the chart-of-accounts IDs and tracking categories per legal entity (UK vs International).
- **Sprint**: 3.
- **Action**: Xero -> Apps -> Connected Apps -> we initiate the OAuth flow from our app once it's deployed; you confirm the connection from your Xero admin. We'll send a one-page summary asking which chart-of-accounts and tracking-category IDs map to UK vs International (this is plan-section-11 question Q-3, still open).

### B7. Timely

- **What we need**: API token. We'll create the client + project per signing event with the budget = `monthly_fee_usd / 100` hours.
- **Sprint**: 3.
- **Action**: Timely -> Settings -> Developer Tools -> generate an API token. Send via the secure channel.

### B8. Slack

- **What we need**: Incoming webhook URL for `#bullet_inbound_clients` (and any other channels you want notifications routed to).
- **Sprint**: 2.
- **Action**: Slack -> the channel -> Channel settings -> Integrations -> Add an app -> "Incoming Webhooks" -> generate the webhook URL. Send the URL via the secure channel. We can add more channels (per-platform alert channels) the same way later.
- **Note**: Phase 1 is one-way notifications only (no bot user, no slash commands). Confirmed at 21/04/2026 meeting.

### B9. Google Workspace (Sheets, Docs, Drive, Calendar, Gmail)

- **What we need**: A **service account** with **domain-wide delegation** so we can write to Sheets, Drive, Docs, and read Calendar events on behalf of the Bullet workspace.
- **Sprint**: 2.
- **Action**: Google Cloud Console -> create a new project called `bullet-onboarding-automation` -> Enable APIs (Sheets, Docs, Drive, Calendar, Gmail) -> IAM & Admin -> Service Accounts -> create one called `onboarding-bot` -> generate a JSON key. Then: Google Workspace Admin -> Security -> Domain-wide Delegation -> add the service account's client ID with the scopes we'll specify.
- **Action from Bullet**: This needs a Workspace **super-admin** to enable domain-wide delegation. Identify who has that role (likely John or a designated admin) so the right person sees the request when we send the scopes list.

### B10. Meta Business Manager

- **What we need**: System User access token + Business Manager scopes (read-only ad-account access, audience-size queries).
- **Sprint**: 4 (research agent).
- **Action**: Meta Business Manager -> Business Settings -> Users -> System Users -> add a new system user called `IzzyAgents Research Bot` with Admin access on the relevant ad accounts. Generate a long-lived token with `ads_read` + `business_management` scopes.

### B11. Zoom and/or Google Meet (transcript capture)

- **What we need**: Webhook for `recording.completed` (Zoom) or Drive notifications for completed Meet recordings.
- **Sprint**: 1.
- **Action**:
  - **Zoom**: Marketplace -> Develop -> Build App -> Webhook-only app -> subscribe to `recording.completed`. Send the webhook secret.
  - **Google Meet**: Recordings drop into Drive automatically; we monitor via the Drive API (already covered by B9 service account).
- **Note**: Stephen mentioned the Zoom-to-Meet migration is in progress. Tell us which provider is live for sales calls vs kick-off calls so we wire the right webhook from day one.

### B12. GitHub

- **What we need**: We already have the repo at `tsizzybots/Bullet-Digital-Media-Onboarding`. No action from Bullet beyond confirming the IzzyAgents engineering team has push access (they do, since IzzyAgents owns the org).
- **Sprint**: 1 blocker.
- **Action from Bullet**: None. Listed here for completeness.

---

## Section C - Estimated total monthly infrastructure cost (Phase 1 pilot scale)

For pilot scale (3-5 active clients/week, full team using the dashboard daily):

| Service | Plan | Monthly cost (USD) |
|---------|------|--------------------|
| Neon | Launch | $19 |
| Render | Team + 4 prod services + 4 staging services | $80 - $200 |
| Inngest | Free | $0 |
| Cloudflare R2 | Pay-as-you-go | < $5 |
| Sentry | Team | $26 |
| Resend | Pro | $20 |
| Anthropic | Pay-as-you-go | $50 - $200 |
| OpenAI Whisper | Pay-as-you-go (capped at $50) | $5 - $50 |
| Firecrawl | Hobby (Sprint 4 onwards) | $16 |
| **Total estimate** | | **$220 - $540 USD/month** |

Costs scale roughly linearly with pilot client volume. At Phase 2 production scale (full Bullet client base of 90+), expect $400-900 USD/month, dominated by Render compute and Anthropic token usage.

This does **not** include the existing services Bullet already pays for (HubSpot, PandaDoc, GHL, Asana, Stripe, Xero, Timely, Slack, Google Workspace, Meta, Canva, Loom).

---

## Section D - What Bullet needs to do, in order

This is the action checklist. Tick each item as you complete it.

**Before Sprint 1 (week 1):**

- [ ] Create Neon account + Launch plan; invite `team@izzyagents.ai` as Admin
- [ ] Create Render account + Team plan; invite `team@izzyagents.ai` as Admin
- [ ] Create Inngest account; invite `team@izzyagents.ai` as Owner
- [ ] Create or confirm Cloudflare account; invite `team@izzyagents.ai` as Administrator
- [ ] Create Sentry account + Team plan; invite `team@izzyagents.ai` as Admin
- [ ] Create Resend account + Pro plan; invite `team@izzyagents.ai` as Admin
- [ ] Add `bulletdigitalmedia.com` SPF/DKIM/DMARC DNS records (records to follow from us)
- [ ] Create Anthropic Console account + add credit card; invite `team@izzyagents.ai` as Admin
- [ ] Create OpenAI Platform account + add credit card + set $100 USD usage cap; invite `team@izzyagents.ai` as Owner
- [ ] HubSpot Private App created and access token shared
- [ ] PandaDoc API key + webhook configured; access token shared
- [ ] GHL agency-level API key + workflow IDs shared
- [ ] Zoom (or confirmation Google Meet is the live provider) webhook configured
- [ ] One sample sales-call recording sent to us so we can validate the transcript pipeline

**Before Sprint 2 (week 3):**

- [ ] Asana service-account user created + personal access token shared + project/task template IDs shared
- [ ] Slack incoming webhook for `#bullet_inbound_clients` shared
- [ ] Google Cloud project + service account + domain-wide delegation enabled

**Before Sprint 3 (week 5):**

- [ ] Stripe restricted keys provisioned per the 30/04/2026 brief (UK + International)
- [ ] Xero OAuth ready to connect; chart-of-accounts mapping confirmed (open question Q-3 in plan)
- [ ] Timely API token shared

**Before Sprint 4 (week 7):**

- [ ] Firecrawl account + Hobby plan; invite `team@izzyagents.ai` as Admin
- [ ] Meta Business Manager system user created + token shared

---

## Section E - Security and credential handling

- We never paste raw API keys into plain-text email or chat messages.
- Acceptable secure channels for sharing credentials: 1Password shared vault (preferred), Bitwarden Send (single-use), signed/encrypted email, or Slack DM with auto-delete enabled on the message.
- Once we receive a credential, it goes into Render's encrypted environment groups, never into the codebase, never into a plain file, never into a Git commit. Pre-commit hooks block accidental key commits.
- Rotation cadence for Phase 1: every 6 months, or immediately on any team change at IzzyAgents. We'll prompt you when rotation is due.
- If you ever suspect a key is leaked, contact us immediately (team@izzyagents.ai) and we'll rotate within 24 hours.

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
