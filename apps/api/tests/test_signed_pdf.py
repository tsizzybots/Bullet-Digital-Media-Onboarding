"""Integration tests for the S1-25b `store_signed_pdf` fan-out.

These hit Postgres (`documents` insert, `platform_actions` begin/complete/
fail) via the transactional `async_session` fixture, so DB tests are marked
`@pytest.mark.db` and skip when no DATABASE_URL is reachable. PandaDoc and R2
are replaced with `FakePandaDocClient` + `FakeStorageClient` so no network
call is made and the request/upload can be asserted.

The matrix deliberately covers the failure/transport/replay paths the human
reviewer caught us missing on S1-25 (see `/pre-pr-review`), not just the
happy path:
- success writes one documents row + platform_actions success + one R2 put;
- PandaDoc 404, a transport-level download error (httpx.ReadTimeout), an R2
  upload failure, and an empty PDF each record `failed` and leave no orphan;
- replay is idempotent; a pre-existing documents row is not duplicated.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.pandadoc.client import FakePandaDocClient, PandaDocNotFound
from bullet_api.storage.client import FakeStorageClient
from bullet_api.worker import CLIENT_CREATED_EVENT, PANDADOC_SIGNED_EVENT
from bullet_api.worker.signed_pdf import (
    ClientNotFoundError,
    EmptyPdfError,
    build_signed_pdf_key,
    store_signed_pdf,
    store_signed_pdf_core,
)

PDF_BYTES = b"%PDF-1.7\nfake signed agreement\n%%EOF"


async def _seed_client(session: AsyncSession) -> uuid.UUID:
    result = await session.execute(
        text(
            "INSERT INTO clients (email, legal_entity, current_step, step_entered_at) "
            "VALUES ('signer@example.com', 'Sample Gym Ltd', 'signed', now()) RETURNING id"
        ),
    )
    return result.scalar_one()


async def _seed_onboarding_event(session: AsyncSession) -> uuid.UUID:
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    result = await session.execute(
        text(
            "INSERT INTO onboarding_events (event_type, external_id, payload, verified_at) "
            "VALUES (:et, :eid, cast('{}' AS jsonb), now()) RETURNING id"
        ),
        {"et": PANDADOC_SIGNED_EVENT, "eid": document_id},
    )
    return result.scalar_one()


async def _documents(session: AsyncSession, client_id: uuid.UUID) -> list:
    rows = await session.execute(
        text(
            "SELECT kind, r2_key, external_url, metadata "
            "FROM documents WHERE client_id = :cid AND kind = 'pandadoc_signed_pdf'"
        ),
        {"cid": client_id},
    )
    return rows.all()


async def _actions(session: AsyncSession, client_id: uuid.UUID) -> list:
    rows = await session.execute(
        text(
            "SELECT status, external_id, last_error, retry_count "
            "FROM platform_actions WHERE client_id = :cid AND platform = 'pandadoc'"
        ),
        {"cid": client_id},
    )
    return rows.all()


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_success_stores_pdf_and_writes_document_row(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    document_id = "doc_signed_1"
    panda = FakePandaDocClient(pdfs={document_id: PDF_BYTES})
    storage = FakeStorageClient()

    result = await store_signed_pdf_core(
        async_session,
        panda,
        storage,
        client_id=client_id,
        onboarding_event_id=event_id,
        document_id=document_id,
    )

    expected_key = build_signed_pdf_key(client_id, document_id)
    assert result.stored is True
    assert result.skipped is False
    assert result.r2_key == expected_key

    # exactly one R2 put with the PDF bytes
    assert storage.puts == [(expected_key, PDF_BYTES, "application/pdf")]

    # one documents row, FK'd to client, with r2_key + metadata
    docs = await _documents(async_session, client_id)
    assert len(docs) == 1
    assert docs[0].kind == "pandadoc_signed_pdf"
    assert docs[0].r2_key == expected_key
    assert docs[0].metadata["document_id"] == document_id
    assert docs[0].metadata["size_bytes"] == len(PDF_BYTES)
    assert docs[0].metadata["source"] == "pandadoc"

    # platform_actions success with external_id = r2_key
    actions = await _actions(async_session, client_id)
    assert len(actions) == 1
    assert actions[0].status == "success"
    assert actions[0].external_id == expected_key
    assert actions[0].retry_count == 0


# --------------------------------------------------------------------------- #
# Failure modes (the hardening matrix)
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_pandadoc_404_records_failed_no_orphan(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    panda = FakePandaDocClient(pdfs={})  # unknown id -> PandaDocNotFound
    storage = FakeStorageClient()

    with pytest.raises(PandaDocNotFound):
        await store_signed_pdf_core(
            async_session,
            panda,
            storage,
            client_id=client_id,
            onboarding_event_id=event_id,
            document_id="missing_doc",
        )

    assert storage.puts == []  # never uploaded
    assert await _documents(async_session, client_id) == []  # no orphan row
    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"
    assert actions[0].retry_count == 1


@pytest.mark.db
async def test_transport_level_download_error_records_failed_and_propagates(
    async_session: AsyncSession,
) -> None:
    """S1-25 regression: a transport-level error (NOT a typed PandaDocNotFound)
    must still flip the row to `failed`, not leave it stuck `in_progress`."""
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    panda = FakePandaDocClient(download_error=httpx.ReadTimeout("read timed out"))
    storage = FakeStorageClient()

    with pytest.raises(httpx.ReadTimeout):
        await store_signed_pdf_core(
            async_session,
            panda,
            storage,
            client_id=client_id,
            onboarding_event_id=event_id,
            document_id="doc_timeout",
        )

    assert storage.puts == []
    assert await _documents(async_session, client_id) == []
    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"
    assert "read timed out" in actions[0].last_error
    assert actions[0].retry_count == 1


@pytest.mark.db
async def test_r2_upload_failure_records_failed_no_document_row(
    async_session: AsyncSession,
) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    document_id = "doc_upload_fail"
    panda = FakePandaDocClient(pdfs={document_id: PDF_BYTES})
    storage = FakeStorageClient(error=RuntimeError("r2 unavailable"))

    with pytest.raises(RuntimeError):
        await store_signed_pdf_core(
            async_session,
            panda,
            storage,
            client_id=client_id,
            onboarding_event_id=event_id,
            document_id=document_id,
        )

    # Insert runs only after a successful upload, so no row points at a missing object.
    assert await _documents(async_session, client_id) == []
    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"
    assert "r2 unavailable" in actions[0].last_error


@pytest.mark.db
async def test_empty_pdf_records_failed(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    document_id = "doc_empty"
    panda = FakePandaDocClient(pdfs={document_id: b""})  # zero-byte body
    storage = FakeStorageClient()

    with pytest.raises(EmptyPdfError):
        await store_signed_pdf_core(
            async_session,
            panda,
            storage,
            client_id=client_id,
            onboarding_event_id=event_id,
            document_id=document_id,
        )

    assert storage.puts == []  # never uploaded an empty object
    assert await _documents(async_session, client_id) == []
    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"


@pytest.mark.db
async def test_missing_client_raises_client_not_found(async_session: AsyncSession) -> None:
    panda = FakePandaDocClient(pdfs={"d": PDF_BYTES})
    storage = FakeStorageClient()
    with pytest.raises(ClientNotFoundError):
        await store_signed_pdf_core(
            async_session,
            panda,
            storage,
            client_id=uuid.uuid4(),
            onboarding_event_id=None,
            document_id="d",
        )
    assert storage.puts == []


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_replay_same_event_is_idempotent(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    document_id = "doc_replay"
    panda = FakePandaDocClient(pdfs={document_id: PDF_BYTES})
    storage = FakeStorageClient()

    first = await store_signed_pdf_core(
        async_session,
        panda,
        storage,
        client_id=client_id,
        onboarding_event_id=event_id,
        document_id=document_id,
    )
    assert first.stored is True

    second = await store_signed_pdf_core(
        async_session,
        panda,
        storage,
        client_id=client_id,
        onboarding_event_id=event_id,
        document_id=document_id,
    )
    assert second.stored is False
    assert second.skipped is True

    # one R2 put, one documents row, one action row across both runs
    assert len(storage.puts) == 1
    assert len(await _documents(async_session, client_id)) == 1
    assert len(await _actions(async_session, client_id)) == 1


@pytest.mark.db
async def test_guarded_insert_does_not_duplicate_existing_document(
    async_session: AsyncSession,
) -> None:
    """If a documents row for this client+kind+r2_key already exists (e.g. a
    retry after a crash between upload and commit), the guarded insert is a
    no-op - no duplicate row."""
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    document_id = "doc_preexist"
    key = build_signed_pdf_key(client_id, document_id)

    await async_session.execute(
        text(
            "INSERT INTO documents (client_id, kind, r2_key, metadata) "
            "VALUES (:cid, 'pandadoc_signed_pdf', :key, cast('{}' AS jsonb))"
        ),
        {"cid": client_id, "key": key},
    )

    panda = FakePandaDocClient(pdfs={document_id: PDF_BYTES})
    storage = FakeStorageClient()
    await store_signed_pdf_core(
        async_session,
        panda,
        storage,
        client_id=client_id,
        onboarding_event_id=event_id,
        document_id=document_id,
    )

    assert len(await _documents(async_session, client_id)) == 1  # not duplicated


# --------------------------------------------------------------------------- #
# Declaration assertions
# --------------------------------------------------------------------------- #


def test_store_signed_pdf_declares_concurrency_caps() -> None:
    cfg = store_signed_pdf.get_config("").main
    assert cfg.concurrency is not None
    assert len(cfg.concurrency) == 2
    global_cap = next(c for c in cfg.concurrency if c.key is None)
    assert global_cap.limit == 5
    assert global_cap.scope == "fn"
    per_client = next(c for c in cfg.concurrency if c.key == "event.data.client_id")
    assert per_client.limit == 1
    assert per_client.scope == "fn"


def test_store_signed_pdf_triggers_on_client_created() -> None:
    cfg = store_signed_pdf.get_config("").main
    assert cfg.triggers[0].event == CLIENT_CREATED_EVENT
