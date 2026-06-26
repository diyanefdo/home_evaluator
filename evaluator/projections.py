"""Projection engine for the buy-vs-rent evaluator.

This module turns a flat ``params`` dict of input assumptions into a
``projection`` dict of year/month-by-year numpy arrays that EXACTLY conform to
the schema consumed by ``evaluator.charts`` (see that module's docstring for the
authoritative schema). It also exposes ``compute_summary`` which distils the
projection into a handful of scalar metrics for a CLI executive summary.

This is the financial-modelling layer: amortization, home appreciation, the rent
trajectory, property-tax/maintenance growth, investment compounding (lump + DCA)
and rent-vs-cost crossover detection all happen here. The charting layer does no
modelling of its own.

--------------------------------------------------------------------------
PARAMS the engine reads (generic engine keys; the CLI translates the scraper's
data-keys into these). Defaults are applied for the optional keys.
--------------------------------------------------------------------------
    term_years              : int    -- mortgage term in whole years; drives array lengths.
    purchase_price          : float  -- home purchase price (year-0 home value).
    down_payment            : float  -- down payment dollars (year-0 lump / equity).
    mortgage_rate           : float  -- annual fixed mortgage rate, e.g. 0.044.
    appreciation_rate       : float  -- annual home appreciation, e.g. 0.05.
    property_tax_rate        : float -- annual property tax as a fraction of price.
    property_tax_growth_rate : float -- annual growth of the property-tax bill.
    maintenance_pct_of_value : float -- annual maintenance as a fraction of CURRENT home value.
    rent_monthly            : float  -- starting comparable monthly rent.
    rent_growth_rate        : float  -- annual rent growth.
    investment_return_rate  : float  -- annual nominal return on invested savings (S&P 500).
    insurance_annual        : float  -- annual home insurance dollars (default 1500.0).
    hoa_monthly             : float  -- monthly HOA/condo fee dollars (default 0.0).
    currency_symbol         : str    -- display symbol (default "$"); not used in math.

--------------------------------------------------------------------------
PUBLIC API
--------------------------------------------------------------------------
    build_projection(params: dict) -> dict
    compute_summary(projection: dict, params: dict) -> dict

Modelling notes
---------------
* P&I is FIXED for the whole term at ``mortgage_rate`` -- this is the buyer's
  locked-in advantage. The loan amortizes to ~0 at month ``term_years*12``.
* Maintenance grows with the home: it is recomputed each month off that month's
  appreciated home value (NOT held flat), so it correctly pushes the
  rent-vs-ownership crossover later and shrinks the renter's monthly DCA.
* Property tax grows annually at ``property_tax_growth_rate`` (also not flat).
* Maintenance dollars are folded into the ``cum_insurance_hoa`` bucket (the
  chart's "other carrying costs" layer) so the cost stack is true cash outlay.
* Investment compounding uses the effective monthly rate
  ``(1 + investment_return_rate)**(1/12) - 1`` for both portfolios.
"""

from __future__ import annotations

import numpy as np

from evaluator.charts import _detect_crossover

# --------------------------------------------------------------------------- #
# Defaults for optional params.
# --------------------------------------------------------------------------- #
_DEFAULTS = {
    "insurance_annual": 1500.0,
    "hoa_monthly": 0.0,
    "currency_symbol": "$",
}


def _p(params: dict, key: str) -> float:
    """Fetch a param, applying a default for the optional keys."""
    if key in params:
        return params[key]
    if key in _DEFAULTS:
        return _DEFAULTS[key]
    raise KeyError(f"params is missing required key '{key}'")


