"""Run history ("my runs") — auto-recorded evaluations per user.

Every full evaluation by a signed-in user is appended here. To keep it useful
(not spammy) we skip a run identical to the user's most recent one, and prune to
the most recent ``MAX_PER_USER`` rows.
"""

from __future__ import annotations

from sqlmodel import select

from evaluator import db
from evaluator.models import RunHistory

MAX_PER_USER = 50


def _as_dict(r: RunHistory) -> dict:
    return {
        "id": r.id,
        "inputs": r.inputs or {},
        "snapshot": r.snapshot or {},
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def record_run(user_id: int, inputs: dict, snapshot: dict) -> dict | None:
    """Append a run; dedupe against the latest and prune old rows.

    Returns the stored dict, or None if it was a duplicate of the last run.
    """
    with db.get_session() as session:
        latest = session.exec(
            select(RunHistory)
            .where(RunHistory.user_id == user_id)
            .order_by(RunHistory.created_at.desc())
        ).first()
        if latest is not None and (latest.inputs or {}) == (inputs or {}):
            return None  # same as the previous run — don't log a duplicate

        row = RunHistory(user_id=user_id, inputs=inputs, snapshot=snapshot)
        session.add(row)
        session.commit()
        session.refresh(row)

        # Prune anything beyond the most recent MAX_PER_USER.
        old = session.exec(
            select(RunHistory)
            .where(RunHistory.user_id == user_id)
            .order_by(RunHistory.created_at.desc())
            .offset(MAX_PER_USER)
        ).all()
        for r in old:
            session.delete(r)
        if old:
            session.commit()
        return _as_dict(row)


def list_for_user(user_id: int, limit: int = MAX_PER_USER) -> list[dict]:
    with db.get_session() as session:
        rows = session.exec(
            select(RunHistory)
            .where(RunHistory.user_id == user_id)
            .order_by(RunHistory.created_at.desc())
            .limit(limit)
        ).all()
        return [_as_dict(r) for r in rows]


def get(run_id: int, user_id: int) -> dict | None:
    with db.get_session() as session:
        row = session.get(RunHistory, run_id)
        if row is None or row.user_id != user_id:
            return None
        return _as_dict(row)


def clear(user_id: int) -> int:
    """Delete all of a user's history; returns how many rows were removed."""
    with db.get_session() as session:
        rows = session.exec(
            select(RunHistory).where(RunHistory.user_id == user_id)
        ).all()
        for r in rows:
            session.delete(r)
        session.commit()
        return len(rows)
