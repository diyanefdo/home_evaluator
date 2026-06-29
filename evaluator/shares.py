"""Shareable result links — persist a scenario under a short slug.

Read-only and account-free: anyone with the slug can view the result at
``/r/<slug>``. We don't deduplicate (each share is its own immutable snapshot).
"""

from __future__ import annotations

import secrets

from sqlmodel import select

from evaluator import db
from evaluator.models import SharedResult

_SLUG_BYTES = 6   # ~8 url-safe chars


def _as_dict(r: SharedResult) -> dict:
    return {
        "id": r.id,
        "slug": r.slug,
        "inputs": r.inputs or {},
        "snapshot": r.snapshot or {},
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def create(inputs: dict, snapshot: dict) -> dict:
    """Persist a result under a fresh unique slug and return it."""
    with db.get_session() as session:
        for _ in range(8):  # retry on the astronomically-unlikely slug collision
            slug = secrets.token_urlsafe(_SLUG_BYTES)
            if session.exec(select(SharedResult).where(SharedResult.slug == slug)).first() is None:
                break
        row = SharedResult(slug=slug, inputs=inputs, snapshot=snapshot)
        session.add(row)
        session.commit()
        session.refresh(row)
        return _as_dict(row)


def get_by_slug(slug: str) -> dict | None:
    with db.get_session() as session:
        row = session.exec(select(SharedResult).where(SharedResult.slug == slug)).first()
        return _as_dict(row) if row else None
