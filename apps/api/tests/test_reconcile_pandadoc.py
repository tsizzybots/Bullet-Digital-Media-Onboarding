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
