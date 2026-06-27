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

    -- Tax layer (registered-account sheltering + capital-gains tax) --
    current_age             : int    -- investor age now; sets TFSA cumulative room (default 35).
    annual_income           : float  -- gross earned income; sets RRSP room + marginal rate (default 120000).
    account_strategy        : str    -- "shelter-first" (TFSA->RRSP->taxable) or "taxable-only" (default "shelter-first").
    retirement_marginal_rate : float -- rate on RRSP withdrawals + realized cap gains at term end (default 0.30).

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
from evaluator import tax

# --------------------------------------------------------------------------- #
# Defaults for optional params.
# --------------------------------------------------------------------------- #
_DEFAULTS = {
    "insurance_annual": 1500.0,
    "hoa_monthly": 0.0,
    "currency_symbol": "$",
    "current_age": 35,
    "annual_income": 120_000.0,
    "account_strategy": "shelter-first",
    "retirement_marginal_rate": tax.RETIREMENT_MARGINAL_RATE,
}


def _invest_with_accounts(
    initial_lump: float,
    monthly_contrib: np.ndarray,
    n: int,
    mrate_inv: float,
    *,
    strategy: str,
    tfsa_room0: float,
    tfsa_annual: float,
    rrsp_annual: float,
    marginal_rate: float,
) -> dict:
    """Run a monthly investing waterfall across TFSA -> RRSP -> taxable accounts.

    Both the year-0 lump and each month's contribution are poured into available
    registered room first (``shelter-first``) or entirely into a taxable account
    (``taxable-only``). RRSP contributions generate a refund (contribution x
    marginal_rate) which is reinvested the following January through the same
    waterfall. Returns annual snapshots (length ``T+1``) of each sub-account plus
    the taxable-account cost basis (needed for the capital-gains calc later).
    """
    shelter = strategy != "taxable-only"
    tfsa = rrsp = taxable = basis = 0.0
    tfsa_room = float(tfsa_room0)
    rrsp_room = float(rrsp_annual)  # current-year room; no carried-forward backlog
    rrsp_contrib_year = 0.0
    pending_refund = 0.0

    # Mutable cell so the nested helper can update the running balances.
    state = {"tfsa": 0.0, "rrsp": 0.0, "taxable": 0.0, "basis": 0.0,
             "tfsa_room": tfsa_room, "rrsp_room": rrsp_room, "rrsp_year": 0.0}

    def contribute(amount: float) -> None:
        if amount <= 0:
            return
        if shelter:
            to_t = min(amount, state["tfsa_room"])
            state["tfsa"] += to_t
            state["tfsa_room"] -= to_t
            amount -= to_t
            to_r = min(amount, state["rrsp_room"])
            state["rrsp"] += to_r
            state["rrsp_room"] -= to_r
            state["rrsp_year"] += to_r
            amount -= to_r
        state["taxable"] += amount
        state["basis"] += amount

    contribute(initial_lump)
    snap_t = [state["tfsa"]]
    snap_r = [state["rrsp"]]
    snap_x = [state["taxable"]]
    snap_b = [state["basis"]]
    total = [state["tfsa"] + state["rrsp"] + state["taxable"]]

    for i in range(n):
        # Growth first (matches the original bal*(1+r)+contrib convention).
        state["tfsa"] *= 1 + mrate_inv
        state["rrsp"] *= 1 + mrate_inv
        state["taxable"] *= 1 + mrate_inv
        # New calendar year: inject last year's reinvested RRSP refund.
        if i % 12 == 0 and pending_refund:
            contribute(pending_refund)
            pending_refund = 0.0
        contribute(float(monthly_contrib[i]))
        # Year end: top up registered room and bank the refund for next January.
        if (i + 1) % 12 == 0:
            state["tfsa_room"] += tfsa_annual
            state["rrsp_room"] += rrsp_annual
            pending_refund = state["rrsp_year"] * marginal_rate
            state["rrsp_year"] = 0.0
            snap_t.append(state["tfsa"])
            snap_r.append(state["rrsp"])
            snap_x.append(state["taxable"])
            snap_b.append(state["basis"])
            total.append(state["tfsa"] + state["rrsp"] + state["taxable"])

    return {
        "portfolio": np.array(total),
        "tfsa": np.array(snap_t),
        "rrsp": np.array(snap_r),
        "taxable": np.array(snap_x),
        "basis": np.array(snap_b),
    }


