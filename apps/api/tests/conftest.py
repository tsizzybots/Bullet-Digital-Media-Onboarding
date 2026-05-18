"""Shared pytest fixtures for the Bullet API test suite.

Tests marked `@pytest.mark.db` require a live Postgres reachable via
`DATABASE_URL`. When the engine cannot connect (e.g. running locally
without docker compose up), those tests are skipped with a clear message
rather than failing - non-DB tests still run.

The test engine uses `NullPool` so it cannot leak connections across the
per-function event loops that `pytest-asyncio` creates in auto mode. The
production engine in `bullet_api.db.session` remains pooled.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from bullet_api.config import get_async_database_url, get_settings


def _build_test_engine():
    return create_async_engine(
        get_async_database_url(),
        poolclass=NullPool,
        connect_args={"ssl": get_settings().database_ssl_mode},
    )


@pytest_asyncio.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession bound to a per-test outer transaction.

    Any `session.commit()` inside the test (or a handler under test) only
    releases a SAVEPOINT inside the outer transaction; the fixture rolls
    the outer transaction back on teardown so the test never persists
    state to Neon. This keeps the live-Neon test pattern viable for
    handlers that legitimately need to commit (S1-13 login, S1-14
    confirmation, etc.).
    """
    engine = _build_test_engine()
    async with engine.connect() as connection:
        outer = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if outer.is_active:
                await outer.rollback()
    await engine.dispose()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip @pytest.mark.db tests when DATABASE_URL is unreachable."""

    async def _can_connect() -> bool:
        engine = _build_test_engine()
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    try:
        reachable = asyncio.run(_can_connect())
    except Exception:
        reachable = False

    if reachable:
        return

    skip_marker = pytest.mark.skip(
        reason="DATABASE_URL is not reachable; skipping live-DB tests."
    )
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_marker)
