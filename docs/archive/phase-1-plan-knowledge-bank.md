# Bullet Digital Media — Phase 1 Architecture Plan

**Prepared by**: IzzyAgents Technical Team
**Date**: 23 March 2026
**Status**: Draft — For Client Review

---

## 1. Overview

Phase 1 delivers an AI-powered **internal client knowledge bank** for Bullet Digital Media's team, followed by a **client-facing Telegram AI bot** that answers campaign performance questions. Each client gets a dedicated Telegram group containing the AI bot, the client, and their assigned account manager. The system ingests client documents, pulls live Meta campaign data, and uses AI to provide instant, accurate answers — with strict data isolation between all ~100 clients.

### Phase 1 Goals
- Staff can query any client's information instantly (documents, campaign data, history)
- Faster client query responses, smoother handovers, holiday cover
- Dedicated Telegram group per client where they can ask campaign performance questions directly
- Account managers participate naturally in the same group and can step in at any time
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
| **Backend API** | FastAPI (Python) | High-performance async API for handling Telegram webhooks and data processing |
| **Admin Dashboard** | Next.js + Tailwind CSS + Framer Motion | Staff-facing web interface with utility-first styling and polished animations |
| **Messaging** | Telegram Bot API | Confirmed communication channel - free, instant setup, supports groups, inline keyboards, and bot commands |
| **Caching** | Redis | Conversation history, message deduplication, performance caching |
| **Background Jobs** | Celery | Scheduled Meta API polling, document processing |
| **Hosting** | Render.com | Web services, background workers, deployment |

---

## 3. System Architecture

```
                              EXTERNAL SERVICES
    ┌──────────────────┐  ┌───────────────────┐  ┌──────────────────┐
    │ Telegram Bot     │  │ Meta Marketing    │  │ Claude AI        │
    │ API              │  │ API               │  │ + Voyage AI      │
    └────────┬─────────┘  └─────────┬─────────┘  └────────┬─────────┘
             │                      │                      │
             v                      v                      v
    ┌────────┴─────────┐  ┌─────────┴─────────┐  ┌────────┴─────────┐
    │ Webhook Handler  │  │ Campaign Poller   │  │ RAG Engine       │
    │ (FastAPI)        │  │ (Background)      │  │ (LlamaIndex)     │
    │ • Secret verify  │  │ • Scheduled pulls │  │ • Document search│
    │ • Deduplication  │  │ • Rate management │  │ • Relevance rank │
    │ • Group routing  │  │ • Data caching    │  │ • Context build  │
    │ • Role resolve   │  │                   │  │                  │
    │ • Pause check    │  │                   │  │                  │
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
    │ • Groups  │ │ • Pause   │ │           │
    └───────────┘ └───────────┘ └───────────┘

    ┌──────────────────────────────┐
    │ Admin Dashboard (Web)        │
    │ • Client management          │
    │ • Telegram group management  │
    │ • Document management        │
    │ • Escalation queue           │
    │ • Campaign metrics view      │
    │ • Internal AI query tool     │
    │ • Conversation history       │
    └──────────────────────────────┘
```

---

## 4. How It Works

### 4.1 Client Setup (Via Admin Dashboard + Telegram)

1. Staff creates a new client in the admin dashboard
2. Staff adds the client's Meta ad account ID(s)
3. Staff clicks "Set Up Telegram Group" - the system generates a setup token
4. The assigned account manager creates a Telegram supergroup on their phone/desktop and adds the bot
5. Account manager sends `/setup [TOKEN]` in the group
6. The bot automatically configures the group: renames it to "BDM - [Client Name] - Campaign Hub", sets a branded photo, registers commands, and generates a client invite link
7. The invite link is displayed in the admin dashboard - the team distributes it to the client manually
8. When the client joins, the bot sends a personalised welcome message with an inline keyboard menu
9. Staff uploads relevant documents (onboarding docs, strategy briefs, notes)
10. System automatically processes documents and begins pulling campaign data

### 4.2 Internal Knowledge Bank (Staff Query)

1. Staff selects a client in the admin dashboard
2. Staff types a question (e.g., "What's this client's current CPC?")
3. System searches the client's documents + campaign data
4. AI generates an answer with source citations and data freshness timestamps
5. Staff uses this to respond to the client faster

### 4.3 Telegram Client Query (Client-Facing)

