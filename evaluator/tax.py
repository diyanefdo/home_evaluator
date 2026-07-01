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

import math

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

# Land-transfer / property-transfer tax is PROVINCIAL (a couple of cities add a
# municipal layer). Each schedule below is a list of (lower_threshold, rate)
# marginal brackets on the purchase price. Provinces with no LTT (AB, SK) instead
# charge small flat land-title registration fees, handled separately.

# Ontario provincial Land Transfer Tax.
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
# British Columbia Property Transfer Tax: 1% first $200k, 2% to $2M, 3% to $3M,
# +2% (=5%) on the residential portion above $3M.
_BC_PTT = [(0.0, 0.01), (200_000.0, 0.02), (2_000_000.0, 0.03), (3_000_000.0, 0.05)]
# Manitoba Land Transfer Tax: nil to $30k, then 0.5 / 1.0 / 1.5 / 2.0%.
_MB_LTT = [(0.0, 0.0), (30_000.0, 0.005), (90_000.0, 0.01), (150_000.0, 0.015), (200_000.0, 0.02)]
# Quebec transfer duties ("welcome tax" / droits de mutation) — 2024 base brackets
# (indexed annually). Municipalities may set higher tiers above $500k (see Montreal).
_QC_MUTATION = [(0.0, 0.005), (58_900.0, 0.01), (294_600.0, 0.015)]
# Ville de Montreal's own 2024 schedule adds luxury tiers on top of the base.
_QC_MONTREAL = [
    (0.0, 0.005), (58_900.0, 0.01), (294_600.0, 0.015), (552_300.0, 0.02),
    (1_104_700.0, 0.025), (2_136_500.0, 0.03), (3_113_000.0, 0.035), (4_144_500.0, 0.04),
]
# Nova Scotia deed-transfer tax is municipal; Halifax (HRM) is 1.5% (dominant CMA).
_NS_DEED = [(0.0, 0.015)]
# New Brunswick real-property transfer tax: 1% of the greater of price/assessment.
_NB_TRANSFER = [(0.0, 0.01)]
# PEI real-property transfer tax: 1% (first-time / low-value exemptions ignored).
_PE_TRANSFER = [(0.0, 0.01)]
# Newfoundland & Labrador registration of deeds: ~$100 + 0.4% over $500 (approx).
_NL_REGISTRATION = [(0.0, 0.004)]
# Saskatchewan has no LTT — only a Land Titles fee of 0.3% of value (>$8,400).
_SK_TITLE = [(0.0, 0.003)]

# region key -> its marginal LTT schedule. "toronto" and "alberta" are special-cased.
_LTT_SCHEDULES = {
    "ontario": _ON_LTT,
    "bc": _BC_PTT,
    "manitoba": _MB_LTT,
    "quebec": _QC_MUTATION,
    "quebec_montreal": _QC_MONTREAL,
    "nova_scotia": _NS_DEED,
    "new_brunswick": _NB_TRANSFER,
    "pei": _PE_TRANSFER,
    "newfoundland": _NL_REGISTRATION,
    "saskatchewan": _SK_TITLE,
}

ON_FIRST_TIME_REBATE = 4_000.0        # max Ontario first-time-buyer LTT rebate
TORONTO_FIRST_TIME_REBATE = 4_475.0   # max Toronto first-time-buyer MLTT rebate
# BC first-time-buyer exemption: full below $835k FMV (exemption = tax on the
# first $500k), phasing out to $860k. Modelled as full-below-threshold.
BC_FTB_FULL_THRESHOLD = 835_000.0
BC_FTB_EXEMPT_PORTION = 500_000.0


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


def _alberta_title_fee(price: float) -> float:
    """Alberta has no LTT — only a Land Titles registration fee.

    The property-transfer registration fee is $50 + $5 per $5,000 of value
    (rounded up). The separate mortgage-registration fee (a similar charge on the
    loan) is not modelled here, so this is a slight under-estimate for financed
    purchases — but it is dollars, not the ~1-2% a real LTT would cost.
    """
    return 50.0 + 5.0 * math.ceil(float(price) / 5_000.0)


def _first_time_rebate(price: float, region: str) -> float:
    """First-time-buyer land-transfer rebate/exemption for a region (0 if none)."""
    if region in ("ontario", "toronto"):
        rebate = ON_FIRST_TIME_REBATE
        if region == "toronto":
            rebate += TORONTO_FIRST_TIME_REBATE
        return rebate
    if region == "bc" and price <= BC_FTB_FULL_THRESHOLD:
        # Exemption equals the PTT payable on the first $500k of value.
        return _bracketed_tax(min(price, BC_FTB_EXEMPT_PORTION), _BC_PTT)
    return 0.0


def land_transfer_tax(price: float, region: str = "ontario", first_time: bool = False) -> float:
    """Total land-transfer / property-transfer tax for a region.

    ``region`` is a province/municipality key (see ``_LTT_SCHEDULES``):
    ``"toronto"`` = Ontario provincial + Toronto municipal LTT; ``"alberta"`` /
    ``"saskatchewan"`` have no LTT (nominal title fees); ``"quebec_montreal"`` adds
    Montreal's luxury tiers. Unknown regions fall back to the Ontario schedule.
    """
    price = float(price)
    if region == "alberta":
        return _alberta_title_fee(price)   # no LTT; no FTB rebate
    if region == "toronto":
        total = ontario_ltt(price) + toronto_mltt(price)
    else:
        total = _bracketed_tax(price, _LTT_SCHEDULES.get(region, _ON_LTT))
    rebate = _first_time_rebate(price, region) if first_time else 0.0
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
# Provincial sales tax charged on the CMHC premium (paid up front, not financed).
# Only ON, MB, QC, and SK levy it: ON/MB 8%, QC 9.975%, SK 6%. BC, AB, and the
# HST/no-PST provinces charge nothing on the premium (default 0).
_CMHC_PST_ON_PREMIUM = {
    "ontario": 0.08, "toronto": 0.08, "manitoba": 0.08,
    "quebec": 0.09975, "quebec_montreal": 0.09975, "saskatchewan": 0.06,
}


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
    "ltt_ontario": "https://www.ontario.ca/document/land-transfer-tax ; Toronto MLTT https://www.toronto.ca/services-payments/property-taxes-utilities/municipal-land-transfer-tax-mltt/",
    "ltt_bc": "BC Property Transfer Tax https://www2.gov.bc.ca/gov/content/taxes/property-taxes/property-transfer-tax (1/2/3/5% bands; FTB exemption <$835k)",
    "ltt_quebec": "Quebec transfer duties ('welcome tax') https://www.revenuquebec.ca/ ; Ville de Montreal droits de mutation by-law (luxury tiers, indexed 2024)",
    "ltt_manitoba": "Manitoba Land Transfer Tax https://www.gov.mb.ca/finance/taxation/taxes/land.html (nil<$30k, 0.5/1.0/1.5/2.0%)",
    "ltt_atlantic": "NS deed transfer (HRM 1.5%), NB 1%, PEI 1%, NL registration ~0.4% — municipal/provincial rate tables",
    "ltt_no_ltt": "Alberta (land-title registration fee $50 + $5/$5,000) and Saskatchewan (0.3% title fee) levy no land-transfer tax",
}
