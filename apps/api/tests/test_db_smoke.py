"""Smoke tests for the async DB layer (S1-05).

Proves:
  1. The async session factory yields a working session against the
     configured `DATABASE_URL`.
  2. The first migration (`0001_create_extensions`) has been applied —
     both `vector` and `citext` extensions are present.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.db
async def test_session_executes_select_1(async_session: AsyncSession) -> None:
    result = await async_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


@pytest.mark.db
async def test_extensions_present(async_session: AsyncSession) -> None:
    result = await async_session.execute(
        text(
            "SELECT extname FROM pg_extension "
            "WHERE extname IN ('vector', 'citext') "
            "ORDER BY extname"
        )
    )
    extensions = [row[0] for row in result.all()]
    assert extensions == ["citext", "vector"]
