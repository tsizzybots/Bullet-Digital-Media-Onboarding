# Bullet Digital Media - Phase 1 Sprint Plan

**Prepared by**: IzzyAgents Technical Team
**Date**: 23 March 2026
**Approach**: Test-Driven Development - each task includes test criteria that must pass before the task is considered complete.

---

## Overview

Phase 1 is broken into **4 sprints across 8 weeks**, delivering **53 individually trackable tasks**. Each sprint builds on the previous one, with integration testing at the end of every sprint to catch issues early.

| Sprint | Weeks | Focus | Tasks |
|--------|-------|-------|-------|
| Sprint 1 | 1-2 | Foundation | 11 |
| Sprint 2 | 3-4 | Document RAG (Primary Deliverable) | 14 |
| Sprint 3 | 5-6 | Campaign Data + Telegram | 16 |
| Sprint 4 | 7-8 | Escalation, Audit + Pilot | 12 |
| **Total** | **8** | | **53** |

### Pre-Development Blockers

These must be resolved before the sprint that requires them:

| Requirement | Needed By | Owner | Status |
|-------------|-----------|-------|--------|
| Claude API key | Sprint 2 | Bullet Digital Media | John committed to obtaining |
| Voyage AI API key | Sprint 2 | IzzyAgents | Not started |
| Initial client documents for testing | Sprint 2 | Bullet Digital Media | Not started |
| Meta Marketing API access confirmed | Sprint 3 | Bullet Digital Media | Unconfirmed |
| System User token created in Meta BM | Sprint 3 | Bullet Digital Media | Not started |
| Ad account ownership audit (~100 accounts) | Sprint 3 | Bullet Digital Media | Not started |
| Telegram bot created via @BotFather | Sprint 3 | IzzyAgents | Not started |

---

## Sprint 1 (Weeks 1-2): Foundation

The foundation sprint establishes the project structure, database, authentication, and the admin dashboard scaffold. Everything built here is the base that all subsequent sprints depend on.

---

### S1-01: Project Scaffolding

**Scope**: Set up the monorepo structure with a FastAPI (Python) backend and Next.js (TypeScript) frontend. Configure package management, linting, and formatting.

**Deliverables**:
- `/backend` - FastAPI application with Poetry/pip dependency management
- `/frontend` - Next.js application with Tailwind CSS and Framer Motion
- Shared configuration (`.env.example`, `.gitignore`, `README`)

**Test Criteria**:
- [ ] `pytest` runs with 0 errors on backend
- [ ] `npm run build` succeeds on frontend
- [ ] Both servers start locally without errors
- [ ] Linting passes on both projects
- [ ] `.env.example` documents all required environment variables

---

### S1-02: Neon PostgreSQL Connection + Alembic Migrations

**Scope**: Connect the FastAPI backend to Neon PostgreSQL. Set up Alembic for database migrations with a versioned migration workflow.

**Deliverables**:
- Database connection module with connection pooling (asyncpg)
- Alembic configuration and initial migration
- Health check endpoint that verifies DB connectivity

**Test Criteria**:
- [ ] Alembic migration runs successfully against Neon
- [ ] Connection pool test passes (10 concurrent connections)
- [ ] `SELECT 1` returns successfully from the application
- [ ] Health check endpoint (`GET /health`) returns 200 with DB status
- [ ] Migration rollback works cleanly

---

### S1-03: Core Database Schema

**Scope**: Create the foundational database tables for the application.

**Tables**:
- `clients` - Client profiles (name, status, created_at, etc.)
- `team_members` - Internal staff (name, email, role, assigned clients)
- `documents` - Document metadata (client_id, filename, format, status, uploaded_at)
- `ad_accounts` - Meta ad account links (client_id, account_id, status)

**Test Criteria**:
- [ ] All tables created via Alembic migration
- [ ] Foreign key relationships valid and enforced
- [ ] Seed script inserts test data (3 clients, 2 team members, 5 documents) without errors
- [ ] Unique constraints prevent duplicate entries where expected
- [ ] Indexes created on frequently queried columns (client_id, status)

---

### S1-04: Row-Level Security (RLS) Policies

**Scope**: Implement PostgreSQL Row-Level Security on all client-scoped tables to enforce data isolation at the database level.

**Deliverables**:
- RLS policies on `clients`, `documents`, `ad_accounts`, and all future client-scoped tables
- Database roles for application context setting
- Utility function to set client context on each request

**Test Criteria**:
- [ ] Query as Client A context returns only Client A rows
- [ ] Query as Client B context returns only Client B rows
- [ ] Cross-tenant access attempt returns 0 rows (not an error - just empty)
- [ ] Admin context can access all rows (for dashboard)
- [ ] RLS cannot be bypassed by direct SQL through the application connection

---

### S1-05: Redis Connection + Configuration

**Scope**: Set up Redis for caching, message deduplication, and session/state management.

**Deliverables**:
- Redis connection module with connection pooling
- Cache utility functions (get, set, delete, with TTL)
- Health check integration

**Test Criteria**:
- [ ] Redis `PING` returns `PONG` from the application
- [ ] `SET`/`GET` round-trip works correctly
- [ ] TTL expiration works (set key with 1s TTL, verify expired after 2s)
- [ ] Connection pool handles 20 concurrent requests without errors
- [ ] Health check endpoint includes Redis status

---

### S1-06: Client CRUD API

**Scope**: Build the REST API endpoints for managing clients.

**Endpoints**:
- `POST /api/v1/clients` - Create client
- `GET /api/v1/clients` - List clients (paginated, filterable)
- `GET /api/v1/clients/{id}` - Get client detail
- `PUT /api/v1/clients/{id}` - Update client
- `DELETE /api/v1/clients/{id}` - Soft-delete client

