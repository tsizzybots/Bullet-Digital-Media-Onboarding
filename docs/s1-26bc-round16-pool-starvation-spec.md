# S1-26b/c round 16, item 3: pool starvation under sustained GHL slowness

> **What this is:** the derivation behind the pool-size constants in
> `apps/api/src/bullet_api/db/session.py` (`API_POOL_SIZE` and friends), written
> for S1-26b/c review round 16 on 10/09/2026 and approved the same day. If you
> arrived here from that file's comment, section 2 is the arithmetic, section 1a
> is the Neon ceiling the sizes are checked against, and section 5 is the list of
> things a change here must not break.

**Status: APPROVED 10/09/2026 and IMPLEMENTED.** The reviewer ruled on the spec:
dedicated worker engine, proposed sizes accepted (worker 5/15/30, api 5/10/10,
api `pool_timeout` 10s), with the commit gated on the Neon console reading in
section 1a. Sizes are named constants so that reading adjusts one place; the
decision table below is applied to the numbers, not re-argued.

Shipped: `worker_engine` + `WorkerSessionLocal` in `db/session.py`, all six
worker modules repointed, six tests in `tests/test_worker_pool_isolation.py`,
three mutation-manifest entries (all verified KILLED individually).

**Section 3 carries a CORRECTION to this spec's own first draft.** Read it: a
sentence this document offered for the PR body verbatim did not survive being
executed, and the corrected version is a stronger claim.

Every number below was produced by a command run at `35a84ff` plus this round's
items 1 and 2; the commands are quoted inline. Two numbers could NOT be produced
here and are marked as such rather than estimated.

---

## 0. The premise, re-verified rather than assumed

| Claim in the brief | How it was checked | Result |
|---|---|---|
| The Inngest functions share a process with the dashboard's API | `render.yaml:212-233` | CONFIRMED. `bullet-worker-staging` runs `time.sleep(3600)` in a loop and its own comment says "Inngest functions served via bullet-api-staging /api/inngest". The functions execute inside the `web` service that serves `/clients`. |
| They share one engine and one pool | `grep -rn create_async_engine apps/api/src` | CONFIRMED. Exactly one production engine, `db/session.py:73`. All six worker modules import `AsyncSessionLocal` from it; so does the FastAPI `get_session` dependency. |
| The pool is 15 | measured, not read | CONFIRMED, see below. |
| `Throttle(limit=5, period=10s)` bounds starts only | `ghl_subaccount.py:2023-2031` | CONFIRMED, and the code comment already says so in those words. |
| `/healthz` is DB-free | `main.py:87-90` | CONFIRMED. Returns a literal `{"status": "ok"}`. Render's health check therefore stays green through total pool exhaustion, so the platform never restarts the service. |

Pool parameters, measured rather than inferred from SQLAlchemy's documented
defaults:

```
$ uv run python -c "from bullet_api.db.session import engine; p = engine.pool; ..."
pool class     : AsyncAdaptedQueuePool
pool_size      : 5
max_overflow   : 10
pool_timeout   : 30.0
total capacity : 15
sqlalchemy     : 2.0.49
```

`db/session.py:73` passes no pool arguments at all, so these are SQLAlchemy's
defaults arriving by omission. Nobody chose 15.

---

## 2. The arithmetic (asked for before the option, because it decides the option)

**Start rate.** `throttle=inngest.Throttle(limit=5, period=timedelta(seconds=10))`,
keyless. The function's own comment states the semantics: GCRA over run STARTS,
at most `limit + burst` starts per window with the SDK default burst of 1, so up
to 6 starts in one 10s window and a sustained 5 per 10s.

    lambda = 5 starts / 10 s = 0.5 starts/s   (sustained)
    burst  = 6 starts in one 10 s window      (transient)

**Hold time.** One pooled connection is held for the whole phase-2 transaction,
which begins at the phase-2 `_acquire_dedup_lock` and ends at the terminal
commit. Its components, each with its configured bound:

| Segment | Bound | Source |
|---|---|---|
| Wait on `pg_advisory_xact_lock` | 30 s | `SET LOCAL statement_timeout = '30s'`, `ghl_subaccount.py:380-387` |
| `find_location_by_email` | 10 s | `HttpGhlClient(timeout=10.0)`, `ghl/client.py:208` |
| `create_location` | 10 s | same |
| DB statements in between | under 1 s | not bounded on Neon: `statement_timeout` is discarded, see `db/session.py:23-40` |

    W(worst, uncontended) = 10 + 10           = 20 s
    W(worst, contended)   = 30 + 10 + 10      = 50 s

