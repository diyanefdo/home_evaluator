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


# --------------------------------------------------------------------------- #
# Transaction costs: land-transfer tax at purchase, realtor + legal at sale.
# --------------------------------------------------------------------------- #
REALTOR_COMMISSION_RATE = 0.05   # ~5% of sale price (total, both agents)
HST_RATE = 0.13                  # Ontario HST, charged ON the commission
SALE_LEGAL_FEE = 1_500.0         # legal/discharge fees at sale
PURCHASE_LEGAL_FEE = 2_500.0     # legal + inspection/appraisal at purchase

# Ontario provincial Land Transfer Tax — marginal brackets (lower, rate).
_ON_LTT = [
    (0.0, 0.005), (55_000.0, 0.01), (250_000.0, 0.015),
    (400_000.0, 0.02), (2_000_000.0, 0.025),
]
# Toronto Municipal LTT mirrors the provincial brackets up to $3M (the extra
# luxury tiers above $3M are not modelled).
_TORONTO_MLTT = [
    (0.0, 0.005), (55_000.0, 0.01), (250_000.0, 0.015),
    (400_000.0, 0.02), (2_000_000.0, 0.025),
]
ON_FIRST_TIME_REBATE = 4_000.0        # max Ontario first-time-buyer LTT rebate
TORONTO_FIRST_TIME_REBATE = 4_475.0   # max Toronto first-time-buyer MLTT rebate


def _bracketed_tax(price: float, brackets: list) -> float:
    """Sum a marginal-bracket tax over ``price``."""
    total = 0.0
    for i, (lo, rate) in enumerate(brackets):
        hi = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
        if price > lo:
            total += (min(price, hi) - lo) * rate
        else:
            break
    return total


def ontario_ltt(price: float) -> float:
    """Ontario provincial land-transfer tax on a purchase price."""
    return _bracketed_tax(float(price), _ON_LTT)


def toronto_mltt(price: float) -> float:
    """Toronto municipal land-transfer tax (in addition to the provincial LTT)."""
    return _bracketed_tax(float(price), _TORONTO_MLTT)


def land_transfer_tax(price: float, region: str = "ontario", first_time: bool = False) -> float:
    """Total land-transfer tax. ``region='toronto'`` adds the municipal LTT.

    For non-Ontario postal codes the provincial Ontario LTT is used as a rough
    national proxy (provinces vary widely — AB/SK have none, BC/QC differ).
    """
    total = ontario_ltt(price)
    rebate = ON_FIRST_TIME_REBATE if first_time else 0.0
    if region == "toronto":
        total += toronto_mltt(price)
        if first_time:
            rebate += TORONTO_FIRST_TIME_REBATE
    return max(0.0, total - rebate)


def purchase_closing_costs(
    price: float,
    region: str = "ontario",
    first_time: bool = False,
    legal_fee: float = PURCHASE_LEGAL_FEE,
) -> float:
    """One-time cash cost of buying: land-transfer tax + legal/inspection fees."""
    return land_transfer_tax(price, region, first_time) + float(legal_fee)


# --------------------------------------------------------------------------- #
# CMHC mortgage default insurance (required when the down payment is < 20%).
# --------------------------------------------------------------------------- #
# Insurance is mandatory for a "high-ratio" mortgage (loan-to-value > 80%, i.e.
# down payment under 20%). The one-time premium is a % of the loan amount, scaled
# by loan-to-value, and is normally ADDED TO THE MORTGAGE PRINCIPAL (financed).
# In Ontario the provincial sales tax on the premium (8%) is NOT financeable and
# is paid up front at closing.
CMHC_INSURABLE_LTV = 0.80              # insurance required above this loan-to-value
CMHC_MAX_INSURABLE_PRICE = 1_500_000.0 # homes above this can't be insured (need 20% down)