def build_projection(params: dict) -> dict:
    """Build the full projection dict from input assumptions.

    See the module docstring for the params read and ``evaluator.charts`` for the
    output schema. All currency arrays are 1-D float numpy arrays in nominal
    whole dollars; year 0 is the moment of purchase.
    """
    T = int(params["term_years"])
    n = T * 12
    months = np.arange(1, n + 1, dtype=float)
    years = np.arange(0, T + 1, dtype=float)

    price = float(params["purchase_price"])
    down = float(params["down_payment"])
    loan0 = price - down

    annual_rate = float(params["mortgage_rate"])
    mrate = annual_rate / 12.0
    appr = float(params["appreciation_rate"])

    invest = float(params["investment_return_rate"])
    mrate_inv = (1 + invest) ** (1 / 12) - 1

    # ---- Amortization (fixed-rate, monthly), then sample to annual ----------
    # Guard against a zero-rate loan (avoids division by zero in the P&I formula).
    if mrate == 0.0:
        pmt = loan0 / n
    else:
        pmt = loan0 * (mrate * (1 + mrate) ** n) / ((1 + mrate) ** n - 1)

    bal = loan0
    m_principal = np.zeros(n)
    m_interest = np.zeros(n)
    m_balance = np.zeros(n)
    for i in range(n):
        intr = bal * mrate
        prin = pmt - intr
        bal = max(0.0, bal - prin)
        m_principal[i] = prin
        m_interest[i] = intr
        m_balance[i] = bal

    # ---- Month-indexed helpers ---------------------------------------------
    # 0-based year that each month falls within: months 1-12 -> year 0, etc.
    year_of_month = (months - 1) // 12

    # Month's appreciated home value. Smooth monthly compounding off the purchase
    # price so maintenance grows month-by-month rather than stepping once a year.
    # Index 0 (first month) == purchase price; it then drifts up each month.
    month_home_value = price * (1 + appr) ** (np.arange(n) / 12.0)

    # ---- Monthly ownership-cost components ---------------------------------
    # Property tax: annual bill grows each year, charged monthly.
    ptg = float(params["property_tax_growth_rate"])
    annual_tax_base = price * float(params["property_tax_rate"])
    m_tax = annual_tax_base * (1 + ptg) ** year_of_month / 12.0

    # Insurance + HOA (held flat in nominal terms; folds into the carrying-cost
    # bucket alongside maintenance).
    insurance_annual = float(_p(params, "insurance_annual"))
    hoa_monthly = float(_p(params, "hoa_monthly"))
    m_ins_hoa = np.full(n, insurance_annual / 12.0 + hoa_monthly)

    # Maintenance: a fraction of THIS MONTH'S appreciated home value, monthly.
    maint_pct = float(params["maintenance_pct_of_value"])
    m_maintenance = maint_pct * month_home_value / 12.0

    # The chart's "other carrying costs" layer = insurance + HOA + maintenance.
    m_other_carry = m_ins_hoa + m_maintenance

    # Total monthly cost of ownership = fixed P&I + tax + insurance + HOA + maint.
    monthly_ownership_cost = pmt + m_tax + m_other_carry

    # ---- Rent trajectory: grows annually, applied monthly ------------------
    rent0 = float(params["rent_monthly"])
    rent_growth = float(params["rent_growth_rate"])
    monthly_rent = rent0 * (1 + rent_growth) ** year_of_month

    # ---- Annual home value / loan balance / equity -------------------------
    home_value = price * (1 + appr) ** years
    loan_balance = np.concatenate(([loan0], m_balance[11::12]))
    equity = home_value - loan_balance

    # ---- Annual cumulative cost components (sampled at each year end) -------
    def _cum_annual(monthly_arr: np.ndarray) -> np.ndarray:
        cum = np.cumsum(monthly_arr)
        return np.concatenate(([0.0], cum[11::12]))

    cum_principal = _cum_annual(m_principal)
    cum_interest = _cum_annual(m_interest)
    cum_property_tax = _cum_annual(m_tax)
    cum_insurance_hoa = _cum_annual(m_other_carry)  # insurance + HOA + maintenance

    # ---- Renter portfolio: down-payment lump + DCA of (cost - rent) --------
    renter_contrib_monthly = np.maximum(0.0, monthly_ownership_cost - monthly_rent)
    bal_r = down
    contrib_r = down
    renter_portfolio = [down]
    renter_contributions = [down]
    for i in range(n):
        bal_r = bal_r * (1 + mrate_inv) + renter_contrib_monthly[i]
        contrib_r += renter_contrib_monthly[i]
        if (i + 1) % 12 == 0:
            renter_portfolio.append(bal_r)
            renter_contributions.append(contrib_r)
    renter_portfolio = np.array(renter_portfolio)
    renter_contributions = np.array(renter_contributions)

    # ---- Owner-advantage portfolio: DCA of (rent - cost) after crossover ---
    owner_contrib_monthly = np.maximum(0.0, monthly_rent - monthly_ownership_cost)
    bal_o = 0.0
    contrib_o = 0.0
    owner_portfolio = [0.0]
    owner_contributions = [0.0]
    for i in range(n):
        bal_o = bal_o * (1 + mrate_inv) + owner_contrib_monthly[i]
        contrib_o += owner_contrib_monthly[i]
        if (i + 1) % 12 == 0:
            owner_portfolio.append(bal_o)
            owner_contributions.append(contrib_o)
    owner_adv_portfolio = np.array(owner_portfolio)
    owner_adv_contributions = np.array(owner_contributions)

    crossover_month = _detect_crossover(monthly_rent, monthly_ownership_cost)

    return {
        "years": years,
        "months": months,
        "home_value": home_value,
        "loan_balance": loan_balance,
        "equity": equity,
        "cum_principal": cum_principal,
        "cum_interest": cum_interest,
        "cum_property_tax": cum_property_tax,
        "cum_insurance_hoa": cum_insurance_hoa,
        "monthly_ownership_cost": monthly_ownership_cost,
        "monthly_rent": monthly_rent,
        "renter_portfolio": renter_portfolio,
        "renter_contributions": renter_contributions,
        "owner_adv_portfolio": owner_adv_portfolio,
        "owner_adv_contributions": owner_adv_contributions,
        "crossover_month": crossover_month,
    }


