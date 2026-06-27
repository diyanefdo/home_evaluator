# Canadian Buy-vs-Rent Home Evaluator

A reusable command-line tool that turns a handful of inputs — house price, down
payment, mortgage term, Canadian postal code, plus age/income/strategy for the
tax layer — into **six financial charts** plus an executive summary comparing
buying vs. renting-and-investing.

The work is split across three layers, each built from one of the project's
sub-agents:

| Layer | File | Built by agent | Responsibility |
|-------|------|----------------|----------------|
| Data | `evaluator/data.py` | `canada-housing-financial-scraper` | Regional + national assumptions (appreciation, mortgage/rent/tax rates, S&P 500 CAGR) |
| Projection | `evaluator/projections.py` | `future-projection-analyst` | Year/month-by-year projection engine (amortization, rent, maintenance, investment portfolios, crossover) |
| Charts | `evaluator/charts.py` | `mortgage-investment-charter` | Renders the four PNG charts |
| Orchestrator | `evaluator/cli.py` | — | Wires the layers together behind a CLI |

## Usage

```bash
python3 -m evaluator.cli --price 1000000 --down 200000 --years 30 --postal "M2J 0E8" \
    --age 35 --income 120000 --account-strategy shelter-first
```

`--down` accepts a dollar amount (`200000`) or a percentage of price (`20%`).
Charts are written to `./charts_output` by default (`--out` to change).

### Tax layer (registered accounts + capital gains)

The model is **after-tax**. `--age` sets your cumulative **TFSA** room; `--income`
sets annual **RRSP** room (18% of income, capped) and your marginal tax rate.
`--account-strategy` chooses where the invested savings go:

- `shelter-first` (default) — fill TFSA, then RRSP, then a taxable account;
- `taxable-only` — everything in a taxable account.

At liquidation the renter's portfolio is taxed (TFSA free, RRSP as income,
taxable on gains); the owner's home is a **principal residence** and is
capital-gains-tax-free. RRSP refunds are reinvested and withdrawals are taxed at
a lower retirement rate (`--retirement-rate`, default `0.30`).

**Transaction costs** are included too: **land-transfer tax** at purchase
(Ontario provincial + Toronto municipal for North York FSAs; ~$33k on a $1M home)
plus legal/inspection, and **realtor commission + HST** at sale (~5% + 13%). Use
`--first-time-buyer` for LTT rebates, or `--no-transaction-costs` to exclude them.
See [`knowledge/METHODOLOGY_GAPS.md`](knowledge/METHODOLOGY_GAPS.md) for
assumptions and limitations.

### The six charts

1. **Home value, mortgage balance & equity** over the term, with loan-paid-off
   milestone markers.
2. **Cumulative cost of ownership** stacked breakdown (down payment, principal,
   interest, property tax, insurance + HOA + maintenance).
3. **Renter scenario** — down payment invested up front + monthly
   `MAX(0, ownership cost − rent)` dollar-cost-averaged into the S&P 500.
4. **Homeowner-advantage scenario** — after the crossover year (when rising rent
   exceeds the fixed ownership cost), the homeowner invests
   `MAX(0, rent − ownership cost)`.
5. **Total net worth — homeowner vs renter (after tax)** — head-to-head wealth
   over the term (owner equity + side investments vs renter portfolio), with the
   lead-change year marked. Both scenarios spend the same each month, so it's
   apples-to-apples. Net worth is shown **after tax** (see the tax layer above).
6. **Sensitivity heatmap** — the year-T net-worth gap across a grid of home
   appreciation × investment return (green = buying wins, red = renting wins),
   with your scenario's cell outlined. The result hinges on these two
   assumptions, so this shows how fragile (or robust) the verdict is.
   Disable with `--no-sensitivity`.

### Assumption overrides

Every regional assumption can be overridden from the CLI (see `--help`):
`--rate`, `--appreciation`, `--rent`, `--rent-growth`, `--property-tax-rate`,
`--investment-return`, `--insurance`, `--hoa`.

### Regional coverage & live data

Postal-code routing (most specific first): North York FSAs (`M2H/M2J/M2K/M2N`)
use Toronto-specific data; any other `M` code uses City-of-Toronto data (Toronto
property tax + municipal land-transfer tax); the rest of **Ontario** (`K/L/N/P`)
uses an Ontario-wide default tier; everything else falls back to Canada-wide
defaults. Add more regions in `evaluator/data.py`.

The **5-year mortgage rate is live**: with `--live` (CLI) or `EVALUATOR_LIVE_DATA`
(web; on by default) the tool overlays the current discounted fixed rate derived
from the **Bank of Canada Valet API** (5-yr Government of Canada benchmark yield +
a lender spread). Results are cached (12h) and fall back to the baked-in regional
rate if offline. See `evaluator/live.py`. Other inputs (appreciation, rent,
property tax) are slow-moving researched constants per region.

## Run as a web service (Docker)

A thin FastAPI layer (`webapp.py`) serves the same analysis as a web page. The
six charts are **interactive** (client-side [Plotly](https://plotly.com/javascript/),
vendored locally at `static/plotly.min.js` — no CDN): hover tooltips, clickable
legend to toggle series, and zoom. There are also **what-if sliders** (down
payment, rate, appreciation, investment return, rent) that re-render the charts
and verdict live via `/api/recompute`. The web path sends chart *data* to the
browser (no server-side image rendering); the CLI still writes matplotlib PNGs.

```bash
# local
pip install -r requirements.txt
uvicorn webapp:app --host 0.0.0.0 --port 8000      # open http://localhost:8000

# containerized
docker compose up -d --build                       # open http://localhost:8000
```

Endpoints: `/` (form), `/evaluate?price=1000000&down=200000&years=30&postal=M2J+0E8`,
`/healthz`. To reach it from other devices over a **private link** (Tailscale,
Cloudflare Tunnel, or ngrok) — including WSL2 notes — see
[`knowledge/DOCKER_PRIVATE_DEPLOYMENT.md`](knowledge/DOCKER_PRIVATE_DEPLOYMENT.md).

## Data vintage & caveats

Regional figures were gathered 2026-06-25 (sources cited in `evaluator/data.py`).
Projections use long-run historical assumptions and are **not financial advice**;
past investment returns do not guarantee future results.

## Development

Each module is independently runnable for testing:

```bash
python3 -m evaluator.charts        # renders synthetic charts to ./_chart_preview
python3 -m evaluator.projections   # runs the projection smoke test
```
