"""Live market-data fetchers (free, official sources) with caching + fallback.

Today this fetches the **5-year mortgage rate** from the Bank of Canada's
**Valet API** (https://www.bankofcanada.ca/valet/) — the one macro input that is
both volatile and available from a real, free, official API. Everything else in
``data.py`` (appreciation, rents, property tax) moves slowly and is better
researched once per region than fetched live (see ``knowledge/ROADMAP.md``).

Design goals:
- **stdlib only** (``urllib``) — no new dependency;
- **never block the app**: short timeout, and *any* failure falls back to a
  cached value, then to the baked-in default in ``data.py``;
- **cache** results on disk with a TTL so we don't hit the API on every request.

The discounted 5-year fixed rate a buyer actually gets is estimated from the
Government of Canada 5-year benchmark bond yield plus a typical lender spread
(discounted fixed ~= 5yr GoC yield + ~1.4%). The Bank of Canada's posted
"conventional 5-year" series is the *posted* rate (used for stress tests), which
runs well above what people pay, so we don't use it for the buyer's rate.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.request
from pathlib import Path

# --- Bank of Canada Valet series -------------------------------------------- #
_VALET = "https://www.bankofcanada.ca/valet/observations/{series}/json?recent=1"
_GOC_5YR_YIELD = "BD.CDN.5YR.DQ.YLD"   # Govt of Canada 5-year benchmark bond yield (%)

# Discounted 5-yr fixed ~= 5yr GoC yield + spread. Empirically ~1.3-1.6%; 1.4%
# reproduces the researched ~4.4% baseline against a ~3.0% yield.
FIXED_RATE_SPREAD = 0.014

# --- Caching ---------------------------------------------------------------- #
# Override the cache dir with EVALUATOR_CACHE_DIR (Docker mounts a tmpfs at /tmp).
_CACHE_DIR = Path(os.environ.get("EVALUATOR_CACHE_DIR", Path(tempfile.gettempdir()) / "home_evaluator_cache"))
_CACHE_TTL_SECONDS = 12 * 60 * 60   # rates barely move intraday; refresh twice a day
_HTTP_TIMEOUT = 6.0                  # keep the web request snappy; fall back on slow API


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def _read_cache(key: str, *, max_age: float | None) -> dict | None:
    path = _cache_path(key)
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if max_age is not None and (time.time() - payload.get("_cached_at", 0)) > max_age:
        return None
    return payload


def _write_cache(key: str, payload: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        body = dict(payload, _cached_at=time.time())
        _cache_path(key).write_text(json.dumps(body))
    except OSError:
        pass  # caching is best-effort; never fail the request over it


def _fetch_valet_latest(series: str) -> tuple[float, str]:
    """Return (value, observation_date) for the most recent observation."""
    url = _VALET.format(series=series)
    req = urllib.request.Request(url, headers={"User-Agent": "home-evaluator/1.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 - fixed https host
        doc = json.loads(resp.read().decode("utf-8"))
    obs = doc["observations"][-1]
    return float(obs[series]["v"]), str(obs["d"])


def live_mortgage_rate(*, spread: float = FIXED_RATE_SPREAD, force: bool = False) -> dict | None:
    """Live discounted 5-year fixed mortgage-rate estimate, or None if unavailable.

    Returns a dict with the rate (decimal), the components, and provenance::

        {"rate": 0.0442, "yield": 0.0302, "spread": 0.014,
         "as_of": "2026-06-25", "source": "Bank of Canada Valet (...)"}

    Strategy: fresh cache -> live fetch -> stale cache -> None. ``force`` skips
    the fresh-cache read (used for an explicit refresh).
    """
    key = "boc_5yr_fixed"
    if not force:
        cached = _read_cache(key, max_age=_CACHE_TTL_SECONDS)
        if cached:
            return cached["data"]

    try:
        yld_pct, as_of = _fetch_valet_latest(_GOC_5YR_YIELD)
        rate = round(yld_pct / 100.0 + spread, 5)
        data = {
            "rate": rate,
            "yield": round(yld_pct / 100.0, 5),
            "spread": spread,
            "as_of": as_of,
            "source": f"Bank of Canada Valet ({_GOC_5YR_YIELD}) + {spread * 100:.1f}% spread",
        }
        _write_cache(key, {"data": data})
        return data
    except Exception:  # noqa: BLE001 - network/parse failure: fall back gracefully
        stale = _read_cache(key, max_age=None)   # any age beats nothing
        return stale["data"] if stale else None


if __name__ == "__main__":  # tiny smoke test: python3 -m evaluator.live
    result = live_mortgage_rate(force=True)
    if result:
        print(f"Live 5yr fixed estimate: {result['rate'] * 100:.2f}%  "
              f"(yield {result['yield'] * 100:.2f}% + {result['spread'] * 100:.1f}% spread, "
              f"as of {result['as_of']})")
        print(f"  source: {result['source']}")
    else:
        print("Live rate unavailable (offline?) — app would fall back to data.py defaults.")