def compute_summary(projection: dict, params: dict) -> dict:
    """Distil a projection into scalar executive-summary metrics.

    Data only -- no printing, and intentionally NO buy/rent recommendation
    string; the CLI presents the numbers and lets the user decide.

    Net-worth definitions:
        buyer_net_worth  = equity + owner_adv_portfolio
        renter_net_worth = renter_portfolio
    Snapshots are reported at years 10, 15, 20 and T (the term end). When the
    term is shorter than a milestone year, that milestone is omitted.
    """
    T = int(params["term_years"])

    equity = np.asarray(projection["equity"], dtype=float)
    owner_adv = np.asarray(projection["owner_adv_portfolio"], dtype=float)
    renter = np.asarray(projection["renter_portfolio"], dtype=float)
    monthly_rent = np.asarray(projection["monthly_rent"], dtype=float)

    cum_principal = np.asarray(projection["cum_principal"], dtype=float)
    cum_interest = np.asarray(projection["cum_interest"], dtype=float)
    cum_property_tax = np.asarray(projection["cum_property_tax"], dtype=float)
    cum_insurance_hoa = np.asarray(projection["cum_insurance_hoa"], dtype=float)

    down_payment = float(params["down_payment"])

    def _buyer_nw(y: int) -> float:
        return float(equity[y] + owner_adv[y])

    def _renter_nw(y: int) -> float:
        return float(renter[y])

    milestones = [y for y in (10, 15, 20, T) if 0 <= y <= T]
    milestones = sorted(set(milestones))

    buyer_net_worth = {y: _buyer_nw(y) for y in milestones}
    renter_net_worth = {y: _renter_nw(y) for y in milestones}

    crossover_month = projection.get("crossover_month")
    if crossover_month is None:
        crossover_month = _detect_crossover(
            monthly_rent, np.asarray(projection["monthly_ownership_cost"], dtype=float)
        )
    crossover_year = None if crossover_month is None else crossover_month / 12.0

    total_cost_of_ownership = (
        down_payment
        + float(cum_principal[T])
        + float(cum_interest[T])
        + float(cum_property_tax[T])
        + float(cum_insurance_hoa[T])
    )
    total_rent_paid = float(np.sum(monthly_rent))
    total_interest_paid = float(cum_interest[T])

    final_buyer_minus_renter = _buyer_nw(T) - _renter_nw(T)

    return {
        "crossover_year": crossover_year,
        "buyer_net_worth": buyer_net_worth,
        "renter_net_worth": renter_net_worth,
        "total_cost_of_ownership": total_cost_of_ownership,
        "total_rent_paid": total_rent_paid,
        "total_interest_paid": total_interest_paid,
        "final_buyer_minus_renter": final_buyer_minus_renter,
    }


