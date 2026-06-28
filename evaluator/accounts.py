"""Account persistence helpers — thin functions over the User table.

These return plain dicts (not ORM instances) so callers can use them after the
session closes without tripping SQLAlchemy's detached-instance rules.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select

from evaluator import db
from evaluator.models import User


def _as_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
    }


def upsert_google_user(google_sub: str, email: str,
                       name: str | None = None, picture: str | None = None) -> dict:
    """Create or update the user for a Google identity; record the login time."""
    with db.get_session() as session:
        user = session.exec(select(User).where(User.google_sub == google_sub)).first()
        now = datetime.now(timezone.utc)
        if user is None:
            user = User(google_sub=google_sub, email=email, name=name,
                        picture=picture, created_at=now, last_login=now)
        else:
            user.email = email
            user.name = name
            user.picture = picture
            user.last_login = now
        session.add(user)
        session.commit()
        session.refresh(user)
        return _as_dict(user)


def get_user(user_id: int) -> dict | None:
    """Look up a user by primary key; None if missing."""
    with db.get_session() as session:
        user = session.get(User, user_id)
        return _as_dict(user) if user else None