The 20s figure is the one the brief quotes. The 50s figure is the one that
matters, because contention is not the exceptional case here: the throttle
allows six starts in one window and the lock is keyed on email, so a
returning-client burst contends by construction.

Two caveats on W, both already disclosed elsewhere in the codebase and neither
resolved by this spec:

- `httpx.Timeout(10.0)` is PER-PHASE (connect, read, write, pool), not a total
  call budget, so a single GHL call can legitimately exceed 10 s. Already
  ticketed as S1-26m; the 30 s lock ceiling has the same disclosure.
- The engine's `statement_timeout` never reaches Neon, so no DB statement in
  the window has any ceiling at all in production.

Both push W up, never down.

**Steady-state occupancy.** Little's law, L = lambda x W:

    W = 20 s  ->  L = 0.5 x 20 = 10 connections held by the fan-out
    W = 30 s  ->  L = 0.5 x 30 = 15 connections held by the fan-out
    W = 50 s  ->  L = 0.5 x 50 = 25 connections held by the fan-out

Capacity is 15.

    Hold time at which the fan-out consumes the ENTIRE pool: 15 / 0.5 = 30 s
    Hold time at which it leaves nothing for the dashboard: the same 30 s
    Hold time at which it leaves 5 (the non-overflow pool): 20 s

So the failure does not need an outage. It needs GHL to answer in 30 seconds
instead of 2, sustained, while signings keep arriving at the rate the throttle
already permits. At W = 50 s the fan-out demands 25 of 15 connections, so 10 of
its own runs are also queueing on the pool, each burning `pool_timeout = 30 s`
before raising.

**What the dashboard sees.** Every `get_session` request queues on the same
`AsyncAdaptedQueuePool`, waits up to 30 s, then raises
`sqlalchemy.exc.TimeoutError`, which surfaces as a 500. The dashboard polls
`refetchInterval` per open tab, so every open tab generates this repeatedly.
`/healthz` keeps returning 200 throughout, so Render neither restarts nor alerts.

---

## 1. The option, and the trade

**Recommended: a dedicated engine and pool for the worker (option A).**

`db/session.py` grows a second engine, `worker_engine`, and a second
sessionmaker, `WorkerSessionLocal`, built from the same URL and the same
`connect_args`. The six worker modules import that instead of
`AsyncSessionLocal`. Explicit sizes on BOTH engines, so no pool is ever again
whatever SQLAlchemy's defaults happen to be.

Proposed sizes, derived from the arithmetic above rather than picked:

    worker pool  : pool_size=5,  max_overflow=15, pool_timeout=30  (capacity 20)
    api pool     : pool_size=5,  max_overflow=10, pool_timeout=10  (capacity 15)

Worker capacity 20 covers L at W = 40 s and is one queue-length short of the
W = 50 s worst case, deliberately: the point of the split is that exceeding it
degrades the FAN-OUT (a run waits, then fails visibly through `_record_failure`
and Inngest retries it) instead of the dashboard. Sizing the worker pool to
never queue would just move the unbounded growth somewhere else.

The api `pool_timeout` drops from 30 s to 10 s. A dashboard request that cannot
get a connection in 10 s should fail fast rather than hold a worker coroutine
for half a minute; nothing on the dashboard's read path is worth a 30 s wait.

**Why not option B (sizes on the shared engine).** It is the smaller diff and it
does raise the threshold, but it does not change the SHAPE of the failure: one
pool means the dashboard's availability stays a function of GHL's latency, and
the only question is which multiple of today's latency breaks it. The property
worth buying is independence, and only two pools buy it. If the reviewer wants
the smaller diff this round, option B with `pool_size=10, max_overflow=30,
pool_timeout=10` (capacity 40) makes the realistic case safe and leaves the
shape for S1-26l's neighbourhood; that is a defensible interim, not a fix.

**Neon's connection limit gates these sizes. Section 1a is that question, answered
as far as our own records allow.**

---

## 1a. Neon's limits: what is recorded, what is measured, what must be read

