"""Shared pytest fixtures for the Bullet API test suite.

Tests marked `@pytest.mark.db` require a live Postgres reachable via
`DATABASE_URL`. When the engine cannot connect (e.g. running locally
without docker compose up), those tests are skipped with a clear message
rather than failing - non-DB tests still run.

**IN CI THIS FAILS CLOSED INSTEAD.** When `CI` is set, an unreachable
database is an ERROR, not a skip. Silently skipping is how two review
rounds shipped: ~223 DB tests turned into skips and the run still
reported green, so every guard they covered was unverified while looking
verified. A skipped test cannot fail, so it cannot prove anything - the
same reason `review_gate_mutate.py` reports UNPROVEN rather than passing
a mutation whose test skipped.

The test engine uses `NullPool` so it cannot leak connections across the
per-function event loops that `pytest-asyncio` creates in auto mode. The
production engine in `bullet_api.db.session` remains pooled.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

# Run the suite in Inngest dev mode. Importing `bullet_api.main` calls
# `inngest.fast_api.serve(...)`, which raises SigningKeyMissingError in
# Inngest's default cloud mode when no signing key is set (the case in CI and
# local test runs). Tests never talk to Inngest Cloud, so dev mode is correct
# here. `setdefault` runs at conftest import - before any test module imports
# the app - and leaves an explicitly-set INNGEST_DEV untouched.
os.environ.setdefault("INNGEST_DEV", "1")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from bullet_api.config import get_async_database_url, get_settings  # noqa: E402


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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
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

    db_items = [item for item in items if "db" in item.keywords]

    # In CI, an unreachable database is a CONFIGURATION FAILURE, not a reason to
    # quietly pass. Skipping is a developer convenience for a laptop with no
    # Postgres running; in CI it silently removes the majority of the suite and
    # reports green. That is not hypothetical - S1-26b/c was reviewed and
    # merged-ready at "279 passed / 193 skipped", and the skipped 193 were
    # exactly where the returning-client bugs lived. Two review rounds of
    # findings existed only because those tests never ran.
    if os.environ.get("CI"):
        raise pytest.UsageError(
            f"DATABASE_URL is not reachable, so {len(db_items)} db-marked tests would be "
            "SKIPPED - but CI is set, where skipping them reports a false green. "
            "Start Postgres (docker compose up -d postgres), run migrations, and "
            "export a reachable DATABASE_URL."
        )

    skip_marker = pytest.mark.skip(reason="DATABASE_URL is not reachable; skipping live-DB tests.")
    for item in db_items:
        item.add_marker(skip_marker)