**Test Criteria**:
- [ ] `POST` returns 201 with created client; invalid data returns 422
- [ ] `GET` list returns paginated results; supports `?status=active` filter
- [ ] `GET` detail returns 200 for existing client; 404 for non-existent
- [ ] `PUT` returns 200 with updated fields; rejects invalid data
- [ ] `DELETE` soft-deletes (sets `deleted_at`); client no longer appears in list
- [ ] All endpoints require authentication (tested in S1-07)

---

### S1-07: JWT Authentication + Role-Based Access

**Scope**: Implement JWT-based authentication with role-based access control for the API.

**Roles**: `admin`, `manager`, `specialist`

**Deliverables**:
- Login endpoint (`POST /api/v1/auth/login`)
- Token refresh endpoint (`POST /api/v1/auth/refresh`)
- Auth middleware that validates JWT on protected endpoints
- Role-based permission decorators

**Test Criteria**:
- [ ] Login with valid credentials returns JWT access + refresh tokens
- [ ] Login with invalid credentials returns 401
- [ ] Protected endpoints reject requests without a token (401)
- [ ] Protected endpoints reject expired tokens (401)
- [ ] Admin-only endpoints reject manager/specialist roles (403)
- [ ] Token refresh returns a new valid access token
- [ ] Tokens include user role and ID in payload

---

### S1-08: Admin Dashboard Scaffold

**Scope**: Create the Next.js admin dashboard with authentication flow, layout, and navigation.

**Deliverables**:
- Login page with email/password form
- Authenticated layout with sidebar navigation
- Dashboard home page (placeholder)
- Auth context provider (stores JWT, handles refresh)
- Protected route wrapper (redirects to login if unauthenticated)

**Test Criteria**:
- [ ] Login page renders without errors
- [ ] Valid credentials redirect to dashboard home
- [ ] Invalid credentials show error message (no page reload)
- [ ] Protected routes redirect to login when unauthenticated
- [ ] Sidebar navigation renders with correct menu items
- [ ] Logout clears tokens and redirects to login
- [ ] Dark mode is the default theme

---

### S1-09: Client Management UI

**Scope**: Build the client management pages in the admin dashboard.

**Pages**:
- Client list (table with search, filter by status, pagination)
- Create client form
- Edit client form
- Client detail page (overview, tabs for future sections)

**Test Criteria**:
- [ ] Client list loads and displays clients from the API
- [ ] Search filters clients by name in real-time
- [ ] Status filter works (active, onboarding, paused)
- [ ] Create form submits successfully; new client appears in list
- [ ] Edit form pre-fills with existing data; saves changes
- [ ] Detail page shows all client fields
- [ ] Empty states handled (no clients, no search results)

---

### S1-10: Render.com Deployment Configuration

**Scope**: Configure deployment to Render.com for both backend and frontend.

**Deliverables**:
- `render.yaml` blueprint with web services for backend + frontend
- Environment variable configuration
- Health check endpoints configured
- Build and start commands defined

**Test Criteria**:
- [ ] `render.yaml` passes Render's blueprint validation
- [ ] Backend deploys to staging and responds to health check
- [ ] Frontend deploys to staging and serves the login page
- [ ] Environment variables are configured (not hardcoded)
- [ ] HTTPS enforced on all endpoints

---

### S1-11: Sprint 1 Integration Tests + CI Pipeline

**Scope**: Write integration tests covering Sprint 1 deliverables and set up CI.

**Test Suites**:
- API integration tests (client CRUD with auth)
- RLS cross-tenant isolation tests
- Database migration tests
- Frontend build verification

**Test Criteria**:
- [ ] All unit tests pass (`pytest` + `npm test`)
- [ ] API integration tests pass (authenticated CRUD operations)
- [ ] RLS cross-tenant tests pass (5 scenarios minimum)
- [ ] CI pipeline runs on push to main branch
- [ ] Test coverage report generated (minimum 80% backend)

---

## Sprint 2 (Weeks 3-4): Document RAG - Primary Deliverable

This sprint delivers the **core Phase 1 value**: an internal knowledge bank where staff can query any client's documents and get AI-powered answers with source citations. This is the deliverable John Limber defined as the success criteria.

**Blockers**: Claude API key (Bullet), Voyage AI API key (IzzyAgents), initial test documents (Bullet)

---

### S2-01: Document Storage Schema + File Upload API

**Scope**: Extend the database schema for documents and build the file upload API. Supports both document files and video/audio files for transcription.

**Deliverables**:
- Extended `documents` table (file_path, file_size, format, processing_status, transcription_status, summary, chunk_count)
- `document_chunks` table (document_id, chunk_index, text_content, embedding, metadata)
- File upload endpoint (`POST /api/v1/clients/{client_id}/documents`)
- File storage (Render disk or S3-compatible)

**Test Criteria**:
- [ ] Upload endpoint accepts PDF, DOCX, CSV, TXT, PPTX files (documents)
- [ ] Upload endpoint accepts MP4, MOV, WEBM, M4A, MP3, WAV files (video/audio)
- [ ] Returns 201 with document ID and status "pending"
- [ ] File stored in configured storage backend
- [ ] Metadata saved to `documents` table with correct client_id
- [ ] Rejects unsupported formats with 400 and clear error message
- [ ] Document files: size limit 50MB; video/audio files: size limit 500MB
- [ ] Video/audio uploads set transcription_status to "pending"

---

### S2-02: Document Upload UI

**Scope**: Build the document upload interface in the admin dashboard. Supports both document files and video/audio files.

