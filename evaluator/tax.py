"""Canadian tax + registered-account assumptions for the buy-vs-rent model.

This is the tax layer added to address the two largest methodology gaps (see
``knowledge/METHODOLOGY_GAPS.md``): the renter's taxable investment gains were
untaxed, and the owner's principal-residence capital-gains exemption was not
credited. It provides:

* combined Ontario + federal **marginal income-tax rates** (used for the RRSP
  contribution refund),
* **TFSA** cumulative + annual contribution room (age-derived),
* **RRSP** annual contribution room (income-derived: 18% of earned income up to
  the annual dollar cap),
* the **capital-gains inclusion rate** and a default **retirement marginal rate**
  applied to RRSP withdrawals and realized capital gains at liquidation.

All figures are CAD, nominal. Brackets/limits are ~2024-2026 values; they are
deliberately simple (a single province, no surtax micro-steps, no indexing of
future brackets) — good enough for a long-run comparison, and every value is
overridable upstream. See SOURCES.

The principal-residence exemption is handled in the projection engine simply by
NOT taxing the owner's home equity; this module only deals with the investment
side that *is* taxed.
"""

from __future__ import annotations

# Year the tool treats as "now" for room accumulation. Keep in sync with the
# data module's research vintage.
CURRENT_YEAR = 2026

# Capital gains: 50% of the gain is included in income, taxed at the marginal
# rate. (Ignores the >$250k/yr 66.7% inclusion proposal, which was not in force.)
CAPITAL_GAINS_INCLUSION = 0.50

# Default marginal rate applied at liquidation (retirement): used for RRSP
# withdrawals (taxed as ordinary income) and as the marginal rate on realized
# capital gains in the taxable account. Lower than a working-years rate — the
# usual real-world RRSP advantage. Overridable via params.
RETIREMENT_MARGINAL_RATE = 0.30

# RRSP annual contribution: 18% of prior-year earned income, capped.
RRSP_CONTRIBUTION_PCT = 0.18
RRSP_ANNUAL_DOLLAR_CAP = 33_000.0  # ~2026 (2024 $31,560 / 2025 $32,490)

# Combined Ontario + federal marginal rates on regular income, 2024 (monotonic
# simplification — real Ontario surtax makes a couple of micro-steps non-mono).
# Each tuple is (lower_threshold, marginal_rate_above_it).
_ON_FED_MARGINAL = [
    (0.0, 0.2005),
    (51_446.0, 0.2415),
    (55_867.0, 0.2965),
    (90_599.0, 0.3148),
    (102_894.0, 0.3389),
    (106_732.0, 0.3791),
    (111_733.0, 0.4341),
    (150_000.0, 0.4497),
    (173_205.0, 0.4829),
    (220_000.0, 0.4985),
    (246_752.0, 0.5353),
]

# TFSA dollar limit by year (started 2009). Years beyond the table use
# TFSA_DEFAULT_LIMIT (kept flat; not indexed for simplicity).
_TFSA_LIMITS = {
    2009: 5_000, 2010: 5_000, 2011: 5_000, 2012: 5_000,
    2013: 5_500, 2014: 5_500, 2015: 10_000, 2016: 5_500,
    2017: 5_500, 2018: 5_500, 2019: 6_000, 2020: 6_000,
    2021: 6_000, 2022: 6_000, 2023: 6_500, 2024: 7_000, 2025: 7_000,
}
TFSA_DEFAULT_LIMIT = 7_000.0
TFSA_START_YEAR = 2009
TFSA_ELIGIBILITY_AGE = 18


def marginal_rate(income: float) -> float:
    """Combined Ontario+federal marginal rate on the next dollar at ``income``."""
    rate = _ON_FED_MARGINAL[0][1]
    for threshold, r in _ON_FED_MARGINAL:
        if income >= threshold:
            rate = r
        else:
            break
    return rate


def tfsa_annual_limit(year: int = CURRENT_YEAR) -> float:
    """TFSA dollar limit for a given calendar year."""
    return float(_TFSA_LIMITS.get(year, TFSA_DEFAULT_LIMIT))


def tfsa_cumulative_room(age: int, current_year: int = CURRENT_YEAR) -> float:
    """Total TFSA room accumulated by ``current_year`` for a person now ``age``.

    Assumes the room has never been used (the invest-the-difference money is
    fresh) — room accrues every year from the later of 2009 or the year the
    person turned 18, through the current year.
    """
    if age < TFSA_ELIGIBILITY_AGE:
        return 0.0
    birth_year = current_year - int(age)
    turned_18_year = birth_year + TFSA_ELIGIBILITY_AGE
    start = max(TFSA_START_YEAR, turned_18_year)
    return float(sum(tfsa_annual_limit(y) for y in range(start, current_year + 1)))


def rrsp_annual_room(income: float) -> float:
    """RRSP contribution room for one year: 18% of earned income, capped."""
    return min(RRSP_CONTRIBUTION_PCT * float(income), RRSP_ANNUAL_DOLLAR_CAP)


SOURCES = {
    "marginal_rates": "https://www.taxtips.ca/taxrates/on.htm (Ontario combined 2024)",
    "tfsa_limits": "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/tax-free-savings-account/contributions.html",
    "rrsp_limits": "https://www.canada.ca/en/revenue-agency/services/tax/registered-plans-administrators/pspa/mp-rrsp-dpsp-tfsa-limits-ympe.html",
    "capital_gains": "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/about-your-tax-return/tax-return/completing-a-tax-return/personal-income/line-12700-capital-gains.html",
}