Three tiers, kept apart on purpose.

**RECORDED AS INTENDED, not confirmed as provisioned.** `docs/infrastructure.md:37`
specifies the **Launch** plan at approximately $19 USD/month, and the cost table
at `:219` repeats it. Both are client-onboarding documents describing what to
buy; the matching checklist item at `:242` is still an unticked box. So "Launch"
is the plan we asked for, and this spec does not treat it as the plan that is
running. Nothing below depends on the tier being Launch.

**MEASURED, by us, against the real endpoints.** From the 07/09/2026
platform-discovery entry and the 18/05/2026 decision it corrects:

- The app runtime always connects to Neon's **pooled** endpoint (`-pooler`
  hostname). Direct endpoints are reserved for Alembic, because transaction
  pooling does not support the DDL paths migrations need. Decision recorded
  18/05/2026, re-confirmed by execution 07/09/2026.
- Staging endpoint pair, both probed read-only on 07/09/2026:
  `ep-mute-mode-ab75uu4u-pooler` and `ep-mute-mode-ab75uu4u`.
- On BOTH endpoints, `pg_advisory_xact_lock` acquires and releases, and
  `SET LOCAL` takes effect. This is the fact that matters most here and it is
  already ours: **the dedup lock is unaffected by pooling**, so nothing in this
  fix has to move off the pooled endpoint to keep working.
- On BOTH endpoints, `server_settings` is discarded. Consequence for sizing,
  spelled out because it cuts the wrong way: there is no `statement_timeout`
  bounding any statement in the hold window, so W has no ceiling except the
  ones the code sets for itself.

**READ 10/09/2026. `max_connections` = 901.**

| | |
|---|---|
| Value | **901** |
| Read against | the staging project's PRIMARY (`main` branch) |
| Compute state | idle, at the **0.25 CU floor** |
| Method | Neon SQL Editor, `SHOW max_connections;` |
| Plan | **Launch**, confirmed from the dashboard (previously only specified in `infrastructure.md`, now confirmed as provisioned) |

**Decision: the table's top row applies.** C = 901 against a demand of 35 (worker
20 + api 15) plus Alembic's one direct connection. Sizes ship exactly as
proposed: worker 5/15/30, api 5/10/10, api `pool_timeout` 10s. No adjustment.

**SCOPE NOTE ON THE READING, recorded because it is not a complete answer to the
question this section originally asked.** Two numbers were wanted; one was
captured. The reading came from the SQL Editor, which is a direct-style
connection to the primary, and the pooler's own `default_pool_size` was not
separately captured. At C = 901 that distinction is academic: the decision table
keys on the SERVER ceiling, and 901 clears every row of it by more than an order
of magnitude. Even on the pessimistic assumption that the pooler's client-side
pool sits at a common default of 64, the pair of 35 still fits. The number is
recorded as read, with its scope stated, rather than being presented as the
two-endpoint reading the section asked for.

The commands the section originally specified, kept for whoever refreshes this:

```sql
-- against the DIRECT endpoint (ep-mute-mode-ab75uu4u), the Postgres ceiling
SHOW max_connections;

-- against the POOLED endpoint (ep-mute-mode-ab75uu4u-pooler), what the app
-- actually queues behind
SHOW max_connections;
```

plus, from the Neon dashboard for each project, the pooler's configured
`default_pool_size` and the compute size.

**The autoscaling caveat, softened by how the reading was taken.** `max_connections`
on Neon scales with compute, so a project that autoscales has a moving ceiling,
and this spec originally treated that as a live risk to the sizing. It is much
weaker than that, because **the 901 was read at the 0.25 CU FLOOR**: scaling can
only move the ceiling UP from there, and there is no downscale below the floor
that could bring it under the pair's 35. What survives is a narrower residual - a
plan change, or a Neon-side change to the per-compute default - which is carded on
S1-26s item 4 as an assertion worth adding (read the ceiling at startup and refuse
or warn if the configured pools exceed it), not as a risk being carried.

**The decision rule, stated NOW so the answer plugs in rather than reopening the
argument.** Let `C` be the smaller of the two readings, and note our demand is
worker 20 + api 15 = **35**, plus one direct connection for Alembic during a
deploy:

