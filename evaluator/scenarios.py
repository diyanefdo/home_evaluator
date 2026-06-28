"""Saved-scenario persistence — CRUD over the Scenario table.

All reads/writes are **ownership-scoped**: every function takes ``user_id`` and
only ever touches rows belonging to that user. Returns plain dicts so callers can
use results after the session closes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select

from evaluator import db
from evaluator.models import Scenario


def _as_dict(s: Scenario) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "inputs": s.inputs or {},
        "snapshot": s.snapshot or {},
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def create(user_id: int, name: str, inputs: dict, snapshot: dict) -> dict:
    with db.get_session() as session:
        row = Scenario(user_id=user_id, name=name, inputs=inputs, snapshot=snapshot)
        session.add(row)
        session.commit()
        session.refresh(row)
        return _as_dict(row)


def list_for_user(user_id: int) -> list[dict]:
    """All of a user's scenarios, most-recently-updated first."""
    with db.get_session() as session:
        rows = session.exec(
            select(Scenario)
            .where(Scenario.user_id == user_id)
            .order_by(Scenario.updated_at.desc())
        ).all()
        return [_as_dict(r) for r in rows]


def get(scenario_id: int, user_id: int) -> dict | None:
    """One scenario, only if it belongs to ``user_id``."""
    with db.get_session() as session:
        row = session.get(Scenario, scenario_id)
        if row is None or row.user_id != user_id:
            return None
        return _as_dict(row)


def update(scenario_id: int, user_id: int, *, name: str | None = None,
           inputs: dict | None = None, snapshot: dict | None = None) -> dict | None:
    """Update fields of an owned scenario; None if not found/owned."""
    with db.get_session() as session:
        row = session.get(Scenario, scenario_id)
        if row is None or row.user_id != user_id:
            return None
        if name is not None:
            row.name = name
        if inputs is not None:
            row.inputs = inputs
        if snapshot is not None:
            row.snapshot = snapshot
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        session.refresh(row)
        return _as_dict(row)


def delete(scenario_id: int, user_id: int) -> bool:
    """Delete an owned scenario; True if a row was removed."""
    with db.get_session() as session:
        row = session.get(Scenario, scenario_id)
        if row is None or row.user_id != user_id:
            return False
        session.delete(row)
        session.commit()
        return True
