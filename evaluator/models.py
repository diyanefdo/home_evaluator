"""Database models (SQLModel).

Phase 2 starts with just the ``User`` table — the foundation for accounts and,
later, saved scenarios / run history. A user is keyed by their Google ``sub``
(the stable OpenID subject identifier), not by email (emails can change).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column
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


class Scenario(SQLModel, table=True):
    """A user-saved scenario: a name + all inputs + a snapshot of the result."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    name: str
    # The evaluator inputs that reproduce the run, and a light result snapshot
    # (verdict / gap / region) so the list view needs no recompute.
    inputs: dict = Field(default_factory=dict, sa_column=Column(JSON))
    snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