| Reading | What follows |
|---|---|
| `C >= 50` | Take the sizes as proposed. Comfortable headroom. |
| `36 <= C < 50` | Take the split, cut the worker's `max_overflow` so the pair totals `C - 5`. The split is what buys isolation; the sizes are secondary. |
| `C <= 35` | **The split still wins, but the api pool shrinks, not the worker's.** A starved dashboard is the failure being fixed; giving it a large share of a small ceiling and leaving the fan-out to queue reproduces the problem with the pools reversed. |
| `C` is small enough that 15 total is already near it | Then the CURRENT single pool of 15 is already at the ceiling, which is a finding in its own right and outranks this one. Stop and raise it. |

In every row the answer is the split. The Neon reading moves the numbers, not
the option, which is why this spec recommended the option without waiting for
it. **The reading landed at 901 and selected the top row, so nothing moved.**
The table stays here rather than being deleted: if the compute or the plan
changes, the resize decision is already made and does not need re-arguing.

---

## 3. Consistency with `client_record.py` and `sales_summary.py`

Both were read. Neither is doing something this function could copy.

`client_record.py:35-37`: "PandaDoc fetch happens OUTSIDE the DB session. A
signed PandaDoc fetch can take seconds; holding the pooled connection during
that window would leak connections under a burst of signings."

`sales_summary.py:174-176`: "Commit the in_progress row before the (slow)
external work so a crash leaves a visible row; the commit also releases the
pooled connection."

Both avoid the problem the same way: commit, which returns the connection to the
pool, THEN do the slow thing. `create_ghl_subaccount` already does this in phase
1 (the payload commit at `ghl_subaccount.py:1520-1527` releases the phase-1
lock and its connection).

**Phase 2 cannot do it, and the reason is the fix it would undo.** The lock is
`pg_advisory_xact_lock`, transaction-scoped by deliberate choice: `ghl_subaccount.py:351-353`
records that session-scoped locks were rejected because a leaked session lock
under a connection pool blocks that email forever. Transaction scope means
committing to release the connection also releases the lock, and the lock
spanning the GHL call is precisely what round 12's P1.4 added, to close the
cross-bucket race where two racers both see zero candidates and mint two
sub-accounts with no flag.

So the answer to "should the fix follow their pattern for consistency" is no,
and the inconsistency is not an oversight to tidy away. The two patterns exist
because the two functions hold the connection for different reasons: those two
hold it incidentally, this one holds it to serialise. A fix that shortened the
hold would reopen a P1. That is why the fix belongs at the pool.

### CORRECTION (round 16): the sentence this section originally offered for the body is FALSE

The first draft of this spec ended with a line intended for the PR body verbatim:
"this makes `create_ghl_subaccount` the only fan-out that holds a pooled
connection across an external call, by design, and therefore the only one whose
latency can be another surface's outage."

**It does not survive checking, and there are three counter-examples in this
repo.** Each runs a statement, does NOT commit, and then makes an external call
with the connection still checked out:

| Site | Statement | External call made while holding |
|---|---|---|
| `sales_summary.py:181-195` | `SELECT response FROM platform_actions` | `_emit_summary_ready` (Inngest emit), on the replay path |
| `meet_transcript.py:_emit_linked_if_documented` | `SELECT id FROM documents` | `emitter.send(TRANSCRIPT_LINKED_EVENT)` |
| `client_record.py:_link_parked_transcripts` | `SELECT ... JOIN documents` | `emitter.send(...)` **once per parked transcript**, in a loop |

The mechanism was measured rather than reasoned about, because the whole point
of this round is not to ship another unchecked claim:

```
after open, before any statement : 0 checked out
after a SELECT (no commit)       : 1 checked out  <- an emit here holds it
after commit                     : 0 checked out  <- an emit here does not
```

So a `session.execute` followed by an emit with no commit between them holds a
pooled connection for the duration of that emit. That is exactly the shape at
all three sites, and it is why "only fan-out" is wrong.

**The corrected sentence, which is the one that should go in the body:**

> `create_ghl_subaccount` is not the only fan-out that holds a pooled connection
> across an external call: `sales_summary`'s replay emit,
> `meet_transcript._emit_linked_if_documented` and
> `client_record._link_parked_transcripts` each hold one across an Inngest emit,
> the last once per parked transcript. It is the only one that holds a
> connection across an external call it CANNOT release, because the hold is the
> advisory lock doing the serialising, and the only one whose held window is
> bounded in tens of seconds (two 10s GHL calls behind a 30s lock wait) rather
> than a single short POST. That is what makes its latency another surface's
> outage.

