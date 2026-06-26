"""FastAPI web layer for the Canadian buy-vs-rent home evaluator.

Wraps the existing ``evaluator`` package: takes the four inputs from a form (or
query string), runs the projection + charting pipeline, and returns a single
HTML page with all five charts embedded inline (base64 PNGs — no file serving).

Run locally:
    uvicorn webapp:app --host 0.0.0.0 --port 8000
    # then open http://localhost:8000

See knowledge/DOCKER_PRIVATE_DEPLOYMENT.md for containerized + private-link use.
"""

from __future__ import annotations

import argparse
import base64
import html
import os
import secrets
import shutil
import tempfile

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from evaluator import cli, projections, charts

app = FastAPI(title="Canadian Buy-vs-Rent Home Evaluator")

# --------------------------------------------------------------------------- #
# Optional HTTP basic-auth (recommended when exposed on a public link).
#
# Toggle with EVALUATOR_AUTH:
#   - unset       -> auto: auth is ON when EVALUATOR_PASSWORD is set, else OFF
#   - on/1/true   -> force ON  (requires EVALUATOR_PASSWORD, else startup error)
#   - off/0/false -> force OFF (no login, even if a password is configured)
# Username defaults to "admin"; override with EVALUATOR_USER.
# --------------------------------------------------------------------------- #
_AUTH_USER = os.environ.get("EVALUATOR_USER", "admin")
_AUTH_PASS = os.environ.get("EVALUATOR_PASSWORD")  # None/"" => no credentials set
_security = HTTPBasic(auto_error=False)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _auth_enabled() -> bool:
    """Decide whether basic-auth is active, honouring the EVALUATOR_AUTH flag."""
    flag = os.environ.get("EVALUATOR_AUTH", "").strip().lower()
    if flag in _TRUTHY:
        if not _AUTH_PASS:
            raise RuntimeError(
                "EVALUATOR_AUTH is on but EVALUATOR_PASSWORD is not set — "
                "set a password or turn the flag off."
            )
        return True
    if flag in _FALSY:
        return False
    if flag:  # set to something we don't recognise
        raise RuntimeError(
            f"EVALUATOR_AUTH={flag!r} is not understood; use on/off (or leave unset)."
        )
    # Unset => auto: on only when a password is configured.
    return bool(_AUTH_PASS)


_AUTH_ON = _auth_enabled()


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """Enforce basic-auth when enabled (see _auth_enabled); else no-op."""
    if not _AUTH_ON:
        return
    ok = credentials is not None and secrets.compare_digest(
        credentials.username, _AUTH_USER
    ) and secrets.compare_digest(credentials.password, _AUTH_PASS)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

# Sanity bounds so the public-facing handler can't be handed absurd work.
MAX_YEARS = 50
MAX_PRICE = 100_000_000


