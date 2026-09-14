# S1-26b/c round 17, B2: the pool-exhaustion hole that opens before `begin_action`

**What this is**: the option analysis and decision record for B2, produced during
S1-26b/c ROUND 17 (14/09/2026) and committed alongside the change it justifies.
`db/session.py`'s corrected pool comment points here for its derivation, so this
document is that comment's rationale and must not drift from it.

**Status**: RULED AND IMPLEMENTED, 14/09/2026. This began as the STOP-1 spec with
implementation waiting on a ruling, and everything from section 0 down is
preserved AS WRITTEN at that point, recommendation included, so the reasoning
that produced the decision stays auditable rather than being tidied to match the
outcome. The ruling was **option C-plus**: the retriable belt described in
section 1, plus an `on_failure` dead-letter recorder, which is the mechanism this
document did not yet know existed when it recommended plain C. Option A was
rejected outright (it cannot record a failure when the pool is what failed, and
it breaks the `platform_actions.client_id` foreign key on the not-found path).
Option B, the dedicated GHL pool, is carded to S1-26l. The `on_failure`
durability residual and the no-in-app-recovery finding are carded to S1-26e.
What actually shipped is recorded in the CHANGELOG entry for 14/09/2026.

**Orientation.** Read `docs/s1-26bc-round16-pool-starvation-spec.md` first if you
have not: this document is its sequel and inherits its arithmetic, its decision
table and its Neon reading. Round 16 split one pool into two so that a fan-out
holding connections across GHL calls could not starve the dashboard. That fix is
correct and is not revisited here. What round 16 also did was write a sentence
claiming the worker side degrades *visibly*, and that sentence is false. This
document is about the invisible half.

---

## 0. The gap, stated with what was executed

`create_ghl_subaccount_core`'s first statement is the client SELECT at
`worker/ghl_subaccount.py:1067`. `begin_action` is at `:1149`, eighty-two lines
later. A pooled connection is acquired at the FIRST statement, not when the
session is opened, so the checkout happens at the SELECT and therefore strictly
before any `platform_actions` row exists.

Executed 14/09/2026 against the local Docker Postgres, `pool_size=1,
max_overflow=0, pool_timeout=1`:

```
after `async with WorkerSessionLocal() as session:`          checkedout=0
after the FIRST statement (the client SELECT), no commit     checkedout=1
a SECOND run while saturated                                 sqlalchemy.exc.TimeoutError
  QueuePool limit of size 1 overflow 0 reached, connection timed out, timeout 1.00
```

And the classification, also executed:

```
issubclass(sqlalchemy.exc.TimeoutError,
           (ClientNotFoundError, GhlClientError, GhlNotConfiguredError))  ->  False
MRO: TimeoutError -> SQLAlchemyError -> HasDescriptionCode -> Exception -> BaseException
```

So under pool exhaustion the run raises `sqlalchemy.exc.TimeoutError` after
`WORKER_POOL_TIMEOUT` (30s), with **no action row**, and the wrapper's three
`except` clauses at `:2153-2163` do not name it.

**Three consequences, and I am separating the measured from the inferred because
round 17's own new rule requires it.**

1. **Measured**: there is no `platform_actions` row, so the dashboard shows
   nothing at all for that signing. Not a failed action, not an `in_progress`
   zombie. Nothing.
2. **Measured**: the exception is unmapped by the wrapper and propagates as a
   raw `SQLAlchemyError` subclass.
3. **NOT measured, and it materially changes the severity**: unmapped is not the
   same as `NonRetriable`. Inngest should therefore RETRY this run rather than
   dead-letter it immediately, which means a signing is lost only if the
   saturation outlasts the retry budget. **I have not verified the retry budget
   for this function.** The decorator at `:2010-2116` sets `throttle` and
   `concurrency` and I found no `retries=` argument, so it is presumably the
   Inngest default, but "presumably" is exactly what this project forbids from
   entering a rationale. Before any sizing argument in this document is relied
   on, that number must be looked up and stated.

The healing path is also closed, and that part is not new: `reconcile_pandadoc`
cannot re-emit the signing because its `ON CONFLICT DO NOTHING` sees the
`onboarding_events` row already present. That exact mechanism is documented at
`worker/client_record.py:597` for the schema-drift case, which is the same shape
as this one - a failure that raises before `begin_action`.

**The round-16 sentence this falsifies**, `db/session.py:96-99`:

