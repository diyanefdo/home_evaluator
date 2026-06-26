"""Charting layer for the buy-vs-rent evaluator.

This module renders the five financial-comparison charts from a pre-computed
``projection`` dict (built by the upstream projection module) and a ``params``
dict of input assumptions. It is a pure rendering layer: it performs no
financial modelling of its own beyond trivial derived quantities (e.g. summing
two cost arrays). All heavy lifting -- amortization, rent trajectory,
investment compounding, crossover detection -- happens upstream and is handed
in via ``projection``.

--------------------------------------------------------------------------
PROJECTION DICT SCHEMA (the upstream projection module MUST conform to this)
--------------------------------------------------------------------------

Conventions
-----------
- ``T``  = ``params["term_years"]`` (mortgage term in whole years).
- Annual arrays have length ``T + 1`` and are indexed by year 0..T, where
  index 0 is "today" (the moment of purchase) and index T is the end of the
  mortgage term. Year-0 values are the starting state (e.g. full loan balance,
  zero cumulative interest paid).
- Monthly arrays have length ``T * 12`` and are indexed by month 1..T*12
  (array index 0 == the first month after purchase). Monthly arrays are used
  only for fine-grained series (mortgage vs rent crossover, monthly DCA).
- All currency values are in **nominal dollars** (not inflation-adjusted)
  unless a key name says otherwise. Units are whole dollars (floats are fine).
- All arrays are 1-D ``numpy.ndarray`` of dtype float.

Required keys
-------------
years : float ndarray, shape (T+1,)
    The x-axis for all annual charts: [0, 1, 2, ..., T].

months : float ndarray, shape (T*12,)
    Month index for monthly series: [1, 2, ..., T*12]. Used internally; charts
    plot monthly series against ``months / 12.0`` so every chart shares a
    "years" x-axis.

# ---- Chart 1: house value / loan / equity (annual) --------------------------
home_value : float ndarray, shape (T+1,)
    Projected market value of the home at each year end, appreciation applied.
    Index 0 == purchase price.
loan_balance : float ndarray, shape (T+1,)
    Remaining mortgage principal at each year end. Index 0 == initial loan
    amount (purchase_price - down_payment). Index T should be ~0.
equity : float ndarray, shape (T+1,)
    Owner equity = home_value - loan_balance at each year end. Index 0 ==
    down_payment.

# ---- Chart 2: cumulative ownership cost components (annual) ------------------
cum_principal : float ndarray, shape (T+1,)
    Cumulative principal paid through end of each year. Index 0 == 0.
cum_interest : float ndarray, shape (T+1,)
    Cumulative mortgage interest paid through end of each year. Index 0 == 0.
cum_property_tax : float ndarray, shape (T+1,)
    Cumulative property tax paid. Index 0 == 0.
cum_insurance_hoa : float ndarray, shape (T+1,)
    Cumulative "other carrying costs" = home insurance + HOA + maintenance,
    paid through end of each year. Index 0 == 0. (Maintenance is folded in here
    by the projection engine so the cost stack reflects true cash outlay.)
    Note: the down-payment lump and closing costs are NOT included here; the
    down payment is drawn separately at year 0 from ``params``/derived values.

# ---- Charts 3 & 4: monthly cost comparison ----------------------------------
monthly_ownership_cost : float ndarray, shape (T*12,)
    Total monthly cost of ownership each month = P&I + property tax + insurance
    + HOA (+ PMI if modelled upstream). Used to compare against rent.
monthly_rent : float ndarray, shape (T*12,)
    Projected monthly rent each month (rent growth compounded). Used to compare
    against ownership cost.

# ---- Chart 3: renter scenario investment portfolio (annual) -----------------
renter_portfolio : float ndarray, shape (T+1,)
    Total portfolio value of the renter scenario at each year end. Built
    upstream from: down payment (+ closing costs, if modelled) invested at
    year 0, plus monthly DCA of MAX(0, ownership_cost - rent), compounded at
    the investment return rate. Index 0 == initial lump sum invested.
renter_contributions : float ndarray, shape (T+1,)
    Cumulative dollars contributed (principal only, no returns) in the renter
    scenario. Index 0 == initial lump sum.
    The "returns" series is derived in-chart as portfolio - contributions.

# ---- Chart 4: homeowner-advantage investment portfolio (annual) -------------
owner_adv_portfolio : float ndarray, shape (T+1,)
    Total portfolio value from investing MAX(0, rent - ownership_cost) each
    month (only positive after the crossover), compounded at the investment
    return rate. Index 0 == 0. If rent never exceeds ownership cost, this is
    all zeros.
owner_adv_contributions : float ndarray, shape (T+1,)
    Cumulative contributions for the homeowner-advantage scenario. Index 0 == 0.

Optional keys
-------------
crossover_month : int or None
    1-based month index at which monthly_rent first exceeds
    monthly_ownership_cost, or None if it never does. If omitted/None, the
    charts derive it from the monthly arrays.

--------------------------------------------------------------------------
PARAMS DICT (assumptions; only the keys read here are required)
--------------------------------------------------------------------------
term_years : int                 -- mortgage term, drives array lengths.
down_payment : float             -- down payment dollars (year-0 lump in chart 2).
purchase_price : float           -- used for context/annotations (optional).
closing_costs : float            -- optional; if present and the renter scenario
                                    invested it, included in the year-0 lump note.
investment_return_rate : float   -- annual rate (e.g. 0.07); annotation only.
currency_symbol : str            -- optional, defaults to "$".

Any additional params keys are ignored by this module.

--------------------------------------------------------------------------
ENTRY POINT
--------------------------------------------------------------------------
    generate_charts(projection, params, out_dir) -> list[str]
returns the list of 5 saved PNG file paths in chart order [1, 2, 3, 4, 5].
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")  # non-interactive backend: savefig only, no display.
# Treat '$' literally in all text; otherwise matplotlib reads a pair of dollar
# signs (e.g. "$4.4M vs $5.9M") as LaTeX math mode and mangles the string.
matplotlib.rcParams["text.parse_math"] = False

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _money_formatter(symbol: str = "$") -> mticker.FuncFormatter:
    """Return a tick formatter rendering dollars as $X, $Xk, or $X.XM."""

    def _fmt(value: float, _pos=None) -> str:
        sign = "-" if value < 0 else ""
        v = abs(value)
        if v >= 1_000_000:
            return f"{sign}{symbol}{v / 1_000_000:.2f}M"
        if v >= 1_000:
            return f"{sign}{symbol}{v / 1_000:.0f}k"
        return f"{sign}{symbol}{v:.0f}"

    return mticker.FuncFormatter(_fmt)


def _money_str(value: float, symbol: str = "$") -> str:
    """Compact one-off money string for annotations."""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000:
        return f"{sign}{symbol}{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{sign}{symbol}{v / 1_000:.1f}k"
    return f"{sign}{symbol}{v:,.0f}"


def _style_axes(ax, symbol: str) -> None:
    """Apply shared styling (grid + money y-axis) to an axes."""
    ax.yaxis.set_major_formatter(_money_formatter(symbol))
    ax.grid(True, which="major", linestyle="--", linewidth=0.6, alpha=0.5)
    ax.set_axisbelow(True)


def _require(projection: dict, key: str, expected_len: int) -> np.ndarray:
    """Fetch a required projection array and validate its length."""
    if key not in projection:
        raise KeyError(f"projection is missing required key '{key}'")
    arr = np.asarray(projection[key], dtype=float)
    if arr.shape[0] != expected_len:
        raise ValueError(
            f"projection['{key}'] has length {arr.shape[0]}, expected {expected_len}"
        )
    return arr


def _detect_crossover(monthly_rent: np.ndarray, monthly_cost: np.ndarray) -> int | None:
    """Return 1-based month where rent first exceeds ownership cost, else None."""
    mask = monthly_rent > monthly_cost
    idx = np.argmax(mask)
    if mask[idx]:
        return int(idx) + 1  # convert 0-based index to 1-based month
    return None


# --------------------------------------------------------------------------- #
# Chart 1: House value, loan balance, equity
# --------------------------------------------------------------------------- #
def _chart_house_value(projection, params, out_dir, symbol) -> str:
    T = int(params["term_years"])
    years = _require(projection, "years", T + 1)
    home_value = _require(projection, "home_value", T + 1)
    loan_balance = _require(projection, "loan_balance", T + 1)
    equity = _require(projection, "equity", T + 1)

    fig, ax = plt.subplots(figsize=(11, 6.5))

    ax.plot(years, home_value, color="#1f77b4", lw=2.2, label="Home market value")
    ax.plot(years, loan_balance, color="#d62728", lw=2.2, label="Remaining mortgage balance")
    ax.plot(years, equity, color="#2ca02c", lw=2.2, label="Owner equity")
    ax.fill_between(years, equity, 0, color="#2ca02c", alpha=0.08)

    # Equity milestones expressed as % of the home being "owned" (equity/value).
    # We annotate the first year equity reaches 25/50/75/100% of the home value,
    # plus the year the loan is fully paid (balance ~ 0).
    initial_loan = loan_balance[0] if loan_balance[0] > 0 else np.nan
    for pct in (0.25, 0.50, 0.75, 1.00):
        # "% paid off" measured against the original loan principal.
        paid_off = (initial_loan - loan_balance) / initial_loan if initial_loan else np.zeros_like(loan_balance)
        reached = np.argmax(paid_off >= pct - 1e-9)
        if paid_off[reached] >= pct - 1e-9 and not (pct == 0.25 and reached == 0):
            yx = years[reached]
            ey = equity[reached]
            ax.scatter([yx], [ey], color="#2ca02c", zorder=5, s=36)
            ax.annotate(
                f"{int(pct * 100)}% loan paid\nyr {yx:.0f}",
                xy=(yx, ey),
                xytext=(0, 14),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color="#1a5c1a",
                arrowprops=dict(arrowstyle="-", color="#2ca02c", lw=0.7),
            )

    ax.set_title("Chart 1 — Home Value, Mortgage Balance & Equity Over Time", fontsize=13, fontweight="bold")
    ax.set_xlabel("Year")
    ax.set_ylabel("Value")
    ax.set_xlim(0, T)
    ax.set_ylim(bottom=0)
    _style_axes(ax, symbol)
    ax.legend(loc="upper left", framealpha=0.9)

    path = os.path.join(out_dir, "chart1_house_value.png")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# Chart 2: Cumulative cost of ownership breakdown
# --------------------------------------------------------------------------- #
def _chart_cost_breakdown(projection, params, out_dir, symbol) -> str:
    T = int(params["term_years"])
    years = _require(projection, "years", T + 1)
    cum_principal = _require(projection, "cum_principal", T + 1)
    cum_interest = _require(projection, "cum_interest", T + 1)
    cum_tax = _require(projection, "cum_property_tax", T + 1)
    cum_ins_hoa = _require(projection, "cum_insurance_hoa", T + 1)
    down_payment = float(params.get("down_payment", 0.0))

    fig, ax = plt.subplots(figsize=(11, 6.5))

    # Down payment is a year-0 lump that sits beneath every other component so
    # the stack reflects true cumulative cash outlay from day one.
    dp_layer = np.full_like(years, down_payment)

    layers = [
        (dp_layer, "Down payment (yr 0 lump)", "#7f7f7f"),
        (cum_principal, "Cumulative principal", "#2ca02c"),
        (cum_interest, "Cumulative interest", "#d62728"),
        (cum_tax, "Cumulative property tax", "#ff7f0e"),
        (cum_ins_hoa, "Cumulative insurance + HOA + maintenance", "#9467bd"),
    ]
    stack = ax.stackplot(
        years,
        *[layer for layer, _, _ in layers],
        labels=[lbl for _, lbl, _ in layers],
        colors=[c for _, _, c in layers],
        alpha=0.9,
    )

    total = dp_layer + cum_principal + cum_interest + cum_tax + cum_ins_hoa

    # Total-cost-of-ownership annotations at key milestones.
    milestones = [y for y in (5, 10, 15, T) if y <= T]
    milestones = sorted(set(milestones))
    for y in milestones:
        tot = total[y]
        ax.scatter([y], [tot], color="black", zorder=6, s=22)
        ax.annotate(
            f"yr {y}: {_money_str(tot, symbol)}",
            xy=(y, tot),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=8.5,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="black", alpha=0.85, lw=0.6),
        )

    ax.set_title("Chart 2 — Cumulative Cost of Ownership Breakdown", fontsize=13, fontweight="bold")
    ax.set_xlabel("Year")
    ax.set_ylabel("Cumulative dollars paid")
    ax.set_xlim(0, T)
    ax.set_ylim(bottom=0)
    _style_axes(ax, symbol)
    ax.legend(loc="upper left", framealpha=0.9)

    path = os.path.join(out_dir, "chart2_cost_breakdown.png")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# Chart 3: Renter scenario investment growth
# --------------------------------------------------------------------------- #
def _chart_renter_investment(projection, params, out_dir, symbol) -> str:
    T = int(params["term_years"])
    years = _require(projection, "years", T + 1)
    portfolio = _require(projection, "renter_portfolio", T + 1)
    contributions = _require(projection, "renter_contributions", T + 1)
    returns = portfolio - contributions  # derived growth component

    fig, ax = plt.subplots(figsize=(11, 6.5))

    ax.plot(years, portfolio, color="#1f77b4", lw=2.4, label="Total portfolio value")
    ax.plot(years, contributions, color="#ff7f0e", lw=2.0, ls="--", label="Cumulative contributions")
    ax.fill_between(years, contributions, portfolio, color="#1f77b4", alpha=0.12, label="Investment returns")

    final_val = portfolio[-1]
    ret_rate = params.get("investment_return_rate")
    rate_note = f" @ {ret_rate * 100:.1f}%/yr" if isinstance(ret_rate, (int, float)) else ""
    ax.scatter([years[-1]], [final_val], color="#1f77b4", zorder=6, s=40)
    ax.annotate(
        f"Final portfolio: {_money_str(final_val, symbol)}{rate_note}",
        xy=(years[-1], final_val),
        xytext=(-12, 12),
        textcoords="offset points",
        ha="right",
        fontsize=9,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#1f77b4", alpha=0.9),
    )

    ax.set_title(
        "Chart 3 — Renter Scenario: Invest Down Payment + Monthly Cost Savings",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Year")
    ax.set_ylabel("Portfolio value")
    ax.set_xlim(0, T)
    ax.set_ylim(bottom=0)
    _style_axes(ax, symbol)
    ax.legend(loc="upper left", framealpha=0.9)
    fig.text(
        0.012,
        0.012,
        "Renter invests the down payment up front, then each month invests MAX(0, ownership cost - rent).",
        fontsize=7.5,
        color="#555555",
    )

    path = os.path.join(out_dir, "chart3_renter_investment.png")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# Chart 4: Homeowner-advantage investment growth
# --------------------------------------------------------------------------- #
def _chart_owner_advantage(projection, params, out_dir, symbol) -> str:
    T = int(params["term_years"])
    years = _require(projection, "years", T + 1)
    portfolio = _require(projection, "owner_adv_portfolio", T + 1)
    contributions = _require(projection, "owner_adv_contributions", T + 1)
    monthly_rent = _require(projection, "monthly_rent", T * 12)
    monthly_cost = _require(projection, "monthly_ownership_cost", T * 12)

    crossover_month = projection.get("crossover_month")
    if crossover_month is None:
        crossover_month = _detect_crossover(monthly_rent, monthly_cost)

    fig, ax = plt.subplots(figsize=(11, 6.5))

    never_crosses = crossover_month is None or float(portfolio[-1]) <= 0.0

    if never_crosses:
        # Edge case: rent never overtakes ownership cost -> flat zero line + note.
        ax.plot(years, np.zeros_like(years), color="#8c564b", lw=2.2, label="Owner-advantage portfolio ($0)")
        ax.set_ylim(0, 1)
        ax.set_yticks([0])  # avoid misleading fractional-dollar ticks on an empty axis
        ax.text(
            T / 2.0,
            0.5,
            "With the given rent-growth rate, rent does not exceed total\n"
            "mortgage/ownership costs during the term, so the homeowner has\n"
            "no monthly cost advantage to invest. This chart would become\n"
            "relevant if rent growth accelerates or interest rates rise.",
            ha="center",
            va="center",
            fontsize=10,
            color="#8c564b",
            bbox=dict(boxstyle="round,pad=0.5", fc="#fdf3ef", ec="#8c564b", alpha=0.9),
        )
    else:
        ax.plot(years, portfolio, color="#8c564b", lw=2.4, label="Owner-advantage portfolio value")
        ax.plot(years, contributions, color="#ff7f0e", lw=2.0, ls="--", label="Cumulative contributions")
        ax.fill_between(years, contributions, portfolio, color="#8c564b", alpha=0.12, label="Investment returns")

        crossover_year = crossover_month / 12.0
        ax.axvline(crossover_year, color="#444444", ls=":", lw=1.6)
        ax.annotate(
            f"Crossover — rent first exceeds\nownership cost (yr {crossover_year:.1f})",
            xy=(crossover_year, 0),
            xytext=(8, 40),
            textcoords="offset points",
            fontsize=8.5,
            color="#222222",
            arrowprops=dict(arrowstyle="->", color="#444444", lw=0.9),
        )

        final_val = portfolio[-1]
        ax.scatter([years[-1]], [final_val], color="#8c564b", zorder=6, s=40)
        ax.annotate(
            f"Final portfolio: {_money_str(final_val, symbol)}",
            xy=(years[-1], final_val),
            xytext=(-12, 12),
            textcoords="offset points",
            ha="right",
            fontsize=9,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#8c564b", alpha=0.9),
        )
        ax.set_ylim(bottom=0)

    ax.set_title(
        "Chart 4 — Homeowner Advantage: Invest Rent-Minus-Mortgage After Crossover",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Year")
    ax.set_ylabel("Portfolio value")
    ax.set_xlim(0, T)
    _style_axes(ax, symbol)
    ax.legend(loc="upper left", framealpha=0.9)
    fig.text(
        0.012,
        0.012,
        "Additional wealth a homeowner can build by investing their monthly cost advantage "
        "once a fixed mortgage becomes cheaper than rising rent.",
        fontsize=7.5,
        color="#555555",
    )

    path = os.path.join(out_dir, "chart4_owner_advantage.png")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# Chart 5: Total net worth — homeowner vs renter
# --------------------------------------------------------------------------- #
def _chart_networth_comparison(projection, params, out_dir, symbol) -> str:
    """Head-to-head total net worth of the two scenarios over the term.

    Homeowner net worth = home equity + owner-advantage portfolio.
    Renter net worth    = renter portfolio (down payment + invested savings).
    The two scenarios spend the same amount every month, so this is a fair
    apples-to-apples wealth comparison.
    """
    T = int(params["term_years"])
    years = _require(projection, "years", T + 1)
    equity = _require(projection, "equity", T + 1)
    owner_adv = _require(projection, "owner_adv_portfolio", T + 1)
    renter = _require(projection, "renter_portfolio", T + 1)

    owner_nw = equity + owner_adv

    fig, ax = plt.subplots(figsize=(11, 6.5))

    ax.plot(years, owner_nw, color="#1f77b4", lw=2.4, label="Homeowner net worth (equity + investments)")
    ax.plot(years, renter, color="#ff7f0e", lw=2.4, label="Renter net worth (portfolio)")

    # Shade who is ahead: green where the owner leads, orange where the renter does.
    ax.fill_between(years, owner_nw, renter, where=(owner_nw >= renter),
                    interpolate=True, color="#1f77b4", alpha=0.10)
    ax.fill_between(years, owner_nw, renter, where=(owner_nw < renter),
                    interpolate=True, color="#ff7f0e", alpha=0.10)

    # Mark the lead-change year(s): where (owner - renter) flips sign.
    diff = owner_nw - renter
    sign = np.sign(diff)
    for i in range(1, len(sign)):
        if sign[i - 1] != 0 and sign[i] != 0 and sign[i] != sign[i - 1]:
            # Linear-interpolate the exact crossover year between the two points.
            d0, d1 = diff[i - 1], diff[i]
            frac = d0 / (d0 - d1) if (d0 - d1) != 0 else 0.0
            xc = years[i - 1] + frac
            yc = renter[i - 1] + frac * (renter[i] - renter[i - 1])
            ax.scatter([xc], [yc], color="#444444", zorder=6, s=34)
            ax.annotate(
                f"lead change\nyr {xc:.1f}",
                xy=(xc, yc),
                xytext=(0, -28),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color="#222222",
                arrowprops=dict(arrowstyle="->", color="#444444", lw=0.9),
            )

    # Final values + the year-T gap.
    o_final, r_final = owner_nw[-1], renter[-1]
    for val, color in ((o_final, "#1f77b4"), (r_final, "#ff7f0e")):
        ax.scatter([years[-1]], [val], color=color, zorder=6, s=40)
    leader = "homeowner" if o_final >= r_final else "renter"
    # Anchored in open upper-centre space (not on the final point) to avoid
    # colliding with the title and the curves at the right edge.
    ax.text(
        0.36,
        0.90,
        f"Year {T}: {leader} ahead by {_money_str(abs(o_final - r_final), symbol)}\n"
        f"owner {_money_str(o_final, symbol)}  vs  renter {_money_str(r_final, symbol)}",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#444444", alpha=0.9),
    )

    ax.set_title("Chart 5 — Total Net Worth: Homeowner vs Renter", fontsize=13, fontweight="bold")
    ax.set_xlabel("Year")
    ax.set_ylabel("Total net worth")
    ax.set_xlim(0, T)
    ax.set_ylim(bottom=0)
    _style_axes(ax, symbol)
    ax.legend(loc="upper left", framealpha=0.9)
    fig.text(
        0.012,
        0.012,
        "Both scenarios spend the same amount each month; the difference is held as home equity "
        "(owner) or invested (renter). Fair apples-to-apples wealth comparison.",
        fontsize=7.5,
        color="#555555",
    )

    path = os.path.join(out_dir, "chart5_networth_comparison.png")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def generate_charts(projection: dict, params: dict, out_dir: str) -> list[str]:
    """Render all five buy-vs-rent charts to ``out_dir``.

    Parameters
    ----------
    projection : dict
        Year/month-by-year numpy arrays conforming to the schema in the module
        docstring.
    params : dict
        Input assumptions. Must include ``term_years``; ``down_payment`` is used
        in chart 2; other keys are optional annotations.
    out_dir : str
        Output directory for PNGs (created if it does not exist).

    Returns
    -------
    list[str]
        Absolute/relative paths of the five saved PNGs, in chart order 1..5.
    """
    if "term_years" not in params:
        raise KeyError("params must include 'term_years'")
    os.makedirs(out_dir, exist_ok=True)
    symbol = str(params.get("currency_symbol", "$"))

    paths = [
        _chart_house_value(projection, params, out_dir, symbol),
        _chart_cost_breakdown(projection, params, out_dir, symbol),
        _chart_renter_investment(projection, params, out_dir, symbol),
        _chart_owner_advantage(projection, params, out_dir, symbol),
        _chart_networth_comparison(projection, params, out_dir, symbol),
    ]
    return paths


# --------------------------------------------------------------------------- #
# Standalone self-test: build a synthetic projection and render all 4 charts.
# --------------------------------------------------------------------------- #
def _build_synthetic_projection(params: dict) -> dict:
    """Construct a small but internally-consistent projection for testing."""
    T = int(params["term_years"])
    n = T * 12
    months = np.arange(1, n + 1, dtype=float)
    years = np.arange(0, T + 1, dtype=float)

    price = params["purchase_price"]
    down = params["down_payment"]
    loan0 = price - down
    annual_rate = params["mortgage_rate"]
    mrate = annual_rate / 12.0
    appr = params["appreciation_rate"]
    invest = params["investment_return_rate"]
    mrate_inv = (1 + invest) ** (1 / 12) - 1

    # Amortization (monthly), then sample to annual.
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

    # Other monthly ownership costs.
    m_tax = np.full(n, price * params["property_tax_rate"] / 12.0)
    m_ins_hoa = np.full(n, (params["insurance_annual"] / 12.0) + params["hoa_monthly"])
    monthly_ownership_cost = pmt + m_tax + m_ins_hoa

    # Rent trajectory: grows annually, applied monthly.
    rent0 = params["rent_monthly"]
    rent_growth = params["rent_growth_rate"]
    year_of_month = (months - 1) // 12
    monthly_rent = rent0 * (1 + rent_growth) ** year_of_month

    # Annual home value / balance / equity.
    home_value = price * (1 + appr) ** years
    loan_balance = np.concatenate(([loan0], m_balance[11::12]))
    equity = home_value - loan_balance

    # Annual cumulative cost components (sampled at each year end).
    def _cum_annual(monthly_arr):
        cum = np.cumsum(monthly_arr)
        return np.concatenate(([0.0], cum[11::12]))

    cum_principal = _cum_annual(m_principal)
    cum_interest = _cum_annual(m_interest)
    cum_property_tax = _cum_annual(m_tax)
    cum_insurance_hoa = _cum_annual(m_ins_hoa)

    # Renter portfolio: lump (down payment) at month 0, then DCA of cost - rent.
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

    # Owner-advantage portfolio: DCA of rent - cost (positive after crossover).
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


if __name__ == "__main__":
    demo_params = {
        "term_years": 30,
        "purchase_price": 500_000.0,
        "down_payment": 100_000.0,
        "closing_costs": 10_000.0,
        "mortgage_rate": 0.065,
        "property_tax_rate": 0.011,
        "insurance_annual": 1_800.0,
        "hoa_monthly": 0.0,
        "appreciation_rate": 0.035,
        "rent_monthly": 2_000.0,
        "rent_growth_rate": 0.03,
        "investment_return_rate": 0.07,
        "currency_symbol": "$",
    }
    demo_projection = _build_synthetic_projection(demo_params)
    out = generate_charts(demo_projection, demo_params, "./_chart_preview")
    print("Rendered charts:")
    for p in out:
        print("  ", os.path.abspath(p))