PAGE_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Home Evaluator</title>
<style>
 *{box-sizing:border-box}
 body{font-family:system-ui,-apple-system,sans-serif;max-width:1000px;margin:0 auto;
   padding:1.5rem 1rem;color:#1a1a1a;line-height:1.45;-webkit-text-size-adjust:100%}
 h1{font-size:1.4rem;margin:.2rem 0 1rem}
 label{display:block;margin:.7rem 0 .25rem;font-weight:600}
 /* 16px input font stops iOS Safari from auto-zooming on focus */
 input{width:100%;padding:.6rem;border:1px solid #ccc;border-radius:8px;font-size:16px}
 /* auto-fit collapses the form from 2 columns to 1 on narrow screens */
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.75rem 1rem}
 button{margin-top:1.2rem;padding:.8rem 1.4rem;font-size:1rem;border:0;border-radius:8px;
   background:#1f77b4;color:#fff;cursor:pointer;width:100%;max-width:260px}
 img{width:100%;height:auto;display:block;margin:1rem 0;border:1px solid #eee;border-radius:8px}
 .summary{background:#f5f8fb;border:1px solid #dce6ef;border-radius:10px;padding:.9rem 1.1rem;margin:1rem 0}
 .err{background:#fdf3ef;border:1px solid #e0b4a4;border-radius:10px;padding:.9rem 1.1rem;color:#8c3b1e}
 a{color:#1f77b4}
 @media (max-width:480px){
   body{padding:1rem .8rem}
   h1{font-size:1.2rem}
   button{width:100%;max-width:none}
 }
</style></head><body>"""

PAGE_FOOT = "</body></html>"

FORM = PAGE_HEAD + """
<h1>Canadian Buy-vs-Rent Evaluator</h1>
<p>Enter a scenario to generate the five comparison charts.</p>
<form action="/evaluate" method="get">
  <div class="grid">
    <div><label>House price ($)</label><input name="price" value="1000000"></div>
    <div><label>Down payment ($ or %)</label><input name="down" value="200000"></div>
    <div><label>Mortgage term (years)</label><input name="years" value="30"></div>
    <div><label>Postal code</label><input name="postal" value="M2J 0E8"></div>
  </div>
  <button type="submit">Evaluate</button>
</form>
<p style="color:#777;font-size:.85rem;margin-top:2rem">
  Projections use long-run historical assumptions and are not financial advice.
</p>
""" + PAGE_FOOT


def _error_page(message: str) -> HTMLResponse:
    body = (
        PAGE_HEAD
        + f'<h1>Home Evaluator</h1><div class="err">{html.escape(message)}</div>'
        + '<p><a href="/">&larr; back</a></p>'
        + PAGE_FOOT
    )
    return HTMLResponse(body, status_code=400)


@app.get("/", response_class=HTMLResponse)
def home(_: None = Depends(require_auth)) -> str:
    return FORM


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/evaluate", response_class=HTMLResponse)
def evaluate(
    price: float,
    down: str = "20%",
    years: int = 30,
    postal: str = "M2J 0E8",
    # Optional power-user overrides (default to regional data when omitted).
    rate: float | None = None,
    appreciation: float | None = None,
    rent: float | None = None,
    rent_growth: float | None = None,
    property_tax_rate: float | None = None,
    investment_return: float | None = None,
    insurance: float = 1500.0,
    hoa: float = 0.0,
    _: None = Depends(require_auth),
):
    # --- validate inputs up front (don't rely on the CLI's SystemExit) -------
    if not (0 < price <= MAX_PRICE):
        return _error_page(f"Price must be between $1 and ${MAX_PRICE:,.0f}.")
    if not (1 <= years <= MAX_YEARS):
        return _error_page(f"Mortgage term must be between 1 and {MAX_YEARS} years.")
    try:
        down_amount = cli._parse_down_payment(down, price)
    except ValueError:
        return _error_page(f"Could not read down payment '{down}'. Use e.g. 200000 or 20%.")
    if not (0 < down_amount < price):
        return _error_page("Down payment must be greater than $0 and less than the price.")

    # --- run the pipeline (reuse the CLI's validated param mapping) ----------
    args = argparse.Namespace(
        price=price, down=down, years=years, postal=postal,
        rate=rate, appreciation=appreciation, rent=rent, rent_growth=rent_growth,
        property_tax_rate=property_tax_rate, investment_return=investment_return,
        insurance=insurance, hoa=hoa, out=None, no_charts=False,
    )
    try:
        params = cli.build_engine_params(args)
        projection = projections.build_projection(params)
        summary = projections.compute_summary(projection, params)
    except Exception as exc:  # noqa: BLE001 - surface any modelling error to the user
        return _error_page(f"Could not evaluate this scenario: {exc}")

    out_dir = tempfile.mkdtemp(prefix="charts_")
    try:
        paths = charts.generate_charts(projection, params, out_dir)
        imgs = ""
        for p in paths:
            with open(p, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            imgs += f'<img src="data:image/png;base64,{b64}" alt="chart">'
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    sym = params["currency_symbol"]
    cy = summary.get("crossover_year")
    cross = f"~year {cy:.1f}" if cy else "never within the term"
    gap = summary["final_buyer_minus_renter"]
    leader = "buyer" if gap >= 0 else "renter"

    body = (
        PAGE_HEAD
        + f"<h1>{html.escape(params['postal_code'])} &middot; {html.escape(params['region_label'])}</h1>"
        + '<div class="summary">'
        + f"<b>{sym}{params['purchase_price']:,.0f}</b> &middot; "
        + f"{sym}{params['down_payment']:,.0f} down "
        + f"({params['down_payment'] / params['purchase_price'] * 100:.0f}%) &middot; "
        + f"{years}yr @ {params['mortgage_rate'] * 100:.2f}% fixed<br>"
        + f"Crossover: <b>{cross}</b> &middot; "
        + f"Year-{years} net-worth gap: <b>{sym}{abs(gap):,.0f}</b> in favour of the {leader}"
        + "</div>"
        + imgs
        + '<p><a href="/">&larr; run another scenario</a></p>'
        + PAGE_FOOT
    )
    return HTMLResponse(body)
