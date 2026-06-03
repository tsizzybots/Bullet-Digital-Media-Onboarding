# Services & API Access - Phase 1

**Last updated**: 25/05/2026

---

## Infrastructure & Hosting

| Service | Purpose |
|---------|---------|
| Render.com | Hosting - web service, worker, cron jobs, Redis (provisioned directly via Render) |
| Neon PostgreSQL | Primary database |

---

## AI & Processing

| Service | Purpose |
|---------|---------|
| Anthropic (Claude API) | Sales summaries, kick-off email drafting, research synthesis |
| OpenAI (Whisper) | Transcript generation from Zoom/Google Meet recordings |
| Firecrawl | Client website scraping, competitor research (Sprint 4) |
| Inngest | Background job orchestration |
| Cloudflare R2 | Object storage for transcript audio, scraped HTML, generated email bodies (S3-compatible; not in Postgres) |

---

## Platform Integrations

| Service | Sprint | Purpose |
|---------|--------|---------|
| HubSpot | Sprint 1 | Deal/contact data, signing trigger source |
| PandaDoc | Sprint 1 | Signing webhook + polling reconciliation |
| GoHighLevel | Sprint 1 | Sub-account creation, portal webhooks, conditional email triggers |
| Slack | Sprint 2 | New-client notifications, escalation alerts |
| Asana | Sprint 2 | Onboarding task list creation, finance task date sync |
| Google Workspace | Sprint 2 | Sheets, Docs, Drive, Calendar, Gmail (service account + domain-wide delegation) |
| Stripe | Sprint 3 | Payment method capture, subscription activation |
| Xero | Sprint 3 | Client financial record (UK vs International routing) |
| Timely | Sprint 3 | Client + project auto-creation with time budget |
| Leadsy | Sprint 3 | One-click Facebook asset access link generation |
| Resend | Sprint 2+ | All system-sent outbound emails |
| Meta Business Manager | Sprint 4 | Audience sizing for research agent |

---

## Access Status

| Service | Status | Notes |
|---------|--------|-------|
| HubSpot API | Chasing | Chris chasing (21/04/2026) |
| PandaDoc webhook + token | Chasing | Chris chasing (21/04/2026) |
| Claude API key | Committed | John committed |
| OpenAI (Whisper) | Not started | |
| GoHighLevel API | Not started | Sub-account + workflow IDs needed |
| Google Workspace service account | Not started | Requires domain-wide delegation |
| Slack webhook | Not started | `bullet_inbound_clients` channel + others |
| Asana token | Not started | Token + project and finance task template IDs |
| Cloudflare R2 | Not started | Bullet may already have a Cloudflare account for DNS - check first |
| Stripe restricted key | Brief sent | Dispatched to Stephen (30/04/2026) |
| Xero OAuth | Not started | UK vs International routing rules needed |
| Timely API | Not started | Confirm `monthly_fee / 100` time budget calc |
| Leadsy credentials | Not started | |
| Meta Marketing API | Not started | Sprint 4 - not blocking yet |

---

## Sprint Blockers

**Sprint 1** (immediate): HubSpot, PandaDoc, GoHighLevel, Cloudflare R2, sample sales call recording
**Sprint 2**: Google Workspace service account, Slack webhook, Asana token
**Sprint 3**: Stripe key, Xero OAuth, Timely API, Leadsy credentials
**Sprint 4**: Meta Marketing API
