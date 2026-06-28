"""Database models (SQLModel).

Phase 2 starts with just the ``User`` table — the foundation for accounts and,
later, saved scenarios / run history. A user is keyed by their Google ``sub``
(the stable OpenID subject identifier), not by email (emails can change).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    """An authenticated account (currently via Google OAuth)."""

    id: int | None = Field(default=None, primary_key=True)
    google_sub: str = Field(index=True, unique=True)  # stable Google subject id
    email: str = Field(index=True)
    name: str | None = None
    picture: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    last_login: datetime = Field(default_factory=_utcnow)