**Deliverables**:
- Upload form on client detail page (drag-and-drop + file picker)
- Upload progress indicator
- Document list per client showing status
- Video/audio files show transcription status separately

**Test Criteria**:
- [ ] Drag-and-drop upload works for document and video/audio file types
- [ ] File picker button works as alternative
- [ ] Progress indicator shows during upload
- [ ] Uploaded file appears in client's document list
- [ ] Processing status shown (pending/processing/ready/failed)
- [ ] Video/audio files show transcription status (extracting/transcribing/complete/failed)
- [ ] Error shown for unsupported file types before upload attempt

---

### S2-03: Document Parser

**Scope**: Build parsers to extract text content from each supported format. Routes video/audio files to the transcription pipeline (S2-04) instead of text extraction.

**Document Formats**: PDF, DOCX, CSV, TXT, PPTX
**Video/Audio Formats**: MP4, MOV, WEBM, M4A, MP3, WAV (routed to transcription)

**Deliverables**:
- Parser module with format detection and routing
- Format-specific extractors (PyPDF2/pdfplumber, python-docx, csv, python-pptx)
- Video/audio detection routes to transcription pipeline
- Extracted text stored as raw content before chunking

**Test Criteria**:
- [ ] PDF: extracted text matches expected content from test PDF
- [ ] DOCX: extracted text preserves paragraphs and headings
- [ ] CSV: extracted as readable text (headers + rows)
- [ ] TXT: content preserved as-is
- [ ] PPTX: text extracted from all slides
- [ ] MP4/MOV/WEBM: routed to transcription pipeline (not text parser)
- [ ] M4A/MP3/WAV: routed to transcription pipeline (not text parser)
- [ ] Malformed files return clear error (not crash)
- [ ] Empty files handled gracefully (warning logged, status set to "empty")

---

### S2-04: Video/Audio Transcription Pipeline

**Scope**: Transcribe uploaded video and audio files into text for ingestion into the RAG knowledge bank. Supports Loom recordings, Zoom calls, voice memos, and other media.

**Formats**: MP4, MOV, WEBM, M4A, MP3, WAV

**Deliverables**:
- Audio extraction from video files (ffmpeg)
- Speech-to-text transcription via OpenAI Whisper API ($0.006/min, max 25MB per request - split longer files)
- Transcript stored as document text content
- Transcription status tracking (extracting audio/transcribing/complete/failed)
- Transcript flows into existing pipeline (summarise -> chunk -> embed)

**Test Criteria**:
- [ ] MP4 video uploaded -> audio extracted -> transcript generated
- [ ] MP3/M4A audio uploaded -> transcript generated (no extraction step needed)
- [ ] Transcript text is accurate for clear English speech
- [ ] Transcription status updates visible in UI (extracting/transcribing/complete)
- [ ] Long videos (30+ minutes) handled without timeout (async processing)
- [ ] Large files split into 25MB chunks for Whisper API
- [ ] Failed transcription sets status to "failed" with error detail
- [ ] Transcript feeds into summarisation -> chunking -> embedding pipeline
- [ ] Transcribed content queryable via the internal query tool

---

### S2-05: AI Summarisation Pipeline

**Scope**: Generate a 2-3 sentence summary of each uploaded document using Claude AI.

**Deliverables**:
- Summarisation task (runs after parsing)
- Claude API integration for summarisation
- Summary stored in `documents.summary`

**Test Criteria**:
- [ ] Each uploaded document receives a summary after parsing
- [ ] Summary is 2-3 sentences and accurately describes the document
- [ ] Summary stored in the database
- [ ] Claude API errors handled with retry (3 attempts, exponential backoff)
- [ ] Rate limiting respected (no 429 errors in normal operation)

---

### S2-06: Document Chunking

**Scope**: Split parsed documents into overlapping chunks suitable for embedding and retrieval.

**Deliverables**:
- Chunking module (~512 tokens per chunk, ~50 token overlap)
- Chunks stored in `document_chunks` table
- Chunk metadata (document_id, chunk_index, character offsets)

**Test Criteria**:
- [ ] A 2000-token document produces approximately 4-5 chunks
- [ ] Adjacent chunks have overlapping content (~50 tokens)
- [ ] Chunk boundaries respect sentence boundaries where possible
- [ ] Chunks stored with correct document_id and sequential chunk_index
- [ ] Re-processing a document replaces old chunks (not duplicates)

---

### S2-07: Voyage 4 Embedding Integration

**Scope**: Convert document chunks into vector embeddings using Voyage 4.

**Deliverables**:
- Voyage AI client integration
- Batch embedding endpoint (efficient for multiple chunks)
- Embeddings stored in pgvector column on `document_chunks`

**Test Criteria**:
- [ ] Each chunk receives an embedding after chunking
- [ ] Embedding dimension is correct (1024 for Voyage 4)
- [ ] Embeddings stored in pgvector column
- [ ] Batch processing works (send multiple chunks in one API call)
- [ ] API errors handled with retry
- [ ] Processing status updates: pending -> processing -> ready

---

### S2-08: pgvector Semantic Search Index

**Scope**: Configure pgvector indexing and build the semantic search query layer.

**Deliverables**:
- HNSW or IVFFlat index on embedding column
- Search function: query embedding -> top-K similar chunks (with client filter)
- Relevance score returned with each result

**Test Criteria**:
- [ ] Similarity search returns relevant chunks for test queries
- [ ] Results ordered by relevance score (most relevant first)
- [ ] Client filter enforced: search for Client A returns 0 chunks from Client B
- [ ] Search latency < 500ms for a corpus of 1000 chunks
- [ ] Top-5 results for "What is the CPC?" include chunks about CPC (given test data)