1. Client sends a message in their Telegram group - using `/ask`, @mentioning the bot, or replying to a bot message (e.g., "/ask How much have we spent this month?")
2. System identifies the client by the group's chat ID, which is mapped to a single client
3. System retrieves only that client's documents and campaign data
4. AI generates a response with clear data freshness indicators and inline keyboard buttons for follow-up actions
5. If the AI is uncertain or the topic is sensitive, it escalates to the account manager - who is already in the group and can respond directly from Telegram
6. For complex queries, the account manager can also use the admin dashboard for full context before responding

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
| **Group-to-Client Mapping** | Each Telegram group chat ID is mapped to exactly one client. This mapping is globally unique — the same group cannot belong to two clients |
| **AI Retrieval** | Every document search and data query includes a mandatory client filter. The AI only ever sees one client's data at a time |
| **Audit Logging** | Every data access is logged with who accessed what, when, and for which client |
| **Automated Testing** | Before every deployment, automated tests attempt to access Client A's data from Client B's context — if any test passes, deployment is blocked |

### Authentication & Access Control

- **Admin Dashboard**: JWT-based authentication with role-based access (admin, manager, specialist)
- **API Tokens**: All external service tokens (Meta, Claude, Telegram) stored encrypted in environment variables, never in code
- **Telegram Webhooks**: Every incoming update is verified using a secret token header set during webhook registration

---

## 6. Telegram Integration Details

### 6.1 Group Architecture

Each client gets a dedicated Telegram supergroup containing three participants:

1. **The AI bot** - answers campaign questions, provides reports, manages the menu system
2. **The client** (and optionally their additional contacts)
3. **The assigned account manager** - observes conversations and can step in at any time

Supergroups are used (not basic groups) because they support persistent invite links, granular admin permissions, and better message history management. Account managers can be members of unlimited groups simultaneously, so one account manager can monitor all their clients from their own Telegram app.

### 6.2 Bot Commands & Menu System

The bot provides a `/menu` command that displays an interactive inline keyboard — buttons attached to the message that any group member can tap.

**Client menu (visible to clients):**

| Command | Description |
|---------|-------------|
| `/menu` | Opens the main menu with interactive buttons |
| `/ask` | Ask a question about campaigns (e.g., `/ask What's my CPC this week?`) |
| `/report` | Get a campaign performance summary (sub-menu: today, this week, this month) |
| `/help` | How to use the bot |

**Account manager menu (additional commands, hidden from clients):**

| Command | Description |
|---------|-------------|
| `/pause` | Pause the AI bot in this group (for private conversations with the client) |
| `/resume` | Resume the AI bot |
| `/status` | View bot status, client config, last data sync time |
| `/escalate` | Flag the conversation for review in the admin dashboard |
| `/note` | Add an internal note to the client's record (confirmation sent via DM, not shown in the group) |
| `/refresh` | Trigger an immediate Meta data pull for this client |

These account manager commands are hidden from the client's command list using Telegram's scoped command feature (`BotCommandScopeChatMember`). Clients literally do not see these commands in their menu.

On group setup, the bot pins a welcome message with the main inline keyboard. This acts as a persistent "control panel" always visible at the top of the chat — clients can always scroll up or tap the pin to access the menu without remembering commands.

### 6.3 When the Bot Responds

The bot uses an **explicit-only** response model. It responds ONLY when:

- A command is issued (e.g., `/ask`, `/menu`, `/report`)
- Someone @mentions the bot by username
- Someone replies directly to one of the bot's messages
- An inline keyboard button is pressed

The bot stays **completely silent** during conversations between the client and account manager. There is no auto-detection, no keyword monitoring, and no unsolicited nudges. This keeps the experience clean with zero false positives.

### 6.4 Pause/Resume Mechanism

When the account manager needs to have a private conversation with the client without the bot responding:

**Pausing:**
1. Account manager sends `/pause` or taps the "Pause Bot" button in the menu
2. Bot verifies the sender is an account manager
3. Bot sends a message: *"I'll step back and let you two talk. When you're ready for me again, just tap the button below."* with a **Resume Bot** inline keyboard button
4. Bot pins this message so the resume button is always visible at the top of the chat
5. Bot ignores all messages in the group except `/resume` and the Resume button

**Resuming:**
1. Account manager sends `/resume` or taps the "Resume Bot" button
2. Bot unpins the pause message
3. Bot sends: *"I'm back and ready to help. Ask me anything about your campaigns."* with an **Open Menu** button

The pause state is stored in the database (not in memory), so it survives bot restarts and deployments. Optional: if the bot has been paused for more than 4 hours, the system can notify the account manager as a reminder.

### 6.5 Pricing

- **Telegram Bot API**: Completely free
- No per-message charges
- No template fees or approval processes
- No monthly platform costs
- No volume tiers or rate limit progression
- Cost is limited to server infrastructure only

