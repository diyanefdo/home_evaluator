"""Database engine + session helpers (Phase 2 persistence).

The whole persistence layer is **opt-in**: if ``EVALUATOR_DB`` is unset the app
runs exactly as before (stateless, no accounts). When it's set to a SQLAlchemy URL
the engine is created lazily and tables are created on startup.

We target **PostgreSQL in its own container** (see docker-compose `db` service),
e.g. ``postgresql+psycopg://eval:secret@db:5432/evaluator``. Any SQLAlchemy URL
works, so ``sqlite:///./local.db`` is handy for local development/tests.
"""

from __future__ import annotations

import os

from sqlmodel import SQLModel, Session, create_engine

_DB_URL = os.environ.get("EVALUATOR_DB", "").strip()
_engine = None


def db_enabled() -> bool:
    """True when a database URL is configured."""
    return bool(_DB_URL)


def get_engine():
    """Lazily build (and cache) the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        if not _DB_URL:
            raise RuntimeError("EVALUATOR_DB is not set — persistence is disabled.")
        # pool_pre_ping survives Postgres restarts / idle-disconnects.
        connect_args = {"check_same_thread": False} if _DB_URL.startswith("sqlite") else {}
        _engine = create_engine(_DB_URL, pool_pre_ping=True, connect_args=connect_args)
    return _engine


def init_db() -> bool:
    """Create tables for all registered models. No-op when persistence is off."""
    if not _DB_URL:
        return False
    from evaluator import models  # noqa: F401 - registers tables on SQLModel.metadata
    SQLModel.metadata.create_all(get_engine())
    return True


def get_session() -> Session:
    """A new SQLModel session bound to the engine."""
    return Session(get_engine())