---

### S2-09: LlamaIndex RAG Engine Configuration

**Scope**: Configure LlamaIndex as the RAG orchestration layer connecting pgvector retrieval with Claude AI generation.

**Deliverables**:
- LlamaIndex VectorStoreIndex with Neon/pgvector backend
- Query engine with client context filtering
- Source metadata extraction (document name, chunk location)

**Test Criteria**:
- [ ] Engine initialises successfully with Neon pgvector store
- [ ] Retrieves relevant context for test queries
- [ ] Client isolation enforced (mandatory client_id filter)
- [ ] Returns source metadata (document name, relevance score) with results
- [ ] Handles empty results gracefully (no relevant documents found)

---

### S2-10: Claude AI Response Generation with Citations

**Scope**: Generate natural language responses from retrieved context using Claude, with source citations and professional tone.

**Deliverables**:
- Prompt template that instructs Claude to cite sources and flag uncertainty
- Response formatting (answer + sources + confidence indicator)
- Strategy question detection (escalation flag)

**Test Criteria**:
- [ ] Responses include source citations (document names)
- [ ] Responses reference specific documents ("According to the onboarding questionnaire...")
- [ ] "I don't have enough information to answer that" returned when no relevant data found
- [ ] Professional, concise tone (not overly verbose)
- [ ] Strategy questions (e.g., "What should our targeting be?") trigger escalation flag
- [ ] Campaign number questions return escalation flag if no campaign data loaded yet

---

### S2-11: Internal Query API Endpoint

**Scope**: Build the API endpoint that powers the internal knowledge bank query tool.

**Endpoint**: `POST /api/v1/query`

**Request**: `{ client_id, question }`
**Response**: `{ answer, sources: [...], confidence, escalation_needed, data_freshness }`

**Test Criteria**:
- [ ] Accepts client_id + question; returns structured response
- [ ] Enforces JWT authentication
- [ ] RLS prevents querying a client the user doesn't have access to
- [ ] Response includes freshness timestamps for any data referenced
- [ ] Response latency < 5 seconds for typical queries
- [ ] Concurrent queries from different users don't interfere

---

### S2-12: Internal Query UI

**Scope**: Build the query interface in the admin dashboard where staff ask questions about clients.

**Deliverables**:
- Client selector dropdown
- Question input with submit
- Response display area (answer, sources, confidence)
- Conversation history per client (persisted in the session)
- Loading state during query processing

**Test Criteria**:
- [ ] Client selector populated from API
- [ ] Question submits and displays AI response
- [ ] Source citations are clickable/visible
- [ ] Loading spinner shown during processing
- [ ] Error states handled (API error, timeout)
- [ ] Previous questions shown in conversation history
- [ ] Escalation flag shown when AI is uncertain

---

### S2-13: Document Management UI

**Scope**: Build the complete document management interface in the admin dashboard.

**Deliverables**:
- Document list per client with status indicators
- Delete document (with confirmation)
- Re-process document button
- Document age flagging (>90 days)
- Document detail view (summary, chunk count, processing status)

**Test Criteria**:
- [ ] Document list shows status badges (pending/processing/ready/failed)
- [ ] Delete removes document + all associated chunks + embeddings
- [ ] Delete shows confirmation dialog before proceeding
- [ ] Re-process button re-triggers parse -> chunk -> embed pipeline
- [ ] Documents older than 90 days flagged with visual indicator
- [ ] Document detail shows summary and processing metadata

---

### S2-14: Sprint 2 Integration Tests + RAG Accuracy

**Scope**: End-to-end testing of the document ingestion and query pipeline. RAG accuracy validation with known test documents.

**Test Suites**:
- Upload-to-query pipeline (upload doc, wait for processing, query, verify answer)
- RAG accuracy tests (5 test documents with known answers)
- Cross-client isolation verification at RAG layer
- Performance benchmarks

**Test Criteria**:
- [ ] Upload a document -> query about its contents -> receive accurate answer (end-to-end)
- [ ] RAG returns correct answers for at least 4/5 test documents
- [ ] Cross-client isolation: Client B's query returns nothing from Client A's documents
- [ ] Response latency < 5 seconds for typical queries
- [ ] Test coverage meets threshold (80%+ backend)

---

## Sprint 3 (Weeks 5-6): Campaign Data + Telegram

This sprint adds live Meta campaign data to the knowledge bank and builds the entire Telegram bot integration - groups, commands, menus, pause/resume, and the message processing pipeline.

**Blockers**: Meta API access confirmed (Bullet), System User token (Bullet), Telegram bot created (IzzyAgents)

---

### S3-01: Meta Marketing API Client

**Scope**: Build a read-only client for the Meta Marketing API to fetch campaign performance data.

**Deliverables**:
- Meta API client module (httpx-based, async)
- Authentication with System User token
- Endpoints: campaigns, ad sets, ads, insights (metrics)
- Rate limit handling (429 with exponential backoff)
- Typed data models for all response types

**Test Criteria**:
- [ ] Authenticates successfully with System User token
- [ ] Fetches campaign list for a test ad account
- [ ] Fetches insights (spend, CPC, impressions) for a date range
- [ ] Handles 429 rate limit with backoff (no crash)
- [ ] Handles API errors (500, network timeout) gracefully
- [ ] Returns typed Pydantic models, not raw JSON

---

### S3-02: Campaign Data Schema + Storage

**Scope**: Create database tables for storing campaign performance data.

