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
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from bullet_api.config import get_async_database_url


def _build_test_engine():
    return create_async_engine(
        get_async_database_url(),
        poolclass=NullPool,
    )


@pytest_asyncio.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession backed by a NullPool engine, then dispose."""
    engine = _build_test_engine()
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.rollback()
    finally:
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
