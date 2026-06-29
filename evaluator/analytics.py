"""Usage analytics — anonymized per-evaluation metadata + summary aggregates.

Each evaluation records region/price/verdict (no personal data, no user id — just
a signed-in flag). The admin dashboard reads :func:`summary`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlmodel import select

from evaluator import db
from evaluator.models import UsageEvent

_PRICE_BUCKETS = [
    (0, 500_000, "< $500k"),
    (500_000, 800_000, "$500k–800k"),
    (800_000, 1_200_000, "$800k–1.2M"),
    (1_200_000, 2_000_000, "$1.2M–2M"),
    (2_000_000, float("inf"), "$2M+"),
]


def record(*, fsa: str | None, region: str | None, price: float | None,
           down_pct: float | None, years: int | None, verdict: str | None,
           gap: float | None, after_tax: bool, signed_in: bool) -> None:
    """Insert one usage event (best-effort; swallow DB errors)."""
    try:
        with db.get_session() as session:
            session.add(UsageEvent(
                fsa=fsa, region=region, price=price, down_pct=down_pct, years=years,
                verdict=verdict, gap=gap, after_tax=after_tax, signed_in=signed_in,
            ))
            session.commit()
    except Exception:  # noqa: BLE001 - analytics must never break a request
        pass


def _count(session, *where) -> int:
    stmt = select(func.count(UsageEvent.id))
    for w in where:
        stmt = stmt.where(w)
    return session.exec(stmt).one()


def summary() -> dict:
    """Aggregate stats for the admin dashboard."""
    now = datetime.now(timezone.utc)
    with db.get_session() as session:
        total = _count(session)
        last_7 = _count(session, UsageEvent.created_at >= now - timedelta(days=7))
        last_30 = _count(session, UsageEvent.created_at >= now - timedelta(days=30))

        def grouped(col, limit=None):
            stmt = (select(col, func.count(UsageEvent.id))
                    .group_by(col).order_by(func.count(UsageEvent.id).desc()))
            if limit:
                stmt = stmt.limit(limit)
            return session.exec(stmt).all()

        verdict = {(v or "n/a"): c for v, c in grouped(UsageEvent.verdict)}
        signed = {bool(b): c for b, c in grouped(UsageEvent.signed_in)}
        top_fsa = [(f or "—", c) for f, c in grouped(UsageEvent.fsa, limit=10)]
        top_region = [(r or "—", c) for r, c in grouped(UsageEvent.region, limit=10)]

        prices = [p for p in session.exec(select(UsageEvent.price)).all() if p is not None]

    buckets = []
    for lo, hi, label in _PRICE_BUCKETS:
        buckets.append((label, sum(1 for p in prices if lo <= p < hi)))

    return {
        "total": total,
        "last_7": last_7,
        "last_30": last_30,
        "verdict": verdict,
        "buy": verdict.get("buyer", 0),
        "rent": verdict.get("renter", 0),
        "signed_in": signed.get(True, 0),
        "anonymous": signed.get(False, 0),
        "top_fsa": top_fsa,
        "top_region": top_region,
        "price_buckets": buckets,
    }