**Tables**:
- `campaigns` - Campaign metadata (client_id, campaign_id, name, status, objective)
- `ad_sets` - Ad set metadata (campaign_id, name, targeting, status)
- `ads` - Individual ad data (ad_set_id, name, creative, status)
- `campaign_metrics` - Time-series metrics (entity_id, entity_type, date, spend, impressions, clicks, CPC, CPM, CTR, reach, conversions, roas, leads, cost_per_lead)

**Test Criteria**:
- [ ] All tables created with correct foreign keys
- [ ] Data normalised correctly (metrics linked to campaigns/ad_sets/ads)
- [ ] Indexes on (client_id, date) for fast date-range queries
- [ ] Historical data queryable: "What was spend last week?" returns correct rows
- [ ] Upsert logic works (re-inserting same date doesn't duplicate)

---

### S3-03: Celery Polling Service

**Scope**: Build the background worker that polls Meta's API on a schedule and stores results.

**Schedules**:
- Budget/pacing: every 30 minutes
- Spend/impressions/clicks/CPC/CPM/status: every 60 minutes
- Conversions/ROAS/leads: every 4-6 hours

**Deliverables**:
- Celery worker with beat scheduler
- Task per metric group
- Error handling and retry logic
- Logging per poll cycle

**Test Criteria**:
- [ ] Celery worker starts without errors
- [ ] Scheduled tasks fire at defined intervals
- [ ] Data saved to DB after each successful poll
- [ ] Duplicate data handled via upsert (no duplicate rows)
- [ ] Failed polls logged with error detail and retried (max 3 retries)
- [ ] Poll status visible in admin dashboard (S4-07)

---

### S3-04: Historical Backfill

**Scope**: When a new client is set up, fetch the last 90 days of campaign data.

**Deliverables**:
- Backfill task triggered on client setup
- Paginated API calls for 90-day range
- Progress tracking (days processed / total)

**Test Criteria**:
- [ ] Triggering backfill for a client fetches 90 days of metrics
- [ ] Data stored correctly in campaign_metrics table
- [ ] Progress trackable (percentage or days remaining)
- [ ] Handles API pagination correctly (multiple pages of results)
- [ ] Idempotent: re-running backfill doesn't create duplicate rows
- [ ] Respects Meta API rate limits during bulk fetch

---

### S3-05: Campaign Data Freshness Indicators

**Scope**: Ensure every response that includes campaign data shows when the data was last updated.

**Deliverables**:
- `last_updated` timestamp on all campaign metric responses
- Staleness detection (data > 2 hours old flagged)
- Conversion data caveat ("typically lags 1-3 days")

**Test Criteria**:
- [ ] Every campaign data response includes `last_updated` timestamp
- [ ] Data older than 2 hours flagged as "may be stale" in response
- [ ] Conversion/ROAS/leads data always includes lag caveat
- [ ] Freshness indicator shown in both API responses and Telegram messages
- [ ] Dashboard displays last sync time per client

---

### S3-06: RAG Engine Update - Documents + Campaign Data

**Scope**: Extend the RAG engine to combine document knowledge with live campaign data when answering queries.

**Deliverables**:
- Campaign data retriever (fetches latest metrics from DB)
- Combined context builder (documents + campaign data)
- Prompt update: instruct Claude to use exact campaign numbers, never estimate

**Test Criteria**:
- [ ] "What's my CPC?" returns live campaign data from DB (not hallucinated)
- [ ] "What's our strategy?" returns document-sourced answer
- [ ] Mixed queries ("How is our campaign performing vs the strategy?") combine both sources
- [ ] Campaign numbers in responses match exactly what's in the database
- [ ] Response includes freshness timestamp for campaign data used

---

### S3-07: Telegram Bot Setup + Webhook Handler

**Scope**: Create the Telegram bot and build the webhook handler in FastAPI.

**Deliverables**:
- Bot created via @BotFather (with name, description, profile photo)
- Webhook endpoint (`POST /api/v1/telegram/webhook`)
- Secret token verification on every request
- Update deduplication via update_id in Redis
- Thin Telegram API client module (httpx, async)

**Test Criteria**:
- [ ] Bot exists and is reachable via Telegram
- [ ] Webhook endpoint receives updates from Telegram
- [ ] Secret token verified on every request; invalid tokens get 403
- [ ] Duplicate updates (same update_id) are ignored
- [ ] Bot can send a test message to a chat via the API client
- [ ] Webhook endpoint returns 200 quickly (processing is async)

---

### S3-08: Telegram Group Management Schema

**Scope**: Create database tables for managing Telegram groups and their members.

**Tables**:
- `telegram_groups` - client_id, chat_id, group_name, invite_link, bot_active, setup_token, created_at
- `telegram_group_members` - group_id, telegram_user_id, role (client/account_manager/admin), display_name, joined_at

**Test Criteria**:
- [ ] Tables created with correct foreign keys to clients
- [ ] Unique constraint on chat_id (one group per chat)
- [ ] Group-to-client lookup works: given chat_id, return client_id
- [ ] Member role enum enforced (client, account_manager, admin)
- [ ] bot_active defaults to true
- [ ] setup_token is unique and nullable (null after setup complete)

---

### S3-09: Group Setup Flow

**Scope**: Build the automated group setup flow from token generation to auto-configuration.

**Flow**:
1. Dashboard generates setup token (UUID, expires in 24 hours)
2. AM creates supergroup, adds bot
3. AM sends `/setup TOKEN`
4. Bot validates token, configures group (rename, photo, commands, invite link)
5. Invite link stored and displayed in dashboard

**Test Criteria**:
- [ ] Admin dashboard generates a unique setup token
- [ ] `/setup` with valid token configures the group (name, photo, commands)
- [ ] `/setup` with invalid/expired token returns error message
- [ ] Invite link generated with member limit and stored in DB
- [ ] Setup token marked as used after successful setup
- [ ] Welcome message with inline keyboard pinned to group
- [ ] Bot registered as admin in the group

---

### S3-10: Bot Commands - Client-Facing

**Scope**: Implement the client-visible bot commands.

**Commands**:
- `/menu` - Display main inline keyboard
- `/ask [question]` - Process question through RAG and return answer
- `/report` - Show report sub-menu (today, this week, this month)
- `/help` - Display usage guide

**Test Criteria**:
- [ ] `/menu` sends a message with inline keyboard buttons
- [ ] `/ask What's my CPC?` processes through RAG and returns accurate answer
- [ ] `/ask` without a question prompts user to include their question
- [ ] `/report` shows sub-menu with today/week/month options
- [ ] Report buttons return formatted campaign summary for selected period
- [ ] `/help` returns a clear usage guide with examples
- [ ] All commands work in group context (not just 1:1)

---

### S3-11: Bot Commands - Account Manager Only

**Scope**: Implement AM-exclusive commands, hidden from client's command list.

**Commands**:
- `/pause` - Pause bot in group
- `/resume` - Resume bot
- `/status` - Show bot status and client config
- `/escalate` - Flag conversation for review
- `/note [text]` - Add internal note (DM confirmation)
- `/refresh` - Trigger immediate Meta data pull

**Test Criteria**:
- [ ] Commands hidden from client's command list (BotCommandScopeChatMember)
- [ ] Client executing `/pause` receives "This command is for account managers only"
- [ ] `/status` returns: bot active/paused, client name, last sync time, document count
- [ ] `/escalate` creates escalation record and confirms in group
- [ ] `/note Important client` sends DM confirmation to AM (not visible in group)
- [ ] `/refresh` triggers immediate Meta API pull and confirms completion

---

### S3-12: Inline Keyboard Menus + Navigation

**Scope**: Build the interactive inline keyboard system for the bot.

**Deliverables**:
- Main menu keyboard (Ask, Report, Help)
- Report sub-menu (Today, This Week, This Month, Back)
- AM-extended menu (includes Pause, Status, Escalate)
- Navigation via `editMessageReplyMarkup` (update existing message, don't send new ones)

**Test Criteria**:
- [ ] Menu buttons render correctly on mobile and desktop
- [ ] Sub-menu navigation updates the existing message (not a new message)
- [ ] "Back" button returns to previous menu level
- [ ] Callback queries answered immediately (no loading spinner on button)
- [ ] Maximum 2-3 buttons per row for mobile readability
- [ ] AM menu shows extra options; client menu does not

---

### S3-13: Pause/Resume Mechanism

**Scope**: Build the bot pause/resume feature for account manager conversations.

**Deliverables**:
- Pause handler (pin message with Resume button, set bot_active=false)
- Resume handler (unpin, set bot_active=true, announce return)
- Message handler checks bot_active before processing
- Optional: 4-hour auto-resume reminder

**Test Criteria**:
- [ ] AM sends `/pause`: bot sends "stepping back" message with Resume button and pins it
- [ ] Client sends `/pause`: rejected with error message
- [ ] While paused: bot ignores all messages (including @mentions and replies)
- [ ] While paused: `/resume` and Resume button still work
- [ ] AM sends `/resume`: bot unpins pause message, announces return with Open Menu button
- [ ] Pause state persists across bot restart (stored in DB, not memory)
- [ ] Dashboard shows group as "paused" status

---

### S3-14: Message Processing Pipeline

**Scope**: Build the end-to-end message processing pipeline from Telegram to RAG to response.

**Flow**:
1. Webhook receives update
2. Check: is this a bot-directed message? (@mention, reply-to-bot, command)
3. Check: is bot active in this group?
4. Resolve client_id from chat_id
5. Process through RAG engine
6. Format response for Telegram (MarkdownV2)
7. Send with inline keyboard for follow-up

**Test Criteria**:
- [ ] @mention triggers RAG query and returns answer
- [ ] Reply-to-bot-message triggers RAG query and returns answer
- [ ] Regular group message (not bot-directed) is ignored completely
- [ ] Response formatted correctly for Telegram MarkdownV2
- [ ] Inline keyboard attached to responses (Ask Another, Report, Menu)
- [ ] Data freshness indicators included in campaign data responses
- [ ] Error handling: if RAG fails, bot sends friendly error message

---

### S3-15: Telegram Group Management UI

**Scope**: Build the Telegram group management interface in the admin dashboard.

**Deliverables**:
- "Set Up Telegram Group" button on client detail page
- Setup instructions display with copyable token
- Group status indicator (active, paused, pending)
- Invite link display (copyable)
- Pause/resume toggle (remote control)
- Member list view

**Test Criteria**:
- [ ] "Set Up Telegram Group" button generates token and shows instructions
- [ ] Token is copyable with one click
- [ ] Group status updates in real-time (active/paused/pending)
- [ ] Invite link shown and copyable after group setup
- [ ] Pause/resume toggle works remotely (updates bot_active in DB)
- [ ] Member list shows names and roles

---

### S3-16: Sprint 3 Integration Tests

**Scope**: End-to-end testing of the campaign data pipeline and Telegram bot integration.

**Test Suites**:
- Full flow: create client -> setup Telegram group -> /ask -> receive answer
- Campaign data in responses (freshness, accuracy)
- Pause/resume cycle
- Cross-client isolation via Telegram

**Test Criteria**:
- [ ] Full flow works end-to-end: client created, group set up, question asked, answer received
- [ ] Campaign data appears in bot responses with correct freshness indicators
- [ ] Pause/resume cycle: pause -> messages ignored -> resume -> messages processed
- [ ] Cross-client isolation: Group A's query returns nothing from Client B's data
- [ ] Campaign data freshness timestamps are correct

---

## Sprint 4 (Weeks 7-8): Escalation, Audit + Pilot

The final sprint builds the escalation system, comprehensive audit logging, system health monitoring, and culminates in a pilot deployment with 3-5 real clients.

---

### S4-01: Escalation Trigger System

**Scope**: Build the automated system that detects when a query should be escalated to a human.

**Triggers**:
- Low AI confidence (retrieval quality below threshold)
- Sensitive keyword detection (billing, complaint, strategy, contract, cancel)
- Client explicitly asks for a human ("speak to someone", "talk to my AM")
- No relevant data found for the query

**Deliverables**:
- Escalation detector module (runs on every bot response)
- Keyword list (configurable)
- Confidence threshold (configurable)
- Telegram escalation message sent in group

**Test Criteria**:
- [ ] Low-confidence response auto-flagged as escalation
- [ ] "I want to speak to someone" triggers escalation
- [ ] "What's our billing status?" triggers escalation (sensitive keyword)
- [ ] "What should our targeting be?" triggers escalation (strategy)
- [ ] Query with no relevant data triggers escalation
- [ ] Escalation message sent in Telegram group with AM's name
- [ ] Escalation record created in database

---

### S4-02: Escalation Queue Schema + API

**Scope**: Build the database schema and API for managing escalations.

**Table**: `escalations` - id, client_id, group_id, trigger_type, original_query, ai_draft_response, status (pending/claimed/resolved), claimed_by, resolved_at, resolution_notes

**Endpoints**:
- `GET /api/v1/escalations` - List (filterable by status, client)
- `PUT /api/v1/escalations/{id}/claim` - Claim an escalation
- `PUT /api/v1/escalations/{id}/resolve` - Resolve with response

**Test Criteria**:
- [ ] Escalations table created with correct schema
- [ ] List endpoint returns pending escalations, filterable by status
- [ ] Claim locks escalation to the claiming user
- [ ] Already-claimed escalation returns conflict error (409)
- [ ] Resolve stores response and notes; sets status to resolved
- [ ] Metrics calculable: average time from creation to resolution

---

### S4-03: Escalation Queue UI

**Scope**: Build the escalation management interface in the admin dashboard.

**Deliverables**:
- Pending escalations list (sorted by priority/age)
- Claim button (assigns to current user)
- Escalation detail view (original query, AI draft, conversation context)
- Edit and send response workflow
- Resolved escalations in history tab

**Test Criteria**:
- [ ] Pending escalations displayed with trigger type and age
- [ ] Claim button assigns escalation to logged-in user
- [ ] AI draft response shown and editable
- [ ] Conversation context (previous messages) visible
- [ ] "Send Response" delivers message to the Telegram group
- [ ] Resolved escalation moves to history with resolution details

---

### S4-04: Conversation History Storage

**Scope**: Store all Telegram conversation messages for reference and analysis.

**Table**: `messages` - id, group_id, client_id, sender_type (bot/client/account_manager), telegram_user_id, content, bot_confidence, data_sources, created_at

**Test Criteria**:
- [ ] All Telegram messages (bot + human) stored in messages table
- [ ] Messages linked to correct client via group_id
- [ ] Bot responses include confidence score
- [ ] Bot responses include data sources used (document IDs, metric types)
- [ ] Messages searchable by content and date range
- [ ] Storage does not impact message processing latency (<100ms overhead)

---

### S4-05: Conversation History UI

**Scope**: Build the conversation viewer in the admin dashboard.

**Deliverables**:
- Client-filterable conversation list
- Message timeline (chat-style display)
- Bot response metadata (confidence, sources)
- Search within conversations
- Export capability (CSV)

**Test Criteria**:
- [ ] Conversation list filterable by client
- [ ] Messages display sender, timestamp, and content
- [ ] Bot messages show confidence score and sources used
- [ ] Search returns messages matching query text
- [ ] Export generates CSV with all message fields
- [ ] Pagination handles large conversation histories (100+ messages)

---

### S4-06: Comprehensive Audit Logging

**Scope**: Log every significant action for security, compliance, and debugging.

**Events logged**:
- Data access (who queried which client's data)
- Bot responses (query, response, confidence, sources)
- Escalations (created, claimed, resolved)
- Document access (upload, delete, re-process)
- Authentication events (login, failed login, token refresh)
- Group events (created, paused, resumed, member joined)

**Table**: `audit_log` - id, timestamp, user_id, action_type, resource_type, resource_id, client_id, details (JSONB), ip_address

**Test Criteria**:
- [ ] Every data access creates an audit log entry
- [ ] Every bot response logged with full context
- [ ] Every escalation action logged
- [ ] Every document action logged
- [ ] Audit log viewable in admin dashboard (admin role only)
- [ ] Non-admin users cannot access audit log (403)
- [ ] Audit log entries are immutable (no UPDATE/DELETE)

---

### S4-07: System Health Monitoring UI

**Scope**: Build a system health dashboard for monitoring all integrations.

**Metrics**:
- Meta API polling status per client (last successful pull, errors)
- Telegram bot connection status (webhook active, last update received)
- Data freshness per client (last campaign data sync)
- Document processing queue (pending, processing, failed counts)
- Error rate (last 24 hours)

**Test Criteria**:
- [ ] Meta API status shows last successful poll time per client
- [ ] Telegram bot status shows webhook health
- [ ] Data freshness shows time since last sync per client
- [ ] Document queue shows count by status
- [ ] Error rate dashboard shows trend over last 24 hours
- [ ] Stale data alerts highlighted (client with no sync > 2 hours)

---

### S4-08: Documentation Wiki

**Scope**: Build an in-dashboard documentation/wiki section that provides clear, searchable instructions for every feature of the system. Targeted at Bullet Digital Media's team (non-technical users).

**Deliverables**:
- Wiki section in admin dashboard sidebar navigation
- Markdown-rendered help pages organised by feature area
- Table of contents / category navigation
- Search within documentation
- Content stored as static markdown files in the codebase (version-controlled, updated by developers via deploy)

**Pages** (minimum):
1. Getting Started - Dashboard overview, navigation, login
2. Client Management - Creating, editing, managing clients
3. Document Management - Uploading documents, supported formats, video transcription, processing status, re-processing
4. Internal Query Tool - How to ask questions, understanding responses, source citations, confidence scores
5. Telegram Groups - Setting up a group, setup tokens, invite links, managing groups
6. Bot Commands - Client commands (/menu, /ask, /report, /help), AM commands (/pause, /resume, /status, /escalate, /note, /refresh)
7. Escalation Management - Viewing escalations, claiming, responding, resolution workflow
8. Campaign Data - Understanding metrics, freshness indicators, data lag, scheduled reports
9. System Health - Reading the health dashboard, what alerts mean, troubleshooting
10. FAQ - Common questions and answers

**Test Criteria**:
- [ ] Wiki section accessible from sidebar navigation
- [ ] All 10 help pages render correctly with formatted content
- [ ] Table of contents links navigate to correct sections
- [ ] Search returns relevant help pages for queries like "how to upload a document"
- [ ] Pages render correctly in dark mode
- [ ] Content loads from markdown files (not hardcoded in components)
- [ ] Non-authenticated users cannot access wiki (redirects to login)

---

### S4-09: Cross-Client Data Isolation Test Suite

**Scope**: Automated test suite that validates data isolation at every layer before deployment.

**Scenarios** (minimum 10):
1. DB: Client A session queries Client B's documents -> 0 results
2. DB: Client A session queries Client B's campaign data -> 0 results
3. API: Client A token requests Client B's documents -> 403
4. API: Client A token requests Client B's query endpoint -> 403
5. RAG: Query in Client A context retrieves 0 chunks from Client B
6. Telegram: Message from Group A processes with Client A context only
7. Escalation: Client A's escalation not visible to Client B's AM
8. Audit: Client A's audit log not visible to Client B's admin
9. Campaign: Client A's metrics API returns 0 of Client B's data
10. Search: Semantic search in Client A context returns 0 of Client B's embeddings

**Test Criteria**:
- [ ] All 10+ scenarios pass (0 cross-client data leakage)
- [ ] Tests run automatically before every deployment
- [ ] Test report generated with pass/fail per scenario
- [ ] Any single failure blocks deployment
- [ ] Tests use realistic data (not empty datasets)

---

### S4-10: Security Audit + Hardening

**Scope**: Comprehensive security review and hardening before pilot.

**Checks**:
- CORS configuration (only allowed origins)
- Rate limiting on public-facing endpoints
- Input sanitisation on all user inputs
- SQL injection test suite
- Environment variables audit (no secrets in code)
- Dependency vulnerability scan

**Test Criteria**:
- [ ] CORS rejects requests from non-whitelisted origins
- [ ] Rate limiting triggers after threshold on auth endpoints
- [ ] XSS payloads in message content sanitised
- [ ] SQL injection attempts return errors (not data)
- [ ] No secrets found in codebase (grep for API keys, tokens, passwords)
- [ ] `npm audit` reports 0 critical/high vulnerabilities
- [ ] `pip audit` reports 0 critical/high vulnerabilities

---

### S4-11: Pilot Deployment (3-5 Clients)

**Scope**: Deploy the full system to staging with real client data for 3-5 pilot clients.

**Steps**:
1. Deploy to Render.com staging environment
2. Create pilot clients in the system
3. Upload real client documents
4. Connect Meta ad accounts
5. Create Telegram groups for each pilot client
6. Staff test: internal query tool for all pilot clients
7. Client test: invite pilot clients to Telegram groups

**Test Criteria**:
- [ ] Staging environment fully operational
- [ ] 3-5 pilot clients created with real data
- [ ] Documents uploaded and processed (status: ready)
- [ ] Campaign data pulling for connected ad accounts
- [ ] Telegram groups created and configured for each pilot
- [ ] Staff can query via admin dashboard and get accurate answers
- [ ] Clients can query via Telegram and get accurate, isolated answers
- [ ] Escalation flow tested end-to-end with real scenario

---

### S4-12: Phase 1 Final Acceptance Testing

**Scope**: Validate all Phase 1 success criteria and prepare for production handover.

**Success Criteria** (from Phase 1 plan):
1. Staff can query any client's knowledge base and get accurate, sourced answers
2. Campaign metrics pulled automatically and displayed with freshness indicators
3. Telegram AI bot responds in client groups with correct, isolated data
4. Bot pause/resume works correctly for account manager conversations
5. Menu system and inline keyboards provide intuitive navigation
6. Human escalation works for uncertain, sensitive, or complex queries
7. No cross-client data leakage in automated testing
8. 3-5 pilot clients successfully tested by staff

**Test Criteria**:
- [ ] All 8 success criteria verified and documented
- [ ] Load test: simulated 100 concurrent client groups
- [ ] Full security test suite passes (S4-08 + S4-09)
- [ ] Performance benchmarks met (query < 5s, webhook < 200ms)
- [ ] Stakeholder sign-off checklist completed (John, Stephen)
- [ ] Production deployment plan documented
- [ ] Monitoring and alerting configured

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