def _after_tax_series(acct: dict, *, retirement_rate: float, cg_inclusion: float) -> np.ndarray:
    """After-tax liquidation value of an account waterfall at each year.

    TFSA is tax-free; RRSP is fully taxed as income at ``retirement_rate``;
    the taxable account pays ``cg_inclusion * retirement_rate`` on its gains
    (value minus contributed basis).
    """
    tfsa = np.asarray(acct["tfsa"], dtype=float)
    rrsp = np.asarray(acct["rrsp"], dtype=float)
    taxable = np.asarray(acct["taxable"], dtype=float)
    basis = np.asarray(acct["basis"], dtype=float)
    gains = np.maximum(0.0, taxable - basis)
    cg_tax = gains * cg_inclusion * retirement_rate
    rrsp_tax = rrsp * retirement_rate
    return tfsa + (rrsp - rrsp_tax) + (taxable - cg_tax)


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

    # ---- Tax-layer setup: registered-account room + marginal rates ---------
    age = int(_p(params, "current_age"))
    income = float(_p(params, "annual_income"))
    strategy = str(_p(params, "account_strategy"))
    retirement_rate = float(_p(params, "retirement_marginal_rate"))
    marg_rate = tax.marginal_rate(income)
    tfsa_room0 = tax.tfsa_cumulative_room(age)
    tfsa_annual = tax.tfsa_annual_limit()
    rrsp_annual = tax.rrsp_annual_room(income)
    cg_inclusion = tax.CAPITAL_GAINS_INCLUSION

    def _run_accounts(initial_lump, monthly_contrib):
        return _invest_with_accounts(
            initial_lump, monthly_contrib, n, mrate_inv,
            strategy=strategy, tfsa_room0=tfsa_room0, tfsa_annual=tfsa_annual,
            rrsp_annual=rrsp_annual, marginal_rate=marg_rate,
        )

    # Both scenarios are the same person in alternate worlds, so each starts with
    # identical registered room. The renter pours the down-payment lump + monthly
    # (cost - rent) surplus in; the owner pours only the post-crossover
    # (rent - cost) surplus in (so more of their room stays available -> more of
    # the owner's side savings end up sheltered).
    renter_contrib_monthly = np.maximum(0.0, monthly_ownership_cost - monthly_rent)
    owner_contrib_monthly = np.maximum(0.0, monthly_rent - monthly_ownership_cost)

    # ---- Transaction costs: purchase land-transfer tax + sale commission ----
    # Buying costs the buyer one-time closing costs (land-transfer tax + legal),
    # which are sunk. In the rent scenario that same cash is free to invest, so
    # it's added to the renter's year-0 lump (keeps the cash flows matched).
    include_tx = bool(params.get("include_transaction_costs", True))
    purchase_costs = float(params.get("purchase_closing_costs", 0.0)) if include_tx else 0.0
    commission_rate = float(params.get("commission_rate", 0.0)) if include_tx else 0.0
    hst_rate = float(params.get("hst_rate", 0.0))
    sale_legal = float(params.get("sale_legal_fee", 0.0)) if include_tx else 0.0

    renter_acct = _run_accounts(down + purchase_costs, renter_contrib_monthly)
    owner_acct = _run_accounts(0.0, owner_contrib_monthly)

    # Selling costs the owner would pay to realize their equity each year
    # (realtor commission + HST on it + legal). Netted off equity in the
    # after-tax net-worth comparison only (Chart 1's gross equity is untouched).
    selling_costs = home_value * commission_rate * (1 + hst_rate) + sale_legal

    renter_portfolio = renter_acct["portfolio"]          # pre-tax (charts 3/5)
    owner_adv_portfolio = owner_acct["portfolio"]         # pre-tax (charts 4/5)

    # Cumulative contributions (principal only), incl. reinvested RRSP refunds.
    renter_contributions = np.concatenate(
        ([down], down + np.cumsum(renter_contrib_monthly)[11::12])
    )
    owner_adv_contributions = np.concatenate(
        ([0.0], np.cumsum(owner_contrib_monthly)[11::12])
    )

    # After-tax net worth. The home is a principal residence -> its equity is
    # capital-gains-tax-FREE, so the buyer keeps full equity; only the side
    # investments are taxed.
    renter_after_tax = _after_tax_series(
        renter_acct, retirement_rate=retirement_rate, cg_inclusion=cg_inclusion
    )
    owner_inv_after_tax = _after_tax_series(
        owner_acct, retirement_rate=retirement_rate, cg_inclusion=cg_inclusion
    )
    # Buyer net worth = home equity NET of selling costs (home gain itself is
    # principal-residence tax-free) + after-tax side investments.
    buyer_net_worth_after_tax = (equity - selling_costs) + owner_inv_after_tax
    renter_net_worth_after_tax = renter_after_tax

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
        # ---- Tax layer (after-tax net worth + account breakdown) ----
        "buyer_net_worth_after_tax": buyer_net_worth_after_tax,
        "renter_net_worth_after_tax": renter_net_worth_after_tax,
        "renter_accounts": renter_acct,
        "owner_accounts": owner_acct,
        "owner_inv_after_tax": owner_inv_after_tax,
        "renter_inv_after_tax": renter_after_tax,
        "selling_costs": selling_costs,
        "purchase_closing_costs": purchase_costs,
        "tax_meta": {
            "marginal_rate": marg_rate,
            "retirement_rate": retirement_rate,
            "cg_inclusion": cg_inclusion,
            "account_strategy": strategy,
            "tfsa_room0": tfsa_room0,
            "rrsp_annual": rrsp_annual,
            "current_age": age,
            "annual_income": income,
        },
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

    # After-tax net worth (the headline comparison): falls back to the pre-tax
    # equity+portfolio definition if the tax-layer arrays are absent.
    buyer_at = projection.get("buyer_net_worth_after_tax")
    renter_at = projection.get("renter_net_worth_after_tax")
    if buyer_at is None or renter_at is None:
        buyer_at = equity + owner_adv
        renter_at = renter
    buyer_at = np.asarray(buyer_at, dtype=float)
    renter_at = np.asarray(renter_at, dtype=float)

    down_payment = float(params["down_payment"])

    def _buyer_nw(y: int) -> float:
        return float(buyer_at[y])

    def _renter_nw(y: int) -> float:
        return float(renter_at[y])

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

    purchase_costs = float(projection.get("purchase_closing_costs", 0.0))
    selling_costs_arr = projection.get("selling_costs")
    selling_costs_final = (
        float(np.asarray(selling_costs_arr)[T]) if selling_costs_arr is not None else 0.0
    )

    total_cost_of_ownership = (
        down_payment
        + purchase_costs
        + float(cum_principal[T])
        + float(cum_interest[T])
        + float(cum_property_tax[T])
        + float(cum_insurance_hoa[T])
        + selling_costs_final
    )
    total_rent_paid = float(np.sum(monthly_rent))
    total_interest_paid = float(cum_interest[T])

    final_buyer_minus_renter = _buyer_nw(T) - _renter_nw(T)

    # Investment tax drag at term end (NOT transaction costs): pre-tax portfolio
    # minus after-tax portfolio. The renter has only investments; the buyer's is
    # the tax on side investments (home gain is exempt, so it's usually ~0).
    renter_inv_at = projection.get("renter_inv_after_tax")
    owner_inv_at = projection.get("owner_inv_after_tax")
    renter_inv_at_T = float(np.asarray(renter_inv_at)[T]) if renter_inv_at is not None else _renter_nw(T)
    owner_inv_at_T = float(np.asarray(owner_inv_at)[T]) if owner_inv_at is not None else 0.0
    renter_pretax_T = float(renter[T])
    buyer_pretax_T = float(equity[T] + owner_adv[T])
    renter_tax_paid = renter_pretax_T - renter_inv_at_T
    buyer_tax_paid = float(owner_adv[T]) - owner_inv_at_T

    tax_meta = projection.get("tax_meta", {})
    renter_acct = projection.get("renter_accounts", {})

    def _final(arr_key: str) -> float:
        arr = renter_acct.get(arr_key)
        return float(np.asarray(arr)[-1]) if arr is not None else 0.0

    return {
        "crossover_year": crossover_year,
        "buyer_net_worth": buyer_net_worth,
        "renter_net_worth": renter_net_worth,
        "total_cost_of_ownership": total_cost_of_ownership,
        "total_rent_paid": total_rent_paid,
        "total_interest_paid": total_interest_paid,
        "final_buyer_minus_renter": final_buyer_minus_renter,
        # ---- Tax layer ----
        "after_tax": ("buyer_net_worth_after_tax" in projection),
        "renter_tax_paid": renter_tax_paid,
        "buyer_tax_paid": buyer_tax_paid,
        "renter_pretax_final": renter_pretax_T,
        "buyer_pretax_final": buyer_pretax_T,
        "account_strategy": tax_meta.get("account_strategy"),
        "marginal_rate": tax_meta.get("marginal_rate"),
        "retirement_rate": tax_meta.get("retirement_rate"),
        "renter_final_tfsa": _final("tfsa"),
        "renter_final_rrsp": _final("rrsp"),
        "renter_final_taxable": _final("taxable"),
        # ---- Transaction costs ----
        "purchase_closing_costs": purchase_costs,
        "selling_costs_final": selling_costs_final,
    }


