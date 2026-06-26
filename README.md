# Canadian Buy-vs-Rent Home Evaluator

A reusable command-line tool that turns **four inputs** — house price, down
payment, mortgage term, and a Canadian postal code — into **four financial
charts** plus an executive summary comparing buying vs. renting-and-investing.

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
python3 -m evaluator.cli --price 1000000 --down 200000 --years 30 --postal "M2J 0E8"
```

`--down` accepts a dollar amount (`200000`) or a percentage of price (`20%`).
Charts are written to `./charts_output` by default (`--out` to change).

### The four charts

1. **Home value, mortgage balance & equity** over the term, with loan-paid-off
   milestone markers.
2. **Cumulative cost of ownership** stacked breakdown (down payment, principal,
   interest, property tax, insurance + HOA + maintenance).
3. **Renter scenario** — down payment invested up front + monthly
   `MAX(0, ownership cost − rent)` dollar-cost-averaged into the S&P 500.
4. **Homeowner-advantage scenario** — after the crossover year (when rising rent
   exceeds the fixed ownership cost), the homeowner invests
   `MAX(0, rent − ownership cost)`.

### Assumption overrides

Every regional assumption can be overridden from the CLI (see `--help`):
`--rate`, `--appreciation`, `--rent`, `--rent-growth`, `--property-tax-rate`,
`--investment-return`, `--insurance`, `--hoa`.

Postal codes in North York (FSAs `M2H/M2J/M2K/M2N`) use Toronto-specific data;
all others fall back to Canada-wide defaults. Add more regions in
`evaluator/data.py`.

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
