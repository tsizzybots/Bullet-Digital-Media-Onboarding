"""SQLAlchemy declarative base shared by every model in the app."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single declarative base. Models register their tables here so Alembic
    autogenerate (S1-06+) can diff `Base.metadata` against the live DB."""