For reference, WhatsApp was evaluated and rejected due to hard blockers (group chats require 100,000+ monthly conversations to unlock, AI chatbot policy risk, per-message costs). The full comparison is documented in `docs/whatsapp-vs-telegram.md`.

### 6.6 Human Escalation

The AI will escalate to a human when:
- It is not confident in its answer (low retrieval quality)
- The client explicitly asks to speak to someone
- The topic is sensitive (billing, complaints, strategy, contracts)
- No relevant data is found

**Key advantage of Telegram groups**: the account manager is already in the group and sees the full conversation in real time. When the AI escalates, the account manager can respond directly from their Telegram app — no need to open the admin dashboard for quick interventions.

When escalated, the client sees: *"Great question. I've flagged this for [Account Manager Name] to take a look at. They're in this group and will get back to you shortly."*

For complex responses requiring full data context, the account manager can use the admin dashboard to review the AI's draft response, data sources, and confidence scores before replying.

---

## 7. Admin Dashboard

The admin dashboard is the primary interface for Bullet Digital Media's team to manage the system.

### Key Features

| Feature | Description |
|---------|-------------|
| **Client Management** | Create, edit, and manage client profiles. Assign team members. Track status (onboarding, active, paused). |
| **Telegram Group Management** | Set up client groups (generate setup tokens), view group status (active, paused, pending), generate invite links, manage members, pause/resume bot remotely. |
| **Ad Account Configuration** | Link Meta ad account IDs to clients. Monitor connection status and sync health. |
| **Document Management** | Upload client documents (PDFs, Google Docs exports, spreadsheets, transcripts). Track processing status. |
| **Internal AI Query** | Ask questions about any client and get AI-powered answers from their knowledge base — the primary Phase 1 deliverable. |
| **Escalation Queue** | View pending escalations, claim them, review AI drafts, and send responses. |
| **Conversation History** | View all Telegram conversations per client, including AI confidence scores and data sources used. |
| **System Health** | Monitor Meta API polling status, Telegram bot connection health, and data freshness per client. |

---

## 8. Document Ingestion

### Supported Formats

**Documents:**
- PDF documents
- Word documents (.docx)
- Google Docs (exported as text)
- Spreadsheets (CSV, Google Sheets export)
- Plain text (email content, transcripts)
- Presentations (Google Slides, PowerPoint — text extracted)

**Video & Audio** (automatically transcribed to text):
- Video files (MP4, MOV, WEBM) — Loom recordings, Zoom calls, training videos
- Audio files (M4A, MP3, WAV) — voice memos, call recordings

### How Documents Are Processed

1. **Upload** — Staff uploads a document or video/audio file via the admin dashboard and assigns it to a client
2. **Transcribe** (video/audio only) — Audio is extracted from video files, then transcribed to text using OpenAI Whisper API
3. **Parse** — System extracts text content based on file format (or uses the transcript for video/audio)
4. **Summarise** — AI generates a brief summary of what the document covers (used to improve search accuracy)
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

#### Client Telegram Adoption
- **What could happen**: Some clients may resist downloading Telegram, as WhatsApp is more commonly used in the UK for business communication.
- **Likelihood**: Low-Medium
- **Impact**: Delayed onboarding for resistant clients; potential need for manual workaround
- **How we prevent it**: Frame Telegram as a dedicated, professional campaign channel (not a replacement for existing messaging). Provide clear onboarding instructions - setup takes 2-3 minutes and uses the same phone number. The richer experience (interactive menus, instant responses, account manager in the same group) quickly demonstrates value.
- **Residual risk**: A small number of clients may refuse entirely. For these cases, the account manager can relay information manually using the admin dashboard's internal query tool. If adoption proves to be a widespread barrier post-launch, WhatsApp integration can be added as a secondary channel.

### High Risks

| Risk | Impact | How We Handle It |
|------|--------|-----------------|
| **Stale data causing wrong decisions** | Client acts on outdated metrics | Every response includes data freshness timestamps. Conversion data always caveated with "typically lags 1-3 days." |
| **Meta API access uncertainty** | Cannot build campaign data pipeline | **Pre-development blocker.** Must be resolved before Sprint 3. We provide step-by-step guidance. |
| **Outdated strategy documents** | AI references old strategies as current | Strategy questions escalated to humans in Phase 1. Documents flagged for review after 90 days. |
| **Unauthorised group access** | Someone joins a client group uninvited | Invite links generated with member limits and tracked in the database. Unknown joiners are flagged automatically. Links can be revoked once expected members have joined. |
| **API token compromise** | Unauthorised access to Meta/Telegram/AI services | All tokens stored encrypted in environment variables. Telegram webhook verified via secret token header. Rotation schedule. Logging sanitisation. |
| **Database outage** | System unavailable | Graceful degradation — messages queued, clients notified of delay. Daily backups. |
| **Claude AI outage** | No AI responses generated | Retry queue with backoff. Cached templates for common queries as fallback. |
| **Cost escalation at scale** | Per-client cost exceeds value | Phased rollout validates costs before full deployment. Response caching. Cost monitoring from day 1. |