> The worker gets the larger capacity deliberately: exceeding it must degrade
> the FAN-OUT (a run waits, fails visibly through `_record_failure`, and Inngest
> retries it) rather than the dashboard, which is the failure being fixed.

"Fails visibly through `_record_failure`" is false, and it is false in the one
case the sentence exists to describe. When the worker pool is the thing that is
exhausted, the run never reaches `begin_action`, so there is no action row for
`_record_failure` to update and `_record_failure` is never called at all. The
pool sizing in round 16 was chosen against this sentence.

---

## 1. Option analysis

### Option A: claim first

Move `begin_action` ahead of every other statement, and convert a pool
`TimeoutError` into a recorded action.

**Checkout trace, statement by statement:**

1. `async with WorkerSessionLocal() as session:` - no checkout (measured above).
2. `begin_action(...)` issues the `INSERT ... ON CONFLICT` - **this is now the
   first statement, so the checkout happens here**.
3. Under total exhaustion this checkout waits `pool_timeout` and raises
   `TimeoutError`. There is still no action row.

**THE HOLE, stated precisely as the brief asks.** Recording the failure requires
a write. A write requires a connection. The pool is the thing that failed.
Claim-first moves the failure one statement earlier and does not close it: at
total exhaustion there is no resource left on that engine with which to record
anything. The question "what records the failure when the pool is what failed"
has only three answers:

- **The api engine.** It is a different pool, so it would work. It is also the
  dashboard's pool, and reaching into it under worker saturation reintroduces
  precisely the coupling round 16 removed, at the exact moment the dashboard is
  most likely to be under load. Rejected.
- **Inngest backoff outlasting the saturation window.** This is not a recording
  mechanism, it is a bet: the failure is visible in Inngest's own UI and
  invisible in the dashboard, and the signing survives if and only if capacity
  frees up inside the retry budget. It is an acceptable BELT and not an
  acceptable primary answer, because the project's constraint is that partial
  failures are visible in the dashboard.
- **Both.** Which in practice means the belt, plus accepting the dashboard gap
  for this one failure mode.

**Option A additionally breaks two things, and this is what rules it out rather
than merely weakening it:**

- `platform_actions.client_id` is a foreign key to `clients`. Today
  `ClientNotFoundError` raises at `:1078`, BEFORE the action row is claimed, and
  the function's own docstring calls that "the one failure that legitimately
  leaves no `platform_actions` row". Claiming first means attempting an INSERT
  for a client row that may not exist, which fails the FK. Claim-first is
  therefore not a reordering, it is a redesign of the not-found path.
- The already-provisioned short-circuit at `:1087` calls `begin_action` inside
  its own branch, deliberately. Hoisting the claim above the client SELECT means
  claiming before we know whether the client is already provisioned, changing
  what `already_succeeded` means on the torn path - which is the exact flag B1
  has just finished disentangling in this same round.

**Verdict on A: rejected.** It does not close the hole, and it disturbs two
paths that rounds 13 and 17 deliberately settled.

### Option B: a dedicated GHL pool

Three engines. Long phase-2 holds move to their own `ghl_engine`; short
transactions (the client SELECT, `begin_action`, `_record_failure`) stay on the
worker engine.

**Checkout trace:**

1. Client SELECT - checks out from the **worker** pool.
2. `begin_action` + commit - same worker connection, released at the commit.
3. Phase-2 lock, the two GHL calls and the write-back - check out from the
   **ghl** pool and hold for the duration.
4. Exhaustion of the ghl pool therefore occurs at step 3, which is **after the
   action row exists and is committed**. `_record_failure` runs on the worker
   session, whose pool is not the saturated one, so the failure is recorded and
   the dashboard shows it.

That is the property Option A cannot buy: the resource that records the failure
is structurally not the resource that ran out.

**What it costs:**

- Two sessions inside one function, with the advisory lock held on the ghl
  session while `_record_failure` writes on the worker session. That is two
  transactions, which is safe here because the failure record is independent of
  the lock, but it is a real increase in the function's complexity and this
  function is already the one carrying an extraction card (S1-26l) for being too
  complex.
- **Neon budget**: a third pool. Round 16 ships worker 5/15 (capacity 20) and
  api 5/10 (capacity 15), total 35, plus Alembic's one direct connection during
  a deploy. A ghl pool sized 5/15 takes the total to 55 against the measured
  ceiling of **901** (`SHOW max_connections`, staging primary at the 0.25 CU
  floor, 10/09/2026). Not a constraint.
