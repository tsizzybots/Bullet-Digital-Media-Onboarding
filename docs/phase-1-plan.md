# Bullet Digital Media — Phase 1 Architecture Plan

**Prepared by**: IzzyAgents Technical Team
**Date**: 23 March 2026
**Status**: Draft — For Client Review

---

## 1. Overview

Phase 1 delivers an AI-powered **internal client knowledge bank** for Bullet Digital Media's team, followed by a **client-facing WhatsApp AI agent** that answers campaign performance questions. The system ingests client documents, pulls live Meta campaign data, and uses AI to provide instant, accurate answers — with strict data isolation between all ~100 clients.

### Phase 1 Goals
- Staff can query any client's information instantly (documents, campaign data, history)
- Faster client query responses, smoother handovers, holiday cover
- WhatsApp channel for clients to ask campaign performance questions directly
- Human escalation for complex, sensitive, or uncertain queries
- Strict data isolation — no client ever sees another client's data

### What Phase 1 Does NOT Include
- Strategy recommendations or autonomous strategic advice (escalated to humans)
- "Steve AI" digital twin (Phase 2)
- Productised AI platform (Phase 3)

---

## 2. Technology Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| **Database** | Neon PostgreSQL + pgvector | Managed serverless Postgres with native vector search for AI retrieval |
| **AI Retrieval** | LlamaIndex | Purpose-built for document RAG, 40% faster retrieval than alternatives, native Neon/pgvector support |
| **AI Model** | Claude (Anthropic) | Primary AI for understanding questions and generating responses |
| **Embeddings** | Voyage 4 (Anthropic's recommended partner) | Best-in-class accuracy for converting documents into searchable vectors |
| **Backend API** | FastAPI (Python) | High-performance async API for handling WhatsApp webhooks and data processing |
| **Admin Dashboard** | Next.js + Tailwind CSS + Framer Motion | Staff-facing web interface with utility-first styling and polished animations |
| **Messaging** | WhatsApp Cloud API | Confirmed communication channel for client-facing AI |
| **Caching** | Redis | Conversation history, message deduplication, performance caching |
| **Background Jobs** | Celery | Scheduled Meta API polling, document processing |
| **Hosting** | Render.com | Web services, background workers, deployment |

---

## 3. System Architecture

```
                              EXTERNAL SERVICES
    ┌──────────────────┐  ┌───────────────────┐  ┌──────────────────┐
    │ WhatsApp Cloud   │  │ Meta Marketing    │  │ Claude AI        │
    │ API              │  │ API               │  │ + Voyage AI      │
    └────────┬─────────┘  └─────────┬─────────┘  └────────┬─────────┘
             │                      │                      │
             v                      v                      v
    ┌────────┴─────────┐  ┌─────────┴─────────┐  ┌────────┴─────────┐
    │ Webhook Handler  │  │ Campaign Poller   │  │ RAG Engine       │
    │ (FastAPI)        │  │ (Background)      │  │ (LlamaIndex)     │
    │ • Security       │  │ • Scheduled pulls │  │ • Document search│
    │ • Deduplication  │  │ • Rate management │  │ • Relevance rank │
    │ • Client routing │  │ • Data caching    │  │ • Context build  │
    └────────┬─────────┘  └─────────┬─────────┘  └────────┬─────────┘
             │                      │                      │
             └──────────┬───────────┴──────────────────────┘
                        v
             ┌──────────┴──────────┐
             │   Core API Layer    │
             │  • Data isolation   │
             │  • Access control   │
             │  • Audit logging    │
             └──────────┬──────────┘
                        │
          ┌─────────────┼─────────────┐
          v             v             v
    ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
    │ Database  │ │ Cache     │ │ Escalation│
    │ (Neon)    │ │ (Redis)   │ │ Manager   │
    │ • Client  │ │ • Convo   │ │ • Queue   │
    │   data    │ │   history │ │ • Notify  │
    │ • Vectors │ │ • Dedup   │ │ • Resolve │
    └───────────┘ └───────────┘ └───────────┘

    ┌──────────────────────────┐
    │ Admin Dashboard (Web)    │
    │ • Client management      │
    │ • Phone number mapping   │
    │ • Document management    │
    │ • Escalation queue       │
    │ • Campaign metrics view  │
    │ • Internal AI query tool │
    └──────────────────────────┘
```

---

## 4. How It Works

### 4.1 Client Setup (Via Admin Dashboard)

1. Staff creates a new client in the admin dashboard
2. Staff adds the client's Meta ad account ID(s)
3. Staff registers the client's WhatsApp phone number(s)
4. Staff uploads relevant documents (onboarding docs, strategy briefs, notes)
5. System automatically processes documents and begins pulling campaign data

### 4.2 Internal Knowledge Bank (Staff Query)

1. Staff selects a client in the admin dashboard
2. Staff types a question (e.g., "What's this client's current CPC?")
3. System searches the client's documents + campaign data
4. AI generates an answer with source citations and data freshness timestamps
5. Staff uses this to respond to the client faster

### 4.3 WhatsApp Client Query (Client-Facing)

1. Client sends a WhatsApp message (e.g., "How much have we spent this month?")
2. System identifies the client by their registered phone number
3. System retrieves only that client's documents and campaign data
4. AI generates a response with clear data freshness indicators
5. If the AI is uncertain or the topic is sensitive, it escalates to a team member
6. Team member reviews and responds via the admin dashboard

### 4.4 Campaign Data Pipeline

The system automatically polls Meta's Marketing API on a schedule:

| Data Type | Refresh Rate | Notes |
|-----------|-------------|-------|
| Spend, impressions, clicks, CPC, CPM | Every 60 minutes | Near real-time |
| Budget and daily pacing | Every 30 minutes | Critical for spend questions |
| Conversions, ROAS, leads | Every 4-6 hours | Meta reports these with a 1-3 day lag |
| Campaign status changes | Every 60 minutes | ACTIVE/PAUSED updates |
| Historical backfill | On client setup | Last 90 days of data |

**Every response that includes campaign metrics will state when the data was last updated**, so clients always know how fresh the numbers are.

---

## 5. Data Isolation & Security

### Why This Matters

If a client were to receive answers about another client's campaigns, this would be a catastrophic failure. The entire system is designed around preventing this.

### How We Prevent Cross-Client Data Leakage

| Layer | Protection |
|-------|-----------|
| **Database** | PostgreSQL Row-Level Security (RLS) — the database itself enforces that queries can only return data for the current client, regardless of application logic |
| **Phone Number Mapping** | Each WhatsApp phone number is mapped to exactly one client. This mapping is globally unique — the same number cannot belong to two clients |
| **AI Retrieval** | Every document search and data query includes a mandatory client filter. The AI only ever sees one client's data at a time |
| **Audit Logging** | Every data access is logged with who accessed what, when, and for which client |
| **Automated Testing** | Before every deployment, automated tests attempt to access Client A's data from Client B's context — if any test passes, deployment is blocked |

### Authentication & Access Control

- **Admin Dashboard**: JWT-based authentication with role-based access (admin, manager, specialist)
- **API Tokens**: All external service tokens (Meta, Claude, WhatsApp) stored encrypted in environment variables, never in code
- **WhatsApp Webhooks**: Every incoming message is cryptographically verified using HMAC-SHA256 signatures

---

## 6. WhatsApp Integration Details

### Setup Requirements

- WhatsApp Cloud API (Meta-hosted, no third-party middleware needed)
- One WhatsApp Business Account (WABA) under Bullet Digital Media's Meta Business Manager
- Business verification with Meta (2-10 business days)
- Pre-approved message templates for any proactive outreach

### How Client Identification Works

When a client sends a WhatsApp message, the system receives their phone number. This phone number is looked up against a database of pre-registered client contacts. Only registered phone numbers receive AI responses — unrecognised numbers get a message directing them to contact their account manager.

### Pricing Model

- **Client-initiated conversations** (client messages first): Responses within 24 hours are **free** (service messages)
- **Proactive outreach** (we message first): Requires pre-approved templates, charged per message (~£0.01-0.04 depending on type)
- **First 1,000 service conversations per month**: Free

### Human Escalation

The AI will escalate to a human team member when:
- It is not confident in its answer (low retrieval quality)
- The client explicitly asks to speak to someone
- The topic is sensitive (billing, complaints, strategy, contracts)
- No relevant data is found

When escalated, the client receives: *"Great question. I've flagged this for your account manager to review. They'll get back to you shortly."*

The team member sees the full conversation, the AI's draft response, and can edit or replace it before sending.

### WhatsApp Group Chats — Evaluated and Not Viable

We investigated whether each client could have a dedicated WhatsApp group containing the AI bot, the client's contacts, and their assigned account manager. This would allow account managers to observe conversations and step in directly from their own number.

**Finding: WhatsApp Business API groups are not viable for Phase 1.**

| Constraint | Detail |
|-----------|--------|
| **Volume threshold** | Group functionality requires **100,000+ monthly business-initiated conversations** before it is unlocked. At launch, volume will be ~1,000/month (Tier 1). This is a hard blocker. |
| **Max group size** | 8 members per group — workable for this use case, but irrelevant given the volume blocker. |
| **No interactive messages** | Buttons, lists, and quick replies are **not supported** in group chats — only plain text and templates. |
| **No analytics** | No message delivery or read analytics for group template messages. |
| **Account manager in multiple groups** | Technically supported (one number can be in up to 10,000 groups), but moot given the volume threshold. |

### Recommended Alternative: Account Manager Dashboard Access

Instead of WhatsApp groups, account managers get full visibility and intervention capability through the admin dashboard:

| Capability | How It Works |
|-----------|-------------|
| **See all client conversations** | Account managers view real-time WhatsApp conversations for all their assigned clients in the dashboard |
| **Step in at any time** | Account managers can take over any conversation — their response sends from the business WhatsApp number |
| **Full context** | When stepping in, the account manager sees the complete conversation history, the AI's draft response, and the data sources used |
| **Escalation notifications** | When the AI escalates, the assigned account manager is notified immediately (dashboard + email) |
| **No client confusion** | Clients interact with one consistent WhatsApp number rather than seeing messages from different sources in a group |

This approach provides the same observability and intervention capability that groups would offer, without the platform restrictions. If WhatsApp unlocks group functionality at higher volumes in the future, this can be revisited.

---

## 7. Admin Dashboard

The admin dashboard is the primary interface for Bullet Digital Media's team to manage the system.

### Key Features

| Feature | Description |
|---------|-------------|
| **Client Management** | Create, edit, and manage client profiles. Assign team members. Track status (onboarding, active, paused). |
| **Phone Number Mapping** | Register and verify client WhatsApp numbers. Map phone numbers to specific clients for data isolation. |
| **Ad Account Configuration** | Link Meta ad account IDs to clients. Monitor connection status and sync health. |
| **Document Management** | Upload client documents (PDFs, Google Docs exports, spreadsheets, transcripts). Track processing status. |
| **Internal AI Query** | Ask questions about any client and get AI-powered answers from their knowledge base — the primary Phase 1 deliverable. |
| **Escalation Queue** | View pending escalations, claim them, review AI drafts, and send responses. |
| **Conversation History** | View all WhatsApp conversations per client, including AI confidence scores and data sources used. |
| **System Health** | Monitor Meta API polling status, WhatsApp connection health, and data freshness per client. |

---

## 8. Document Ingestion

### Supported Formats

- PDF documents
- Word documents (.docx)
- Google Docs (exported as text)
- Spreadsheets (CSV, Google Sheets export)
- Plain text (email content, Loom transcripts)
- Presentations (Google Slides, PowerPoint — text extracted)

### How Documents Are Processed

1. **Upload** — Staff uploads a document via the admin dashboard and assigns it to a client
2. **Parse** — System extracts text content based on file format
3. **Summarise** — AI generates a brief summary of what the document covers (used to improve search accuracy)
4. **Chunk** — Document is split into searchable segments (~512 tokens each with overlap)
5. **Embed** — Each segment is converted into a mathematical vector for semantic search
6. **Store** — Vectors are stored in the database, tagged to the specific client

### Important Note on Document Currency

The AI is only as current as the documents uploaded. John Limber noted that campaign strategy is "multi-layered" and "evolves and changes for each client." In Phase 1:

- **Strategy questions are always escalated to a human team member** — the AI does not answer strategy questions autonomously
- Documents older than 90 days are flagged for review
- The team will need to maintain document currency as part of their workflow

---

## 9. Meta Marketing API Integration

### Access Requirements

To pull campaign data, the system needs:

1. **Marketing API access** at Standard tier (supports 100+ ad accounts)
2. **A System User token** created in Meta Business Manager (never expires, suitable for server-to-server automation)
3. **Ad account access** for each client — either directly under Bullet's Business Manager, or via partner access grants from client-owned Business Managers

### Available Campaign Data

| Metric | Description | Freshness |
|--------|-------------|-----------|
| Spend | Total ad spend | Near real-time |
| Impressions | Times ad shown | Near real-time |
| Clicks | Link clicks | Near real-time |
| CPC | Cost per click | Near real-time |
| CPM | Cost per 1,000 impressions | Near real-time |
| CTR | Click-through rate | Near real-time |
| Reach | Unique users | Near real-time |
| Conversions | Purchase/signup events | **1-3 day lag** |
| ROAS | Return on ad spend | **1-3 day lag** |
| Leads | Lead form submissions | **1-3 day lag** |
| Cost per lead | Cost per lead | **1-3 day lag** |
| Campaign status | ACTIVE, PAUSED, etc. | Near real-time |
| Budget pacing | Daily/lifetime budget, spend today | Near real-time |

Data is available at campaign, ad set, and individual ad level.

### Pre-Development Blocker

**Meta API access is currently unconfirmed.** Before development of the campaign data pipeline can begin, Bullet Digital Media must:

1. Confirm the current Marketing API access tier
2. Create a System User with read-only ad account permissions
3. Audit which of the ~100 ad accounts are under Bullet's Business Manager versus client-owned

We will provide step-by-step guidance for completing these steps.

---

## 10. Threats, Limitations & Risks

This section provides an honest assessment of all identified risks. Every system of this nature carries these risks — the difference is whether they are understood and managed from day one.

### Critical Risks

#### Cross-Client Data Leakage
- **What could happen**: The AI returns one client's campaign data or documents to a different client
- **Likelihood**: Medium (without proper safeguards); Very Low (with our multi-layered protections)
- **Impact**: Loss of client trust, potential legal action, reputational damage
- **How we prevent it**: Database-level row security, mandatory client filters on every query, automated cross-tenant tests before every deployment, comprehensive audit logging
- **Residual risk**: Schema migration errors could temporarily weaken protections. Mitigated by mandatory code review for any database changes and automated testing.

#### AI Hallucination on Campaign Numbers
- **What could happen**: The AI fabricates campaign metrics that look plausible but are wrong (e.g., reports CPC of £1.20 when actual is £2.40)
- **Likelihood**: Medium (if metrics are LLM-generated); Very Low (with our approach)
- **Impact**: Client makes business decisions based on fabricated data. Trust destroyed.
- **How we prevent it**: Campaign metrics are **never generated by the AI model**. They are fetched directly from Meta's API, cached in the database, and presented through structured templates. The AI's role is to contextualise and explain the data, not to produce it. All calculations are done in application code, not by the AI.
- **Residual risk**: Edge cases where the AI interpolates between data points or calculates comparisons incorrectly. Mitigated by performing all calculations in application code.

#### WhatsApp AI Chatbot Policy (January 2026)
- **What could happen**: Meta's updated WhatsApp policy bans general-purpose AI chatbots. If our system is classified as general-purpose, the WhatsApp Business account could be suspended.
- **Likelihood**: Medium
- **Impact**: Complete loss of the client communication channel
- **How we prevent it**: The system is architected as a **scoped campaign performance support tool**, not a general-purpose chatbot. It answers only campaign performance questions and escalates everything else. Message templates submitted for Meta approval explicitly describe the use case as "marketing campaign performance reporting." Human escalation is always available.
- **Residual risk**: Meta's enforcement is opaque and may evolve. We maintain Telegram as a documented fallback channel that can be activated within days if needed.

### High Risks

| Risk | Impact | How We Handle It |
|------|--------|-----------------|
| **Stale data causing wrong decisions** | Client acts on outdated metrics | Every response includes data freshness timestamps. Conversion data always caveated with "typically lags 1-3 days." |
| **Meta API access uncertainty** | Cannot build campaign data pipeline | **Pre-development blocker.** Must be resolved before Sprint 3. We provide step-by-step guidance. |
| **Outdated strategy documents** | AI references old strategies as current | Strategy questions escalated to humans in Phase 1. Documents flagged for review after 90 days. |
| **Phone number spoofing** | Unauthorised access to campaign data | Only pre-registered, verified phone numbers receive responses. Unknown numbers are rejected. |
| **API token compromise** | Unauthorised access to Meta/WhatsApp/AI services | All tokens stored encrypted in environment variables. Rotation schedule. Logging sanitisation. |
| **Database outage** | System unavailable | Graceful degradation — messages queued, clients notified of delay. Daily backups. |
| **Claude AI outage** | No AI responses generated | Retry queue with backoff. Cached templates for common queries as fallback. |
| **Cost escalation at scale** | Per-client cost exceeds value | Phased rollout validates costs before full deployment. Response caching. Cost monitoring from day 1. |

### Platform Limitations (Cannot Be Changed)

These are inherent limitations of the platforms we integrate with. They cannot be engineered away but can be managed:

1. **Conversion data always lags 1-3 days** — This is how Meta reports conversion data. The AI will clearly state this in every response involving conversions.

2. **Historical campaign data limited to 90 days via API** — Meta's API only serves the last 90 days of detailed metrics. For older data, historical reports must be uploaded as documents. Over time, our local database will accumulate more history.

3. **WhatsApp 24-hour messaging window** — The AI can only respond for free within 24 hours of the client's last message. After that, proactive outreach requires pre-approved template messages (which cost ~£0.01-0.04 each and need Meta approval).

4. **WhatsApp template approval required for proactive messages** — Any new type of outbound message needs Meta approval (typically 1 minute to 24 hours). This limits how quickly new message types can be deployed.

5. **WhatsApp rate limits start at 1,000 unique users/day** — New accounts start at Tier 1. The phased rollout (starting with a subset of clients) allows natural tier progression before full deployment.

6. **Strategy documents require team maintenance** — The AI is only as current as the documents uploaded. Campaign strategy is fluid and the team must maintain document currency as part of their workflow. This is a shared responsibility.

7. **Attribution window restrictions (January 2026)** — Meta removed support for 7-day and 28-day view-through attribution windows. This affects how conversion data is reported.

8. **Reach data limited to 13 months with breakdowns** — When breaking down reach by age, gender, or country, only the last 13 months of data is available.

---

## 11. Build Sequence

### Sprint 1 (Weeks 1-2): Foundation
- Database setup with all tables and security policies
- Core API scaffolding with data isolation enforcement
- Client management API (create, edit, manage clients)
- Admin dashboard scaffold (authentication, client list/detail pages)

### Sprint 2 (Weeks 3-4): Document RAG — Primary Deliverable
- Document upload and storage
- Document processing pipeline (parse, chunk, embed, store)
- AI retrieval engine (search documents + generate answers)
- **Internal query interface** — admin dashboard page where staff ask questions about a client and get AI-powered answers

**This is the core Phase 1 deliverable**: the internal knowledge bank that John defined as the success criteria.

### Sprint 3 (Weeks 5-6): Campaign Data + WhatsApp
- Meta Marketing API polling service (all scheduled data pulls)
- Campaign data display with freshness indicators
- WhatsApp webhook (security, deduplication, client identification)
- Message processing pipeline (combine documents + metrics, generate response)

**Requires**: Meta API access confirmed (pre-development blocker)

### Sprint 4 (Weeks 7-8): Escalation + Pilot
- Escalation system (triggers, queue, staff notifications, resolve workflow)
- Conversation management in admin dashboard
- Comprehensive audit logging
- Deploy to 3-5 pilot clients for staff testing

---

## 12. Pre-Development Requirements

Before development can begin, the following must be in place:

| Requirement | Owner | Status | Blocker? |
|-------------|-------|--------|----------|
| Meta Marketing API access confirmed | Bullet Digital Media | Unconfirmed | Yes (Sprint 3) |
| System User token created in Meta BM | Bullet Digital Media | Not started | Yes (Sprint 3) |
| Ad account ownership audit (~100 accounts) | Bullet Digital Media | Not started | Yes (Sprint 3) |
| WhatsApp Business API application submitted | IzzyAgents + Bullet | Not started | Yes (Sprint 3) |
| Claude API key obtained | Bullet Digital Media | John committed to obtaining | Yes (Sprint 2) |
| Voyage AI API key obtained | IzzyAgents | Not started | Yes (Sprint 2) |
| Initial client documents for testing | Bullet Digital Media | Not started | Helpful (Sprint 2) |
| WhatsApp Business phone number registered | Bullet Digital Media | Not started | Yes (Sprint 3) |

---

## 13. Success Criteria (Phase 1)

As defined by John Limber during discovery:

> *"Phase 1 — Internal Client Knowledge Bank. The immediate goal is to build a comprehensive knowledge base for each client that the internal team can access quickly. This will help with responding to client queries faster, reducing time spent searching across platforms, smoother client handovers, and providing cover when team members are on holiday. I think building this initially would have tons of value and means we can stress test this internally before it becomes client facing."*

Phase 1 is complete when:
- [ ] Staff can query any client's knowledge base and get accurate, sourced answers
- [ ] Campaign metrics are pulled automatically and displayed with freshness indicators
- [ ] WhatsApp AI responds to registered client phone numbers with correct, isolated data
- [ ] Human escalation works for uncertain, sensitive, or complex queries
- [ ] No cross-client data leakage in automated testing
- [ ] 3-5 pilot clients successfully tested by staff

---

## 14. Next Steps

1. **Review this document** — Confirm alignment on architecture, priorities, and approach
2. **Resolve pre-development blockers** — Particularly Meta API access and Claude API key
3. **Submit WhatsApp Business API application** — Apply early as approval takes 2-10 business days
4. **Identify pilot clients** — Select 3-5 clients for initial testing in Sprint 4
5. **Gather initial documents** — Collect a sample set of client documents for testing the ingestion pipeline
6. **Confirm investment scope** — Align on Sprint 1 start date

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
