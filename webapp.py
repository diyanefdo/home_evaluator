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
import shutil
import tempfile

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from evaluator import cli, projections, charts

app = FastAPI(title="Canadian Buy-vs-Rent Home Evaluator")

# Sanity bounds so the public-facing handler can't be handed absurd work.
MAX_YEARS = 50
MAX_PRICE = 100_000_000


PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Home Evaluator</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}}
 h1{{font-size:1.5rem}} label{{display:block;margin:.7rem 0 .2rem;font-weight:600}}
 input{{width:100%;padding:.5rem;border:1px solid #ccc;border-radius:6px;font-size:1rem}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}
 button{{margin-top:1.2rem;padding:.6rem 1.4rem;font-size:1rem;border:0;border-radius:6px;
   background:#1f77b4;color:#fff;cursor:pointer}}
 img{{width:100%;margin:1rem 0;border:1px solid #eee;border-radius:6px}}
 .summary{{background:#f5f8fb;border:1px solid #dce6ef;border-radius:8px;padding:1rem 1.2rem;margin:1rem 0}}
 .err{{background:#fdf3ef;border:1px solid #e0b4a4;border-radius:8px;padding:1rem 1.2rem;color:#8c3b1e}}
 a{{color:#1f77b4}}
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
def home() -> str:
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