- **Round 16's pool tests and manifest entries that need updating**: the binding
  test (`test_the_worker_and_the_api_do_not_share_a_pool`), both sizing tests,
  round 17's new AST test `test_every_pool_kwarg_is_passed_explicitly` (a third
  parametrize case), `test_each_shipped_pool_kwarg_differs_from_the_library_
  default_or_says_so` (three more constants to classify), the connect_args test,
  and `test_no_worker_module_reaches_for_the_api_sessionmaker` (which currently
  asserts worker modules mention no `AsyncSessionLocal`; it would need to
  express which modules may use which of three sessionmakers). On the manifest
  side that is the six per-kwarg entries round 17 just created becoming nine,
  plus a binding entry for the new engine.

**Verdict on B: it is the only option that closes the hole.** It is also a
materially larger diff than anything else in round 17.

### Option C (not requested, offered because diff growth is the governing risk): the belt alone

Map `sqlalchemy.exc.TimeoutError` in the wrapper, log it loudly, and card the
structural fix.

This does not make the failure visible in the dashboard. It makes it legible
rather than an unmapped stack trace, it names the cause, and it is roughly a
six-line diff with one test. The signing still depends on Inngest's retry budget
outlasting the saturation.

It is the honest minimum, and given that this PR was pre-approved for merge two
rounds ago and diff growth is the reviewer's named risk, it deserves to be on
the table next to B rather than discovered later.

### The belt, for A, B or C

Map `sqlalchemy.exc.TimeoutError` (or `SQLAlchemyError` broadly, narrowed to the
timeout by inspection) in the wrapper so the worst case is legible.

**One disagreement with the brief, stated rather than quietly implemented.** The
brief says the belt should ensure the worst case "dead-letters with a legible
error, never an unmapped stack". Dead-lettering is the wrong terminal state
here: pool exhaustion is definitionally transient, and `inngest.NonRetriableError`
would terminally fail a signing that the next attempt would very likely
complete. That is the same mistake round 5 corrected when it narrowed
`except RuntimeError` (which was dead-lettering `httpx.StreamError` transport
failures), and the same one round 16 corrected on the lookup's 2xx guard.

**Recommended belt**: catch it, log at ERROR with the client id and the pool
name so an operator can see which pool saturated, and **re-raise unchanged** so
Inngest retries. Legible, not terminal. If the budget is exhausted Inngest
dead-letters it on its own, which is the correct place for that decision.

---

## 2. The proof test

The reviewer's chain, executed rather than asserted:

1. Saturate the relevant pool (the worker pool under today's code and under
   Option A; the ghl pool under Option B) by holding connections open in an
   uncommitted transaction, exactly as `test_a_saturated_worker_pool_leaves_the_
   api_pool_serving` already does.
2. Dispatch run N+1 through the Inngest wrapper.
3. Assert the final state contains a `platform_actions` row recording the
   failure - or, under Option C, that the mapped error was raised and logged
   with the pool named.
4. Assert reconcile's skip is no longer reachable for this shape, by showing the
   action row exists and so the dashboard has something to retry from.

**Seams the test needs, and only one of them exists today:**

- **A tiny-pool engine**: exists, but the current saturation test builds its OWN
  engines rather than exercising the shipped ones, which is carded as a gap. The
  proof test needs the same affordance and inherits the same caveat: it proves
  the seam, not the sizing.
- **An injectable session factory**: does NOT exist. `WorkerSessionLocal` is a
  module global referenced directly inside the wrapper at `:2142`, so the test
  must monkeypatch the module attribute. That works and is ugly. If Option B
  ships, the two factories should be passed in or resolved through one accessor,
  which makes both this test and the next fan-out's tests honest.
- **A way to invoke the wrapper**: exists, `fn._handler`, precedent set in
  S1-25c.
- **A pool-name signal in the error**: does not exist and Option C needs it,
  since under C the assertion is on the log line rather than on a row.

---

## 3. The corrected comment

`db/session.py:96-99`, under whichever option ships. Proposed wording under
Option B:

> The worker gets the larger capacity deliberately, and the GHL pool larger
> still: exceeding the GHL pool degrades the FAN-OUT (the run waits, then fails
> visibly through `_record_failure`, because the action row was claimed and
> committed on the WORKER pool before any long hold began) rather than the
> dashboard. Exhausting the WORKER pool is the case with no visible failure,
> because the checkout that fails is the one before `begin_action`; that is what
> the third pool exists to make unreachable in practice.

