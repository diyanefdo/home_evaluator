"""Free address -> postal-code geocoding, to auto-fill the region from an address.

The evaluator only needs a **postal code** (really just its Forward Sortation
Area) to route a scenario to the right regional assumptions and land-transfer
rules. Typing a full street address is friendlier than knowing your FSA, so this
module resolves an address to its Canadian postal code via the free
**OpenStreetMap Nominatim** service.

Design goals mirror ``evaluator.live``:
- **stdlib only** (``urllib``) — no new dependency;
- **never block the app**: short timeout, and any failure returns ``None`` so the
  UI simply falls back to manual postal entry;
- **cache** results on disk with a long TTL (addresses don't move) so we respect
  Nominatim's usage policy (<=1 req/s, meaningful User-Agent, cache results).

Nominatim's ToS asks for a valid identifying User-Agent and discourages heavy
bulk use; this is a low-volume, cached, single-result lookup. For a high-traffic
public deployment, swap in a paid geocoder (the interface here is deliberately
tiny).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Identify the app per Nominatim policy; include a contact per their guidance.
_USER_AGENT = "home-evaluator/1.0 (buy-vs-rent calculator; +https://github.com/)"

_CACHE_DIR = Path(os.environ.get("EVALUATOR_CACHE_DIR", Path(tempfile.gettempdir()) / "home_evaluator_cache"))
_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60   # addresses are stable; cache a month
_HTTP_TIMEOUT = 6.0

# Canadian postal code: A1A 1A1 (a letter-digit-letter, optional space, digit-letter-digit).
_POSTAL_RE = re.compile(r"[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d")


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"geo_{key}.json"


def _cache_key(query: str) -> str:
    # Filesystem-safe, collision-resistant enough for a per-address cache file.
    import hashlib
    return hashlib.sha1(query.strip().lower().encode("utf-8")).hexdigest()[:16]


def _read_cache(key: str, *, max_age: float | None) -> dict | None:
    try:
        payload = json.loads(_cache_path(key).read_text())
    except (OSError, ValueError):
        return None
    if max_age is not None and (time.time() - payload.get("_cached_at", 0)) > max_age:
        return None
    return payload


def _write_cache(key: str, payload: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(json.dumps(dict(payload, _cached_at=time.time())))
    except OSError:
        pass  # caching is best-effort


def _normalize_postal(code: str) -> str:
    """Return a canonical ``A1A 1A1`` postal code (uppercased, single space)."""
    compact = re.sub(r"\s+", "", code).upper()
    return f"{compact[:3]} {compact[3:]}" if len(compact) == 6 else compact


def geocode_address(query: str, *, force: bool = False) -> dict | None:
    """Resolve a Canadian address to a postal code + place labels, or ``None``.

    Returns e.g.::

        {"postal": "V6B 1A1", "fsa": "V6B", "city": "Vancouver",
         "province": "British Columbia", "label": "Vancouver, BC, Canada",
         "lat": 49.28, "lon": -123.11, "source": "OpenStreetMap Nominatim"}

    Strategy: fresh cache -> live fetch -> stale cache -> None. Only results that
    include a postal code are returned (the postal code is the whole point).
    """
    query = (query or "").strip()
    if len(query) < 3:
        return None
    key = _cache_key(query)
    if not force:
        cached = _read_cache(key, max_age=_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached["data"]

    params = urllib.parse.urlencode({
        "q": query, "format": "jsonv2", "addressdetails": 1,
        "countrycodes": "ca", "limit": 1,
    })
    req = urllib.request.Request(f"{_NOMINATIM}?{params}", headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 - fixed https host
            hits = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - network/parse failure: fall back gracefully
        stale = _read_cache(key, max_age=None)
        return stale["data"] if stale else None

    data = _extract(hits, query)
    if data is not None:
        _write_cache(key, {"data": data})
    return data


def _extract(hits: list, query: str) -> dict | None:
    """Pull a postal code + place fields out of Nominatim results.

    Prefers the postal code in the top hit's structured address; falls back to a
    postal-code pattern found anywhere in the query string (users often type it).
    """
    top = hits[0] if hits else {}
    addr = top.get("address", {}) if isinstance(top, dict) else {}
    postal = addr.get("postcode", "")
    if not postal:
        m = _POSTAL_RE.search(query)
        postal = m.group(0) if m else ""
    postal = _normalize_postal(postal)
    if len(postal.replace(" ", "")) != 6:
        return None   # no usable postal code -> let the UI fall back to manual entry

    city = (addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("municipality") or addr.get("county") or "")
    return {
        "postal": postal,
        "fsa": postal[:3],
        "city": city,
        "province": addr.get("state", ""),
        "label": top.get("display_name", ""),
        "lat": float(top["lat"]) if top.get("lat") else None,
        "lon": float(top["lon"]) if top.get("lon") else None,
        "source": "OpenStreetMap Nominatim",
    }


if __name__ == "__main__":  # smoke test: python3 -m evaluator.geocode "CN Tower, Toronto"
    import sys
    q = " ".join(sys.argv[1:]) or "441 Yonge St, Toronto"
    r = geocode_address(q, force=True)
    if r:
        print(f"{q!r} -> {r['postal']}  ({r['city']}, {r['province']})")
        print(f"  {r['label']}")
    else:
        print(f"No postal code found for {q!r} (offline, or address not found).")
