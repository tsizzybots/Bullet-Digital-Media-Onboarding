"""Integration tests for the PandaDoc reconciliation cron (S1-23).

These hit Postgres (the ``onboarding_events`` upsert) via the transactional
``async_session`` fixture, so they are all marked ``@pytest.mark.db`` and skip
when no DATABASE_URL is reachable. The PandaDoc client, event emitter and Slack
notifier are injected as test doubles so no network call is made and the
emitted events / posted alerts are captured rather than sent.

Card spec: a mocked PandaDoc API returns 3 docs, 1 of which has no event row;
the cron creates the missing event and posts to a Slack mock; a subsequent run
with no new docs is a no-op.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.crons.reconcile_pandadoc import reconcile_pandadoc
from bullet_api.integrations.pandadoc_client import FakePandaDocClient, PandaDocDocument
from bullet_api.integrations.slack import FakeSlackNotifier
from bullet_api.worker import PANDADOC_SIGNED_EVENT, FakeEventEmitter

# Fixed run instant so the look-back watermark is deterministic.
NOW = datetime(2026, 6, 3, 3, 0, tzinfo=UTC)


def _doc(doc_id: str, name: str = "Agreement") -> PandaDocDocument:
    return PandaDocDocument(
        document_id=doc_id,
        name=name,
        status="document.completed",
        date_completed="2026-06-01T00:00:00Z",
        raw={"id": doc_id, "name": name, "status": "document.completed"},
    )


async def _count_events(db: AsyncSession, doc_id: str) -> int:
    result = await db.execute(
        text(
            "SELECT count(*) FROM onboarding_events WHERE event_type = :et AND external_id = :eid"
        ),
        {"et": PANDADOC_SIGNED_EVENT, "eid": doc_id},
    )
    return result.scalar_one()


async def _seed_existing_event(db: AsyncSession, doc_id: str) -> None:
    """Insert a row as if the live webhook had already delivered this document."""
    await db.execute(
        text(
            "INSERT INTO onboarding_events "
            "  (event_type, external_id, payload, verified_at) "
            "VALUES (:et, :eid, '{}'::jsonb, now())"
        ),
        {"et": PANDADOC_SIGNED_EVENT, "eid": doc_id},
    )


@pytest.mark.db
async def test_reconcile_creates_only_the_missing_event_and_alerts(
    async_session: AsyncSession,
) -> None:
    present_a = f"doc_{uuid.uuid4().hex[:12]}"
    present_b = f"doc_{uuid.uuid4().hex[:12]}"
    missing = f"doc_{uuid.uuid4().hex[:12]}"
    # Two of the three docs already have an event row (webhook delivered them).
    await _seed_existing_event(async_session, present_a)
    await _seed_existing_event(async_session, present_b)

    fake_pandadoc = FakePandaDocClient(docs=[_doc(present_a), _doc(present_b), _doc(missing)])
    emitter = FakeEventEmitter()
    slack = FakeSlackNotifier()

    result = await reconcile_pandadoc(
        async_session,
        pandadoc_client=fake_pandadoc,
        emitter=emitter,
        slack=slack,
        lookback_days=7,
        now=NOW,
    )

    assert result.checked == 3
    assert result.created == 1

    # Only the missing doc was created; the present ones were untouched.
    assert await _count_events(async_session, missing) == 1
    assert await _count_events(async_session, present_a) == 1
    assert await _count_events(async_session, present_b) == 1

    # Exactly one event emitted, for the missing doc, with a real row id.
    assert len(emitter.sent) == 1
    name, data = emitter.sent[0]
    assert name == PANDADOC_SIGNED_EVENT
    assert data["document_id"] == missing
    assert data["onboarding_event_id"]

    # Exactly one Slack alert, naming the missing doc.
    assert len(slack.posted) == 1
    assert missing in slack.posted[0]

    # The look-back watermark = now - 7 days was passed to PandaDoc.
    assert fake_pandadoc.calls == [datetime(2026, 5, 27, 3, 0, tzinfo=UTC)]


@pytest.mark.db
async def test_reconcile_second_run_is_a_noop(async_session: AsyncSession) -> None:
    missing = f"doc_{uuid.uuid4().hex[:12]}"
    fake_pandadoc = FakePandaDocClient(docs=[_doc(missing)])

    first = await reconcile_pandadoc(
        async_session,
        pandadoc_client=fake_pandadoc,
        emitter=FakeEventEmitter(),
        slack=FakeSlackNotifier(),
        lookback_days=7,
        now=NOW,
    )
    assert first.created == 1

    # Same doc returned again, but now it already has a row -> pure no-op.
    emitter2 = FakeEventEmitter()
    slack2 = FakeSlackNotifier()
    second = await reconcile_pandadoc(
        async_session,
        pandadoc_client=fake_pandadoc,
        emitter=emitter2,
        slack=slack2,
        lookback_days=7,
        now=NOW,
    )

    assert second.checked == 1
    assert second.created == 0
    assert emitter2.sent == []
    assert slack2.posted == []
    assert await _count_events(async_session, missing) == 1


@pytest.mark.db
async def test_reconcile_no_documents_is_a_noop(async_session: AsyncSession) -> None:
    fake_pandadoc = FakePandaDocClient(docs=[])
    emitter = FakeEventEmitter()
    slack = FakeSlackNotifier()

    result = await reconcile_pandadoc(
        async_session,
        pandadoc_client=fake_pandadoc,
        emitter=emitter,
        slack=slack,
        lookback_days=7,
        now=NOW,
    )

    assert result.checked == 0
    assert result.created == 0
    assert emitter.sent == []
    assert slack.posted == []


@pytest.mark.db
async def test_reconcile_stamps_account_on_row_event_and_alert(
    async_session: AsyncSession,
) -> None:
    """S1-25c: a healed signing is stamped with the account it was listed from
    (here INT) on the onboarding_events row, the emitted pandadoc.signed event,
    and the Slack alert - so the fan-out downloads with the matching key."""
    missing = f"doc_{uuid.uuid4().hex[:12]}"
    fake_pandadoc = FakePandaDocClient(docs=[_doc(missing)])
    emitter = FakeEventEmitter()
    slack = FakeSlackNotifier()

    result = await reconcile_pandadoc(
        async_session,
        pandadoc_client=fake_pandadoc,
        emitter=emitter,
        slack=slack,
        lookback_days=7,
        now=NOW,
        account="int",
    )

    assert result.created == 1

    row = await async_session.execute(
        text(
            "SELECT pandadoc_account FROM onboarding_events "
            "WHERE event_type = :et AND external_id = :eid"
        ),
        {"et": PANDADOC_SIGNED_EVENT, "eid": missing},
    )
    assert row.scalar_one() == "int"

    _name, data = emitter.sent[0]
    assert data["account"] == "int"
    assert slack.posted[0].startswith("[INT] ")


async def test_run_reconciles_each_configured_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """S1-25c: `_run` loops every configured account, calling the core once per
    account and accumulating the totals. No DB / network: the production deps
    `_run` imports are stubbed, and the core is replaced by a recorder.

    Intentionally coupled to `_run`'s import wiring (it stubs each dep `_run`
    imports); the behavioural contract that matters - per-account isolation on
    failure - is covered separately by
    `test_run_isolates_accounts_a_later_failure_does_not_undo_an_earlier_one`,
    so this stays a thin wiring/accumulation check."""
    import types

    import bullet_api.config as cfg
    import bullet_api.crons.reconcile_pandadoc as mod
    import bullet_api.db as db
    import bullet_api.integrations.pandadoc_client as pc
    import bullet_api.integrations.slack as sl
    import bullet_api.pandadoc.accounts as acct
    import bullet_api.worker as wk
    from bullet_api.pandadoc.accounts import PandaDocCreds

    called_accounts: list[str] = []

    async def _fake_core(session, *, pandadoc_client, emitter, slack, lookback_days, now, account):
        called_accounts.append(account)
        return mod.ReconcileResult(checked=2, created=1)

    monkeypatch.setattr(mod, "reconcile_pandadoc", _fake_core)
    monkeypatch.setattr(
        acct,
        "api_accounts",
        lambda _s: [PandaDocCreds("uk", "UKKEY", ""), PandaDocCreds("int", "INTKEY", "")],
    )
    monkeypatch.setattr(
        cfg,
        "get_settings",
        lambda: types.SimpleNamespace(
            pandadoc_api_base_url="https://api.pandadoc.com",
            slack_webhook_url="",
            reconciliation_lookback_days=7,
        ),
    )
    monkeypatch.setattr(pc, "HttpPandaDocClient", lambda **_kw: object())
    monkeypatch.setattr(sl, "HttpSlackNotifier", lambda _url: object())
    monkeypatch.setattr(wk, "InngestEventEmitter", lambda _c: object())

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

    monkeypatch.setattr(db, "AsyncSessionLocal", lambda: _FakeSession())

    result = await mod._run()

    # One core call per configured account, UK first; totals accumulated.
    assert called_accounts == ["uk", "int"]
    assert result.checked == 4
    assert result.created == 2


async def test_run_isolates_accounts_a_later_failure_does_not_undo_an_earlier_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1-25c isolation: `_run` is NOT all-or-nothing across accounts. Each
    per-account `reconcile_pandadoc` commits at its end (see the core tests), so
    when a later account (here int) raises, the earlier account (uk) has already
    run to completion and its commit stands; the failure propagates out of
    `_run` so only the failed account is re-processed on the next nightly pass,
    rather than the whole pass rolling back."""
    import types

    import bullet_api.config as cfg
    import bullet_api.crons.reconcile_pandadoc as mod
    import bullet_api.db as db
    import bullet_api.integrations.pandadoc_client as pc
    import bullet_api.integrations.slack as sl
    import bullet_api.pandadoc.accounts as acct
    import bullet_api.worker as wk
    from bullet_api.pandadoc.accounts import PandaDocCreds

    completed: list[str] = []

    async def _flaky_core(session, *, pandadoc_client, emitter, slack, lookback_days, now, account):
        if account == "int":
            # A transient INT failure (e.g. the list call / a Slack post) raising
            # AFTER UK has already committed.
            raise RuntimeError("int list failed")
        completed.append(account)
        return mod.ReconcileResult(checked=1, created=1)

    monkeypatch.setattr(mod, "reconcile_pandadoc", _flaky_core)
    monkeypatch.setattr(
        acct,
        "api_accounts",
        lambda _s: [PandaDocCreds("uk", "UKKEY", ""), PandaDocCreds("int", "INTKEY", "")],
    )
    monkeypatch.setattr(
        cfg,
        "get_settings",
        lambda: types.SimpleNamespace(
            pandadoc_api_base_url="https://api.pandadoc.com",
            slack_webhook_url="",
            reconciliation_lookback_days=7,
        ),
    )
    monkeypatch.setattr(pc, "HttpPandaDocClient", lambda **_kw: object())
    monkeypatch.setattr(sl, "HttpSlackNotifier", lambda _url: object())
    monkeypatch.setattr(wk, "InngestEventEmitter", lambda _c: object())

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_a: object) -> bool:
            # Do not suppress: the int failure must propagate out of `_run`.
            return False

    monkeypatch.setattr(db, "AsyncSessionLocal", lambda: _FakeSession())

    with pytest.raises(RuntimeError, match="int list failed"):
        await mod._run()

    # UK ran to completion (its core committed) BEFORE int raised; the int
    # failure was neither swallowed nor allowed to undo UK's pass.
    assert completed == ["uk"]
