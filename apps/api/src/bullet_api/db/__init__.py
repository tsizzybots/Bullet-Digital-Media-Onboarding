"""Async database layer for the Bullet API.

Public surface:
- `Base`: SQLAlchemy declarative base. All future models inherit from this.
- `engine`: shared `AsyncEngine` bound to `DATABASE_URL`.
- `AsyncSessionLocal`: session factory for ad-hoc `async with` blocks.
- `get_session`: FastAPI dependency that yields a managed `AsyncSession`.
"""

from __future__ import annotations

from bullet_api.db.base import Base
from bullet_api.db.session import AsyncSessionLocal, engine, get_session

__all__ = ["AsyncSessionLocal", "Base", "engine", "get_session"]
