# Methodology Gaps & Concerns — Buy vs Rent Model

Status notes on the rent-vs-buy comparison logic, captured 2026-06-25 for later
revisiting. The numbers below reference the baseline scenario:
**$1,000,000 home / $200,000 down / 30yr / 4.4% fixed / M2J 0E8**, which currently
shows the **renter ahead by ~$1.57M at year 30** ($5.95M vs $4.39M).

---

## ✅ UPDATE 2026-06-26 — Tax layer implemented (gaps #1 & #2 addressed)

A registered-account + capital-gains **tax layer** now ships in
`evaluator/tax.py` and is wired through the projection, CLI, web form, and
Chart 5. Specifically:

- **Renter portfolio is now taxed at liquidation** (gap #1): TFSA tax-free,
  RRSP fully taxed as ordinary income, taxable account taxed on its capital
  gains (50% inclusion × marginal rate).
- **Principal-residence exemption credited** (gap #2): the owner's home equity is
  never taxed.
- New inputs: **age** (→ TFSA cumulative room), **income** (→ RRSP room + marginal
  rate), and an **account strategy** toggle (*shelter-first* TFSA→RRSP→taxable, vs
  *taxable-only*). RRSP refunds are reinvested; withdrawals/realized gains are
  taxed at a lower **retirement** marginal rate (default 30%).
- Chart 5 and the headline net-worth gap are now **after-tax**.

**Tax-layer simplifications worth revisiting:** single province (Ontario);
brackets/limits ~2024-2026, not indexed forward; assumes TFSA room is fully
unused at the start and RRSP has *no carried-forward* room (annual only); cap
gains realized as a lump at term end at the retirement rate; one flat retirement
rate.

## ✅ UPDATE 2026-06-26 — Transaction costs implemented (gaps #3 & #4 addressed)

Purchase + sale transaction costs now ship in `evaluator/tax.py` and flow
through the model (toggle with `--no-transaction-costs`):

- **Purchase (gap #4):** land-transfer tax — Ontario provincial + Toronto
  municipal for Toronto FSAs (≈$33k on a $1M home), Ontario LTT as a national
  proxy elsewhere — plus ~$2,500 legal/inspection. Optional **first-time-buyer**
  rebates. The buyer's closing costs are sunk, so that same cash is credited to
  the renter's invested year-0 lump (keeps cash flows matched).
- **Sale (gap #3):** ~5% realtor commission + 13% HST on it + legal, netted off
  the owner's equity in the after-tax net-worth comparison (Chart 1's gross
  equity is untouched).

**New result (baseline, age 35 / $120k income / shelter-first, with tax +
transaction costs):** renter ahead ~**$2.38M** after tax (vs ~$1.60M with tax
only) — transaction costs push toward renting, as expected. The result is still
dominated by the appreciation assumption (now made visible — see below).

## ✅ UPDATE 2026-06-26 — Sensitivity heatmap implemented (gap #6 addressed)

`projections.build_sensitivity()` re-runs the full after-tax model across a grid
of **home appreciation (3-8%) × investment return (6-11%)** and renders it as
**Chart 6**: a green/red heatmap of the year-T net-worth gap, with the scenario's
own cell outlined. (Toggle with `--no-sensitivity`.)

It makes the fragility unmistakable: at the baseline, **19 of 36 cells favor
buying** — the verdict flips with a ~1-2 point shift in either assumption. The
baseline lands at 5% appreciation / 10% return (−$2.38M, renting), but at 7%
appreciation / 8% return buying wins by ~$2.65M. The point estimate should never
be read without this chart.

Remaining open items: gap **#7 (minor polish)**. (Gap #5 is intentionally
out of scope — see below.)

---

## What the model gets right ✅

The core "buy vs. rent-and-invest-the-difference" framework is sound and
correctly implemented:

- **Matched cash flows.** Both scenarios spend the same amount each month. The
  renter invests the down payment up front plus `MAX(0, ownership_cost − rent)`;
  the owner invests `MAX(0, rent − ownership_cost)` after crossover. Outlays are
  equal in both regimes → a genuine apples-to-apples wealth comparison.
- **No double-counting.** Owner net worth = home equity + side investments;
  renter net worth = portfolio. The principal the owner "overpays" vs rent is
  captured as equity, not also as an investment.
- **Down-payment opportunity cost** is correctly credited to the renter.

So the comparison is directionally valid. The issues below affect the
**magnitude** of the result, not the structure — but several are large enough to
plausibly flip the conclusion.

---

## Gaps that bias the result ⚠️

Ordered by materiality. "Direction" = which side the omission currently flatters.

### 1. Capital-gains tax on the renter's portfolio — **overstates renter** (large)
The renter's ~$5.95M is roughly **$5.5M of investment gains**. In a taxable
account, Canadian capital gains are taxed (50% inclusion × marginal rate ≈ ~25%
effective at high incomes) → on the order of **$1M+ owed at liquidation**.
TFSA/RRSP shelters only a fraction of a portfolio this size. The model applies
**no tax** to the portfolio.

### 2. Principal-residence exemption — **understates buyer** (large)
A Canadian primary residence is **100% capital-gains-tax-free** on sale (see
`prompts.md`: "is primary house taxed — no"). The home's ~$4.3M terminal value
carries no tax, while the renter's gains do. By taxing neither side, the model
quietly erases the single biggest tax advantage of buying in Canada.

> Gaps #1 and #2 together are the headline issue: correcting them roughly cancels
> the renter's $1.57M lead before any other adjustment.

### 3. Selling costs — **overstates buyer** (medium)
Realtor commission (~5%) + HST on commission on a ~$4.3M sale ≈ **~$240k** haircut
on terminal equity. Not modeled.

### 4. Purchase transaction costs — **overstates buyer** (small–medium)
Toronto levies **both** Ontario and municipal land-transfer tax ≈ **~$33k** on a
$1M home, plus legal/inspection fees (~$2–3k). First-time-buyer rebates may
offset part. Not modeled. (A `closing_costs` param is referenced but unused.)

### 5. Terminal asymmetry / imputed rent — **OUT OF SCOPE (decided 2026-06-26)**
~~At year 30 the owner lives nearly rent-free while the renter keeps paying rent,
and the year-30 snapshot doesn't value the owner's lower future housing cost.~~

**Decision: not modelling this — intentionally out of scope.** The tool's horizon
*is* the mortgage term, and the year-T net-worth snapshot already counts both
assets at market value (the home AND the portfolio), so the balance sheet is
complete at that instant. The "owner lives rent-free afterward" concern is
neutralized by the renter's option to **buy a house at year T** with their (now
larger) portfolio — leaving them in the same housing position with cash to spare.
Extending past the horizon would only add assumptions (extra years, post-term
behavior, future prices/rates) and more fragility, not clarity. The model is also
already slightly conservative toward the owner (it nets selling costs off equity
as if they liquidate at year T).

### 6. Headline is fragile to the appreciation assumption — **sensitivity** (high)
The owner controls a $1M appreciating asset on $200k down — **5:1 leverage** — so
the result hinges almost entirely on the **home appreciation (5%) vs S&P return
(10%)** gap.
- Current run uses **5%** appreciation (deliberately conservative).
- Scraper found the **historical GTA rate is ~7%**.
- At ~7%, the owner likely wins outright even before the tax corrections.

The point estimate is far less trustworthy than the sensitivity. A small
appreciation × return grid (e.g. 4/5/6/7% × 7/10%) would make this visible.

### 7. Smaller modeling simplifications
- ~~**Rent comparable is uncertain.** $3,800/mo implies a ~4.6% gross yield;
  Toronto detached yields run ~3–4%, so true comparable rent may be *lower*,
  which would let the renter invest more early (favors renter). Big lever,
  thinly sourced (scraper flagged this).~~ **Addressed (2026-06-30):** rent is no
  longer a flat regional constant — it is scaled from each region's benchmark
  price/rent pair by the entered home price via a sub-linear price-to-rent
  elasticity (rent ≈ price^0.7, `data.RENT_PRICE_ELASTICITY`), so gross yields
  fall with price as observed in-market (Toronto now ~3% at the detached
  benchmark, higher for cheaper units). Still overridable via `--rent` / slider.
  Remaining uncertainty: benchmark prices are CMA-level point estimates and the
  elasticity is a single market-wide constant, not neighbourhood-specific.
- **No inflation adjustment.** All figures nominal 2056 dollars. Fine for a
  like-for-like comparison, but the absolute numbers look bigger than they feel.
- **Home insurance held flat** at $1,500/yr (should grow with inflation; minor).
- **Property tax** grows on its own bill at 3.5%/yr, decoupled from appreciation
  — realistic, not a flaw, but worth noting.

---

## Net effect

The omissions push in both directions but do **not** cancel:

| Correction | Direction | Rough magnitude |
|---|---|---|
| Capital-gains tax on portfolio | → buyer | ~$1M+ |
| Principal-residence exemption | → buyer | (keeps owner's ~$4.3M tax-free) |
| Selling costs | → renter | ~$240k |
| Purchase transaction costs | → renter | ~$35k |
| Lower terminal housing cost | → buyer | not captured |

The investment-tax + principal-residence pair (favoring the buyer) clearly
outweighs the transaction costs (favoring the renter). **Bottom line: the current
model tilts toward renting by ignoring Canadian tax reality; a corrected version
would likely land near parity at 5% appreciation and favor buying at ~7%.**

---

## Recommended fixes, in priority order

1. ~~**Tax layer (highest value).** Capital-gains tax on the renter's taxable
   portfolio, with optional TFSA/RRSP sheltering; principal-residence exemption
   for the owner.~~ **DONE 2026-06-26** — see the update note at the top.
2. ~~**Transaction costs.** Land-transfer tax (Ontario + Toronto) at purchase;
   realtor commission + HST at sale.~~ **DONE 2026-06-26** — see the second
   update note at the top.
3. ~~**Sensitivity output.** An appreciation × return grid so the result's
   fragility is obvious at a glance.~~ **DONE 2026-06-26** — Chart 6, see the
   third update note at the top.
4. ~~**Terminal-value handling.** Extend a few years past payoff, or annotate the
   owner's post-payoff housing-cost advantage.~~ **OUT OF SCOPE 2026-06-26** —
   see gap #5; the fixed-horizon net-worth snapshot is complete and the renter
   can buy at term end.
5. **Lower-priority polish.** Inflation-adjusted (real) view toggle; grow
   insurance; let rent comparable be sourced/overridden more transparently.
