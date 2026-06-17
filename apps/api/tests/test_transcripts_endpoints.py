"""Tests for the S1-27 transcript API: GET /transcripts/unlinked + POST link.

Auth is exercised both ways (a fake founder via dependency override for the
happy paths; the real dependency + no cookie for the 401). Seeded rows carry a
per-test run id so assertions are robust against other rows in the test DB.
All `@pytest.mark.db`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth import CurrentUser, get_current_user
from bullet_api.db import get_session
from bullet_api.main import app
from bullet_api.worker import TRANSCRIPT_LINKED_EVENT, FakeEventEmitter, get_event_emitter


async def _seed_user(session: AsyncSession) -> CurrentUser:
    """Insert a real users row so `linked_by` (FK -> users.id) is satisfiable on
    a manual attach, and return it as the authenticated CurrentUser."""
    email = f"dash+{uuid.uuid4().hex[:6]}@example.com"
    user_id = (
        await session.execute(
            text(
                "INSERT INTO users (email, password_hash, full_name, role) "
                "VALUES (:email, 'x', 'Dash Board', 'founder') RETURNING id"
            ),
            {"email": email},
        )
    ).scalar_one()
    return CurrentUser(id=user_id, email=email, full_name="Dash Board", role="founder")


@pytest_asyncio.fixture
async def authed_client(
    async_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, FakeEventEmitter, CurrentUser]]:
    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    user = await _seed_user(async_session)
    fake_emitter = FakeEventEmitter()
    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_event_emitter] = lambda: fake_emitter
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, fake_emitter, user
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def anon_client(async_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = _session_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


async def _park_transcript(
    session: AsyncSession, emails: list[str], *, client_id: uuid.UUID | None = None
) -> uuid.UUID:
    external_id = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"
    return (
        await session.execute(
            text(
                "INSERT INTO sales_call_transcripts "
                "  (source, external_id, r2_key, participant_emails, client_id, linked_at) "
                "VALUES ('google_meet', :eid, :r2, cast(:emails AS jsonb), cast(:cid AS uuid), "
                "        CASE WHEN cast(:cid AS uuid) IS NULL THEN NULL ELSE now() END) "
                "RETURNING id"
            ),
            {
                "eid": external_id,
                "r2": f"sales-call-transcripts/{external_id}.txt",
                "emails": json.dumps(emails),
                "cid": client_id,
            },
        )
    ).scalar_one()


async def _seed_client(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text(
                "INSERT INTO clients (email, legal_entity, current_step, step_entered_at) "
                "VALUES (:email, 'Sample Gym Ltd', 'signed', now()) RETURNING id"
            ),
            {"email": f"client+{uuid.uuid4().hex[:6]}@gym.com"},
        )
    ).scalar_one()


# --------------------------------------------------------------------------- #
# GET /transcripts/unlinked
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_unlinked_list_returns_only_unlinked(
    async_session: AsyncSession,
    authed_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, _, _ = authed_client
    parked = await _park_transcript(async_session, ["a@gym.com"])
    linked_client = await _seed_client(async_session)
    linked = await _park_transcript(async_session, ["b@gym.com"], client_id=linked_client)

    response = await client.get("/transcripts/unlinked")
    assert response.status_code == 200
    ids = {t["id"] for t in response.json()["transcripts"]}
    assert str(parked) in ids
    assert str(linked) not in ids


@pytest.mark.db
async def test_unlinked_list_requires_auth(anon_client: AsyncClient) -> None:
    response = await anon_client.get("/transcripts/unlinked")
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# POST /transcripts/{id}/link
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_manual_link_happy_path(
    async_session: AsyncSession,
    authed_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake, user = authed_client
    transcript_id = await _park_transcript(async_session, ["unmatched@gym.com"])
    client_id = await _seed_client(async_session)

    response = await client.post(
        f"/transcripts/{transcript_id}/link", json={"client_id": str(client_id)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["transcript_id"] == str(transcript_id)
    assert body["client_id"] == str(client_id)
    assert body["document_id"]

    row = (
        await async_session.execute(
            text(
                "SELECT client_id, link_method, linked_by "
                "FROM sales_call_transcripts WHERE id = :id"
            ),
            {"id": transcript_id},
        )
    ).one()
    assert row.client_id == client_id
    assert row.link_method == "manual"
    assert row.linked_by == user.id

    assert [n for n, _ in fake.sent] == [TRANSCRIPT_LINKED_EVENT]


@pytest.mark.db
async def test_manual_link_same_client_is_idempotent_and_re_emits(
    async_session: AsyncSession,
    authed_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    """Re-POSTing the same client to an already-linked transcript is idempotent
    (200, not 409) and re-emits transcript.linked - the recovery path for a
    prior attempt whose post-commit emit failed. No duplicate documents row."""
    client, fake, _ = authed_client
    transcript_id = await _park_transcript(async_session, ["unmatched@gym.com"])
    client_id = await _seed_client(async_session)

    first = await client.post(
        f"/transcripts/{transcript_id}/link", json={"client_id": str(client_id)}
    )
    assert first.status_code == 200
    # Re-POST the SAME client (simulates a retry after a dropped emit).
    second = await client.post(
        f"/transcripts/{transcript_id}/link", json={"client_id": str(client_id)}
    )
    assert second.status_code == 200
    assert second.json()["document_id"] == first.json()["document_id"]

    docs = (
        await async_session.execute(
            text(
                "SELECT count(*) FROM documents WHERE client_id = :cid AND kind = 'transcript_text'"
            ),
            {"cid": client_id},
        )
    ).scalar_one()
    assert docs == 1  # no duplicate document
    # both requests emitted transcript.linked (at-least-once; S1-29 dedupes)
    assert [n for n, _ in fake.sent] == [TRANSCRIPT_LINKED_EVENT, TRANSCRIPT_LINKED_EVENT]


@pytest.mark.db
async def test_manual_link_unknown_transcript_404(
    authed_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, _, _ = authed_client
    response = await client.post(
        f"/transcripts/{uuid.uuid4()}/link", json={"client_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


@pytest.mark.db
async def test_manual_link_malformed_id_404(
    authed_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, _, _ = authed_client
    response = await client.post(
        "/transcripts/not-a-uuid/link", json={"client_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


@pytest.mark.db
async def test_manual_link_already_linked_409(
    async_session: AsyncSession,
    authed_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, _, _ = authed_client
    existing_client = await _seed_client(async_session)
    transcript_id = await _park_transcript(async_session, ["x@gym.com"], client_id=existing_client)
    other_client = await _seed_client(async_session)

    response = await client.post(
        f"/transcripts/{transcript_id}/link", json={"client_id": str(other_client)}
    )
    assert response.status_code == 409


@pytest.mark.db
async def test_manual_link_with_null_r2_key_links_no_document_no_emit(
    async_session: AsyncSession,
    authed_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    """A transcript whose capture never finished (r2_key NULL) can still be
    linked, but gets NO documents row and emits NO transcript.linked (there is
    no stored text to summarise yet) - the documented edge in link_transcript."""
    client, fake, _ = authed_client
    external_id = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"
    transcript_id = (
        await async_session.execute(
            text(
                "INSERT INTO sales_call_transcripts (source, external_id, participant_emails) "
                "VALUES ('google_meet', :eid, cast('[]' AS jsonb)) RETURNING id"
            ),
            {"eid": external_id},
        )
    ).scalar_one()
    client_id = await _seed_client(async_session)

    response = await client.post(
        f"/transcripts/{transcript_id}/link", json={"client_id": str(client_id)}
    )
    assert response.status_code == 200
    assert response.json()["document_id"] is None

    docs = (
        await async_session.execute(
            text("SELECT count(*) FROM documents WHERE client_id = :cid"),
            {"cid": client_id},
        )
    ).scalar_one()
    assert docs == 0
    assert fake.sent == []  # nothing to summarise -> no emit


@pytest.mark.db
async def test_manual_link_unknown_client_400(
    async_session: AsyncSession,
    authed_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, _, _ = authed_client
    transcript_id = await _park_transcript(async_session, ["x@gym.com"])
    response = await client.post(
        f"/transcripts/{transcript_id}/link", json={"client_id": str(uuid.uuid4())}
    )
    assert response.status_code == 400