**Does this change the fix?** No, and it strengthens it. The three emit sites are
short holds that the split also protects, since they are worker code drawing
from the worker pool. It does change one thing in the arithmetic's favour: L in
section 2 counts only the GHL fan-out, so the true worker-side demand is
slightly higher than 25 at W=50s. The proposed worker capacity of 20 is
unchanged, because those holds are short enough not to move steady-state
occupancy materially, but the direction is worth recording: section 2 is a
LOWER bound on worker demand, not an exact figure.

**Carded, not fixed here:** the three emit sites should commit (or close) before
emitting, matching `client_record.py:434`'s own commit-before-emit rule which
does it correctly four lines away from a site that does not. Out of scope this
round by the diff-growth rule.

---

## 4. The proof

**Shape of the test at head.** Not "a pool can be exhausted", which is trivially
true, but "the fan-out's connections and the dashboard's connections come from
the same place".

    given  an engine with pool_size=1, max_overflow=0, pool_timeout=1
    when   one phase-2-shaped transaction holds its connection
           (BEGIN, pg_advisory_xact_lock, sleep past a GHL-call-shaped delay)
    then   a dashboard-shaped SELECT on the SAME engine raises
           sqlalchemy.exc.TimeoutError inside pool_timeout
    and    after the fix, the dashboard-shaped SELECT draws from the api engine
           and returns its row

**A reduced pool size IS needed, and this is the "say so" the brief asked for.**
Holding 15 real connections open for 50 s in a unit test is a 50-second test
that flakes on a slow machine, and it would prove the arithmetic rather than the
seam. `pool_size=1, max_overflow=0` reproduces the same seam in under 2 seconds.
The honest statement of what that test proves: it proves the sessions come from
one pool at head and two pools after, which is the change. It does NOT prove the
sizing; the sizing is proved by the arithmetic in section 2, and nothing in the
test suite can validate it without a load rig.

**A seam is also needed.** At head there is no way to ask "which engine did this
worker use" other than by importing `AsyncSessionLocal` and checking its bind,
because the engine is a module-level singleton. The test asserts on
`WorkerSessionLocal.kw["bind"]` and `AsyncSessionLocal.kw["bind"]` being
distinct engine objects, plus the behavioural exhaustion test above against a
locally built pair. No production seam is added.

Precedent for the idiom exists: `test_dedup_lock_production_settings.py:73`
already builds an engine carrying the production settings so a test cannot pass
against values production does not have, and its own docstring explains why it
uses `NullPool` rather than the production engine.

**Manifest entries owed:** one per new guard, in the same commit. At minimum:
the worker sessionmaker binds to the worker engine (mutate it back to the api
engine, the exhaustion test must fail), and the explicit pool sizes on both
engines (mutate either back to omitted, the sizing-assertion test must fail).

---

## 5. What must NOT change

Confirmed against the code, and each is reviewer-verified this round:

1. **The advisory lock stays `pg_advisory_xact_lock`, taken twice.** Phase 1
   around the sibling SELECT, phase 2 spanning the GHL call and the write-back.
   Session scope was already rejected (`ghl_subaccount.py:351-353`).
2. **The `SET LOCAL statement_timeout = '30s'` around the lock wait stays, and
   still restores the literal `'5s'` rather than `= DEFAULT`.** Round 15 proved
   `= DEFAULT` hands the rest of the transaction an unbounded budget on Neon.
   A second engine must carry the same `connect_args`, or the restore puts back
   a different value on the worker's connections than on the api's.
3. **`begin_action` stays hoisted above the phase-1 lock, committed before it.**
   Round 13's P1.5. Nothing in this fix reorders anything in the function.
4. **The commit points do not move.** The whole point of section 3 is that the
   hold cannot be shortened; a fix that "helpfully" also shortened it would undo
   P1.4.
5. **`/healthz` stays DB-free.** It is tempting to make it check the pool so
   Render restarts on starvation. That trades a degraded dashboard for a restart
   loop that kills in-flight signings, and the fan-out's own visibility already
   comes from `platform_actions`.