# Standard purchase premium rates by loan-to-value band (ceiling, rate).
_CMHC_PREMIUM_RATES = [
    (0.65, 0.0060), (0.75, 0.0170), (0.80, 0.0240),
    (0.85, 0.0280), (0.90, 0.0310), (0.95, 0.0400),
]
# Provincial sales tax charged on the premium (paid up front, not financed).
# Only Ontario (incl. Toronto) is modelled here; ON/MB are 8%, QC 9.975%, SK 6%.
_CMHC_PST_ON_PREMIUM = {"ontario": 0.08, "toronto": 0.08}


def cmhc_min_down_payment(price: float) -> float:
    """Minimum down payment that allows an insured mortgage (None-equiv = 20%).

    5% on the first $500k, 10% on the portion from $500k to $1.5M; above $1.5M an
    insured mortgage is unavailable, so 20% is required.
    """
    price = float(price)
    if price > CMHC_MAX_INSURABLE_PRICE:
        return 0.20 * price
    if price <= 500_000.0:
        return 0.05 * price
    return 0.05 * 500_000.0 + 0.10 * (price - 500_000.0)


def cmhc_premium_rate(ltv: float) -> float:
    """Premium rate for a given loan-to-value (0 at or below 80%)."""
    if ltv <= CMHC_INSURABLE_LTV:
        return 0.0
    for ceiling, rate in _CMHC_PREMIUM_RATES:
        if ltv <= ceiling:
            return rate
    return _CMHC_PREMIUM_RATES[-1][1]


def cmhc_insurance(price: float, down: float, region: str = "ontario") -> dict:
    """CMHC default-insurance breakdown for a purchase.

    Returns a dict::

        {"required": bool,    # down payment < 20%
         "insurable": bool,   # meets CMHC price + min-down rules
         "ltv": float, "rate": float,
         "premium": float,    # financed into the mortgage principal
         "pst": float, "pst_rate": float,  # paid up front at closing
         "reason": str|None}  # why it isn't insurable, if applicable

    Premium and PST are 0 when no insurance is required.
    """
    price = float(price)
    down = float(down)
    loan = max(0.0, price - down)
    ltv = loan / price if price > 0 else 0.0
    out = {"required": ltv > CMHC_INSURABLE_LTV, "insurable": True, "ltv": ltv,
           "rate": 0.0, "premium": 0.0, "pst": 0.0, "pst_rate": 0.0, "reason": None}
    if not out["required"]:
        return out

    if price > CMHC_MAX_INSURABLE_PRICE:
        out.update(insurable=False,
                   reason=f"homes over ${CMHC_MAX_INSURABLE_PRICE:,.0f} need 20% down (not insurable)")
        return out
    if down < cmhc_min_down_payment(price) - 1e-6:
        out.update(insurable=False,
                   reason=f"below the minimum down payment (${cmhc_min_down_payment(price):,.0f})")
        return out

    rate = cmhc_premium_rate(ltv)
    premium = rate * loan
    pst_rate = _CMHC_PST_ON_PREMIUM.get(region, 0.0)
    out.update(rate=rate, premium=premium, pst=premium * pst_rate, pst_rate=pst_rate)
    return out


SOURCES = {
    "marginal_rates": "https://www.taxtips.ca/taxrates/on.htm (Ontario combined 2024)",
    "tfsa_limits": "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/tax-free-savings-account/contributions.html",
    "rrsp_limits": "https://www.canada.ca/en/revenue-agency/services/tax/registered-plans-administrators/pspa/mp-rrsp-dpsp-tfsa-limits-ympe.html",
    "capital_gains": "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/about-your-tax-return/tax-return/completing-a-tax-return/personal-income/line-12700-capital-gains.html",
    "cmhc_premiums": "https://www.cmhc-schl.gc.ca/professionals/project-funding-and-mortgage-financing/mortgage-loan-insurance/mortgage-loan-insurance-homeownership-programs/cmhc-mortgage-loan-insurance-cost",
}