# --------------------------------------------------------------------------- #
# Standalone smoke test against the real M2J / North York scenario.
# Run from the project root:  python -m evaluator.projections
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    smoke_params = {
        "term_years": 30,
        "purchase_price": 1_000_000.0,
        "down_payment": 200_000.0,
        "mortgage_rate": 0.044,
        "appreciation_rate": 0.05,
        "property_tax_rate": 0.007673,
        "property_tax_growth_rate": 0.035,
        "maintenance_pct_of_value": 0.01,
        "insurance_annual": 1_500.0,
        "hoa_monthly": 0.0,
        "rent_monthly": 3_800.0,
        "rent_growth_rate": 0.035,
        "investment_return_rate": 0.10,
        "currency_symbol": "$",
    }
    proj = build_projection(smoke_params)
    summ = compute_summary(proj, smoke_params)

    T = int(smoke_params["term_years"])
    n = T * 12

    # ---- Length assertions --------------------------------------------------
    annual_keys = [
        "years", "home_value", "loan_balance", "equity",
        "cum_principal", "cum_interest", "cum_property_tax", "cum_insurance_hoa",
        "renter_portfolio", "renter_contributions",
        "owner_adv_portfolio", "owner_adv_contributions",
    ]
    for k in annual_keys:
        assert proj[k].shape[0] == T + 1, f"{k} length {proj[k].shape[0]} != {T + 1}"
    for k in ("months", "monthly_ownership_cost", "monthly_rent"):
        assert proj[k].shape[0] == n, f"{k} length {proj[k].shape[0]} != {n}"

    # ---- Amortization sanity: loan fully paid at term end -------------------
    final_balance = float(proj["loan_balance"][-1])
    assert abs(final_balance) < 1.0, f"final loan balance {final_balance} not ~0"

    # ---- Schema anchor checks ----------------------------------------------
    assert abs(proj["home_value"][0] - smoke_params["purchase_price"]) < 1e-6
    assert abs(proj["loan_balance"][0] - 800_000.0) < 1e-6
    assert abs(proj["equity"][0] - smoke_params["down_payment"]) < 1e-6
    assert proj["cum_principal"][0] == 0.0 and proj["cum_interest"][0] == 0.0

    # ---- Calibration: $200k lump @ 10% for 30yr ~ $3.49M --------------------
    lump_only = 200_000.0 * (1.10) ** 30
    assert proj["renter_portfolio"][-1] >= lump_only - 1.0, (
        f"renter portfolio {proj['renter_portfolio'][-1]:.0f} below lump-only {lump_only:.0f}"
    )

    cy = summ["crossover_year"]
    print("Smoke test PASSED")
    print(f"  final loan balance        : {final_balance:.4f}")
    print(f"  lump-only calibration ($) : {lump_only:,.0f}")
    print(f"  crossover_year            : {cy if cy is None else round(cy, 2)}"
          + ("" if cy is None else f"  (month {proj['crossover_month']})"))
    print(f"  year-30 buyer net worth   : ${summ['buyer_net_worth'][30]:,.0f}")
    print(f"  year-30 renter net worth  : ${summ['renter_net_worth'][30]:,.0f}")
    print(f"  buyer - renter @ yr30     : ${summ['final_buyer_minus_renter']:,.0f}")
    print(f"  total cost of ownership   : ${summ['total_cost_of_ownership']:,.0f}")
    print(f"  total rent paid (30yr)    : ${summ['total_rent_paid']:,.0f}")
    print(f"  total interest paid       : ${summ['total_interest_paid']:,.0f}")