Under Option C the same paragraph must instead say plainly that worker-pool
exhaustion produces no action row and is visible only in Inngest, which is a
disclosure rather than a fix.

Either way the current sentence does not survive: it claims `_record_failure`
runs in a case where it provably cannot.

---

## 4. Does round 16's sizing survive?

**Under Option C: yes, unchanged.** Nothing about the arithmetic moves.

**Under Option B: no, and it should be re-derived rather than carried across.**
Round 16's worker capacity of 20 was sized for the long holds. Under B the long
holds move to the ghl pool, so:

- **GHL pool** inherits the round-16 arithmetic verbatim: `Throttle(limit=5,
  period=10s)` admits 0.5 run starts/second sustained, a phase-2 hold is 20s
  uncontended and up to 50s behind the lock's own 30s `SET LOCAL` ceiling.
  Little's law `L = 0.5 x 50 = 25` at the worst case, 15 at 30s, 10 at 20s.
  Round 16 chose capacity 20 against that same distribution on the deliberate
  basis that exceeding it degrades the fan-out visibly, and under B that
  reasoning becomes true rather than aspirational. So **ghl 5/15, timeout 30**.
- **Worker pool** now serves only short transactions: the client SELECT,
  `begin_action` plus commit, `_record_failure`, and the other fan-outs. Its
  occupancy is bounded by service time in the low tens of milliseconds rather
  than tens of seconds, so capacity 20 is far more than it needs. It should
  NOT be shrunk in the same change, though: the other fan-outs (`client_record`,
  `sales_summary`, `signed_pdf`, `meet_transcript`) share it, three of them hold
  a connection across an Inngest emit (S1-26s item 1), and none of that has been
  measured. Leave it at 5/15 and card the resize.
- Total becomes 55 against 901. Still not a constraint.

The Little's-law caveat from round 16 carries over unchanged: it gives a MEAN
occupancy under a stationary arrival process, and a burst can exceed it. It
bounds the steady state, not the worst instant.

---

## 5. Do-not-touch list

1. **The advisory lock semantics.** `pg_advisory_xact_lock` is
   transaction-scoped, so the hold IS the serialisation and committing early to
   release the connection reopens round 12's P1.4 cross-bucket race. Under
   Option B the lock moves to the ghl session; it must still be taken and
   released by transaction boundaries, never shortened.
2. **The 30s `SET LOCAL statement_timeout`** on the dedup lock, and its restore
   to the LITERAL `'5s'` rather than `= DEFAULT` (round 15 measured that
   `= DEFAULT` restores the startup value, which is `"0"` on Neon). A third
   engine must carry the same `connect_args` as the other two for exactly this
   reason.
3. **B1's placement**, settled earlier this round: `treat_as_succeeded = False`
   belongs on the fall-through path only, after the recovery try/except. The
   recovery half must keep declining to overwrite a success whose id is still on
   the action row.
4. **The round-15 and round-16 reviewer-verified fixes**: the ALLOW-side
   enumeration, the phone-filler merge gap, the three P1s of round 16, and the
   pool split itself.
5. **`identity_key.py`**, which nothing in B2 touches and which would drag the
   G7 fingerprint and the value-invariance sweep into a change that has no
   business involving them.

---

## 6. Recommendation

**Option C now, Option B on its own card**, unless the reviewer's appetite for
diff growth has changed.

The reasoning is the reviewer's own: this PR was pre-approved for merge at round
16, "more rounds on a diff this size is now the riskier option", and B2 is a
pre-existing hole rather than a regression this PR introduced. Option B is
correct and is also a third engine, a second session inside the most complex
function in the codebase, nine manifest entries, six test rewrites and a
resizing argument that needs a number I have not yet looked up.

Option C makes the failure legible, costs about six lines, and leaves a
truthful comment in `session.py` describing exactly what is and is not visible.
That is a disclosure the dashboard's operator can act on, and it does not spend
the merge.

If the ruling is B, it should be B in its own PR on top of this one, sequenced
alongside S1-26l, rather than appended to a diff this size.

**Whichever way it goes, the false sentence at `db/session.py:96-99` is
corrected in THIS round.** It is three lines, it is already wrong, and leaving a
comment that claims a guard runs where it cannot is the ninth-guard shape all
over again.