### Platform Limitations (Cannot Be Changed)

These are inherent limitations of the platforms we integrate with. They cannot be engineered away but can be managed:

1. **Conversion data always lags 1-3 days** — This is how Meta reports conversion data. The AI will clearly state this in every response involving conversions.

2. **Historical campaign data limited to 90 days via API** — Meta's API only serves the last 90 days of detailed metrics. For older data, historical reports must be uploaded as documents. Over time, our local database will accumulate more history.

3. **Telegram groups are not end-to-end encrypted** — Groups use client-server encryption, not end-to-end. Practical risk is low because the data shared (CPC, spend, ROAS) is business performance data, not sensitive personal information. This is the same data typically shared via email and dashboards.

4. **No read receipts for Telegram bots** — Bots cannot confirm whether a client has read a message. The inline keyboard approach (requiring a button tap) provides an indirect engagement signal when needed.

5. **Strategy documents require team maintenance** — The AI is only as current as the documents uploaded. Campaign strategy is fluid and the team must maintain document currency as part of their workflow. This is a shared responsibility.

6. **Attribution window restrictions (January 2026)** — Meta removed support for 7-day and 28-day view-through attribution windows. This affects how conversion data is reported.

7. **Reach data limited to 13 months with breakdowns** — When breaking down reach by age, gender, or country, only the last 13 months of data is available.

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

### Sprint 3 (Weeks 5-6): Campaign Data + Telegram
- Meta Marketing API polling service (all scheduled data pulls)
- Campaign data display with freshness indicators
- Telegram webhook handler (secret verification, deduplication, group routing, role resolution, pause check)
- Group setup flow (setup token, auto-configuration, invite link generation)
- Bot command system (`/menu`, `/ask`, `/report`, `/help`, `/pause`, `/resume`, `/status`, `/escalate`, `/note`, `/refresh`)
- Inline keyboard menus and sub-menus
- Message processing pipeline (combine documents + metrics, generate response)

**Requires**: Meta API access confirmed (pre-development blocker), Telegram bot created via @BotFather

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
| Telegram bot created via @BotFather | IzzyAgents | Not started | Yes (Sprint 3) — takes less than 2 minutes |
| Claude API key obtained | Bullet Digital Media | John committed to obtaining | Yes (Sprint 2) |
| Voyage AI API key obtained | IzzyAgents | Not started | Yes (Sprint 2) |
| OpenAI API key (Whisper transcription) | IzzyAgents | Not started | Yes (Sprint 2) — needed for video/audio transcription |
| Initial client documents for testing | Bullet Digital Media | Not started | Helpful (Sprint 2) |

---

## 13. Success Criteria (Phase 1)

As defined by John Limber during discovery:

> *"Phase 1 — Internal Client Knowledge Bank. The immediate goal is to build a comprehensive knowledge base for each client that the internal team can access quickly. This will help with responding to client queries faster, reducing time spent searching across platforms, smoother client handovers, and providing cover when team members are on holiday. I think building this initially would have tons of value and means we can stress test this internally before it becomes client facing."*

Phase 1 is complete when:
- [ ] Staff can query any client's knowledge base and get accurate, sourced answers
- [ ] Campaign metrics are pulled automatically and displayed with freshness indicators
- [ ] Telegram AI bot responds in client groups with correct, isolated data
- [ ] Bot pause/resume works correctly for account manager conversations
- [ ] Menu system and inline keyboards provide intuitive navigation
- [ ] Human escalation works for uncertain, sensitive, or complex queries
- [ ] No cross-client data leakage in automated testing
- [ ] 3-5 pilot clients successfully tested by staff

---

## 14. Next Steps

1. **Review this document** — Confirm alignment on architecture, priorities, and approach
2. **Resolve pre-development blockers** — Particularly Meta API access and Claude API key
3. **Create Telegram bot via @BotFather** — Instant setup, takes less than 2 minutes
4. **Identify pilot clients** — Select 3-5 clients for initial testing in Sprint 4
5. **Gather initial documents** — Collect a sample set of client documents for testing the ingestion pipeline
6. **Prepare onboarding guidance** — Brief instructions for the team on distributing Telegram invite links to clients
7. **Confirm investment scope** — Align on Sprint 1 start date

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