def build_sensitivity(
    params: dict,
    appreciation_values: list | None = None,
    return_values: list | None = None,
) -> dict:
    """Re-run the model across a grid of home-appreciation x investment-return.

    The buy-vs-rent result is dominated by these two assumptions (the owner runs
    ~5:1 leverage on an appreciating asset), so a point estimate is fragile. This
    returns a 2-D grid of the year-T **after-tax** net-worth gap (buyer - renter;
    positive = buying wins) so the sensitivity is visible at a glance.

    All other params (price, down, tax, transaction costs, ...) are held at the
    caller's values; only appreciation and investment return are swept.
    """
    appr_vals = list(appreciation_values) if appreciation_values is not None else [
        0.03, 0.04, 0.05, 0.06, 0.07, 0.08
    ]
    ret_vals = list(return_values) if return_values is not None else [
        0.06, 0.07, 0.08, 0.09, 0.10, 0.11
    ]

    gap_grid = np.zeros((len(appr_vals), len(ret_vals)), dtype=float)
    for i, a in enumerate(appr_vals):
        for j, r in enumerate(ret_vals):
            p = dict(params)
            p["appreciation_rate"] = a
            p["investment_return_rate"] = r
            proj = build_projection(p)
            summ = compute_summary(proj, p)
            gap_grid[i, j] = summ["final_buyer_minus_renter"]

    return {
        "appreciation_values": np.array(appr_vals, dtype=float),
        "return_values": np.array(ret_vals, dtype=float),
        "gap_grid": gap_grid,  # buyer - renter (after tax); >0 => buying wins
        "base_appreciation": float(params.get("appreciation_rate", float("nan"))),
        "base_return": float(params.get("investment_return_rate", float("nan"))),
        "after_tax": True,  # the tax layer always runs in build_projection
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
