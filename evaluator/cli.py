"""Command-line orchestrator for the Canadian buy-vs-rent home evaluator.

Takes the four user inputs (house price, down payment, mortgage term, Canadian
postal code), pulls regional assumptions from :mod:`evaluator.data`, runs the
projection engine in :mod:`evaluator.projections`, renders the four charts via
:mod:`evaluator.charts`, and prints an executive summary.

Example
-------
    python -m evaluator.cli --price 1000000 --down 200000 --years 30 --postal "M2J 0E8"

Down payment accepts either a dollar amount (``200000``) or a percentage of the
price (``20%``). Any assumption can be overridden from the command line; run
``--help`` for the full list.
"""

from __future__ import annotations

import argparse
import os
import sys

from evaluator import data
from evaluator import projections
from evaluator import charts
from evaluator import tax


# --------------------------------------------------------------------------- #
# Input parsing
# --------------------------------------------------------------------------- #
def _parse_down_payment(raw: str, price: float) -> float:
    """Accept '200000', '$200,000', or '20%' and return a dollar amount."""
    s = raw.strip().replace("$", "").replace(",", "")
    if s.endswith("%"):
        pct = float(s[:-1]) / 100.0
        return round(price * pct, 2)
    return float(s)


def _money(value: float, symbol: str = "$") -> str:
    return f"{symbol}{value:,.0f}"


def build_engine_params(args: argparse.Namespace) -> dict:
    """Translate CLI args + regional scraper data into the engine's param keys."""
    price = float(args.price)
    down = _parse_down_payment(args.down, price)
    if down >= price:
        raise SystemExit(f"Down payment ({_money(down)}) must be less than price ({_money(price)}).")

    region = data.get_params(args.postal)

    # The scraper exposes data-named keys; the projection engine wants generic
    # keys. Map one to the other, letting explicit CLI flags override the region.
    params = {
        "term_years": int(args.years),
        "purchase_price": price,
        "down_payment": down,
        "postal_code": args.postal,
        "region_label": region.get("_region", "unknown"),
        "mortgage_rate": _override(args.rate, region["current_5yr_fixed_rate"]),
        "appreciation_rate": _override(args.appreciation, region["home_appreciation_rate"]),
        "property_tax_rate": _override(args.property_tax_rate, region["property_tax_rate"]),
        "property_tax_growth_rate": region["property_tax_growth_rate"],
        "maintenance_pct_of_value": region["maintenance_pct_of_value"],
        "insurance_annual": float(args.insurance),
        "hoa_monthly": float(args.hoa),
        "rent_monthly": _override(args.rent, region["current_monthly_rent"]),
        "rent_growth_rate": _override(args.rent_growth, region["rent_growth_rate"]),
        "investment_return_rate": _override(args.investment_return, region["sp500_nominal_cagr"]),
        "currency_symbol": "$",
        # ---- Tax layer (registered accounts + capital-gains tax) ----
        "current_age": int(args.age),
        "annual_income": float(args.income),
        "account_strategy": str(args.account_strategy),
        "retirement_marginal_rate": _override(args.retirement_rate, tax.RETIREMENT_MARGINAL_RATE),
    }

    # ---- Transaction costs (purchase land-transfer tax; sale commission) ----
    include_tx = not bool(getattr(args, "no_transaction_costs", False))
    ltt_region = region.get("ltt_region", "ontario")
    first_time = bool(getattr(args, "first_time_buyer", False))
    purchase_legal = _override(getattr(args, "purchase_legal", None), tax.PURCHASE_LEGAL_FEE)
    params["include_transaction_costs"] = include_tx
    params["ltt_region"] = ltt_region
    params["first_time_buyer"] = first_time
    params["purchase_closing_costs"] = (
        tax.purchase_closing_costs(price, ltt_region, first_time, purchase_legal)
        if include_tx else 0.0
    )
    params["commission_rate"] = _override(getattr(args, "commission_rate", None), tax.REALTOR_COMMISSION_RATE)
    params["hst_rate"] = tax.HST_RATE
    params["sale_legal_fee"] = tax.SALE_LEGAL_FEE
    return params


def _override(cli_value, default):
    """Return the CLI value if the user supplied one, else the regional default."""
    return float(cli_value) if cli_value is not None else default


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_report(params: dict, summary: dict, chart_paths: list[str]) -> None:
    sym = params["currency_symbol"]
    T = params["term_years"]
    line = "=" * 64
    print(line)
    print("  CANADIAN BUY-vs-RENT EVALUATION")
    print(line)
    print(f"  Postal code        : {params['postal_code']}  ({params['region_label']})")
    print(f"  House price         : {_money(params['purchase_price'], sym)}")
    print(f"  Down payment        : {_money(params['down_payment'], sym)} "
          f"({params['down_payment'] / params['purchase_price'] * 100:.1f}%)")
    print(f"  Mortgage term       : {T} years @ {params['mortgage_rate'] * 100:.2f}% fixed")
    print()
    print("  ASSUMPTIONS (regional, unless overridden):")
    print(f"    Home appreciation : {params['appreciation_rate'] * 100:.2f}%/yr")
    print(f"    Rent / growth     : {_money(params['rent_monthly'], sym)}/mo @ {params['rent_growth_rate'] * 100:.2f}%/yr")
    print(f"    Property tax      : {params['property_tax_rate'] * 100:.4f}%/yr (+{params['property_tax_growth_rate'] * 100:.1f}%/yr)")
    print(f"    Maintenance       : {params['maintenance_pct_of_value'] * 100:.2f}% of value/yr")
    print(f"    Insurance / HOA   : {_money(params['insurance_annual'], sym)}/yr  /  {_money(params['hoa_monthly'], sym)}/mo")
    print(f"    Investment return : {params['investment_return_rate'] * 100:.2f}%/yr (S&P 500)")
    print()
    print("  TAX LAYER (registered accounts + capital gains):")
    strat = params.get("account_strategy", "shelter-first")
    strat_label = ("TFSA -> RRSP -> taxable" if strat == "shelter-first"
                   else "taxable account only (no sheltering)")
    print(f"    Investor / income : age {params.get('current_age')}, "
          f"{_money(params.get('annual_income', 0), sym)}/yr")
    print(f"    Account strategy  : {strat}  ({strat_label})")
    print(f"    Marginal rate     : {tax.marginal_rate(params.get('annual_income', 0)) * 100:.1f}% "
          f"(refund on RRSP) ; withdrawal/cap-gains @ {params.get('retirement_marginal_rate', 0.30) * 100:.1f}%")
    print(f"    TFSA room (now)   : {_money(tax.tfsa_cumulative_room(int(params.get('current_age', 35))), sym)}"
          f"  ;  RRSP room/yr {_money(tax.rrsp_annual_room(params.get('annual_income', 0)), sym)}")
    print("    Home: principal residence -> capital-gains-tax-FREE on sale")
    if params.get("include_transaction_costs", True):
        pc = summary.get("purchase_closing_costs", params.get("purchase_closing_costs", 0))
        sc = summary.get("selling_costs_final", 0)
        ft = "  (first-time-buyer rebate applied)" if params.get("first_time_buyer") else ""
        print()
        print("  TRANSACTION COSTS:")
        print(f"    At purchase       : {_money(pc, sym)} land-transfer tax + legal "
              f"({params.get('ltt_region', 'ontario')}){ft}")
        print(f"    At sale (yr {T})    : {_money(sc, sym)} "
              f"(~{params.get('commission_rate', 0.05) * 100:.0f}% commission + HST + legal)")
        print("    (Purchase cost is credited to the renter's invested lump.)")
    else:
        print()
        print("  TRANSACTION COSTS: disabled")
    print(line)

    cy = summary.get("crossover_year")
    if cy is not None:
        print(f"  Crossover year      : ~yr {cy:.1f} (rent first exceeds total ownership cost)")
    else:
        print("  Crossover year      : never (rent stays below ownership cost for the whole term)")
    print()
    nw_tag = "after tax" if summary.get("after_tax") else "pre-tax"
    print(f"  {'Net worth (' + nw_tag + ')':<18}{'Buyer (equity+inv)':>22}{'Renter (portfolio)':>22}")
    for yr in (10, 15, 20, T):
        b = summary["buyer_net_worth"].get(yr)
        r = summary["renter_net_worth"].get(yr)
        if b is None or r is None:
            continue
        print(f"  yr {yr:<15}{_money(b, sym):>22}{_money(r, sym):>22}")
    print()
    if summary.get("after_tax"):
        print(f"  Renter portfolio split @ yr {T} : "
              f"TFSA {_money(summary.get('renter_final_tfsa', 0), sym)}  /  "
              f"RRSP {_money(summary.get('renter_final_rrsp', 0), sym)}  /  "
              f"taxable {_money(summary.get('renter_final_taxable', 0), sym)}")
        print(f"  Tax paid at liquidation       : "
              f"renter {_money(summary.get('renter_tax_paid', 0), sym)}  /  "
              f"buyer {_money(summary.get('buyer_tax_paid', 0), sym)} "
              f"(home exempt)")
    print(f"  Total cost of ownership ({T}yr) : {_money(summary['total_cost_of_ownership'], sym)}")
    print(f"  Total interest paid           : {_money(summary['total_interest_paid'], sym)}")
    print(f"  Total rent paid ({T}yr)         : {_money(summary['total_rent_paid'], sym)}")
    delta = summary["final_buyer_minus_renter"]
    leader = "buyer" if delta >= 0 else "renter"
    print(f"  Year-{T} net-worth gap ({nw_tag}) : {_money(abs(delta), sym)} in favour of the {leader}")
    print(line)
    print("  Charts written:")
    for p in chart_paths:
        print(f"    - {os.path.abspath(p)}")
    print(line)
    print("  Note: projections use historical/long-run assumptions and are not")
    print("  financial advice. Past returns do not guarantee future results.")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="home-evaluator",
        description="Canadian buy-vs-rent analysis: 4 charts + summary from 4 inputs.",
    )
    p.add_argument("--price", required=True, type=float, help="House purchase price, e.g. 1000000")
    p.add_argument("--down", required=True, type=str, help="Down payment: dollars (200000) or percent (20%%)")
    p.add_argument("--years", required=True, type=int, help="Mortgage term in years, e.g. 30")
    p.add_argument("--postal", required=True, type=str, help="Canadian postal code, e.g. 'M2J 0E8'")

    p.add_argument("--out", default="./charts_output", help="Output directory for chart PNGs")
    p.add_argument("--no-charts", action="store_true", help="Skip chart rendering; print summary only")

    # Tax layer: registered-account sheltering + capital-gains tax.
    t = p.add_argument_group("tax layer (registered accounts + capital gains)")
    t.add_argument("--age", type=int, default=35, help="Investor age now (sets TFSA room; default 35)")
    t.add_argument("--income", type=float, default=120000.0,
                   help="Gross annual income (sets RRSP room + marginal rate; default 120000)")
    t.add_argument("--account-strategy", choices=["shelter-first", "taxable-only"],
                   default="shelter-first",
                   help="shelter-first: fill TFSA->RRSP then taxable; taxable-only: no sheltering")
    t.add_argument("--retirement-rate", type=float,
                   help="Marginal rate on RRSP withdrawals + realized gains at term end (decimal, default 0.30)")

    # Transaction costs: land-transfer tax at purchase, realtor/legal at sale.
    x = p.add_argument_group("transaction costs (purchase + sale)")
    x.add_argument("--first-time-buyer", action="store_true",
                   help="Apply first-time-buyer land-transfer-tax rebates")
    x.add_argument("--commission-rate", type=float,
                   help="Realtor commission at sale (decimal, default 0.05)")
    x.add_argument("--purchase-legal", type=float,
                   help="Legal + inspection fees at purchase (default 2500)")
    x.add_argument("--no-transaction-costs", action="store_true",
                   help="Disable purchase/sale transaction costs (compare without them)")

    # Optional assumption overrides (default to regional scraper data).
    o = p.add_argument_group("assumption overrides (default: regional)")
    o.add_argument("--rate", type=float, help="Mortgage interest rate (decimal, e.g. 0.044)")
    o.add_argument("--appreciation", type=float, help="Annual home appreciation (decimal)")
    o.add_argument("--rent", type=float, help="Current monthly rent for a comparable home")
    o.add_argument("--rent-growth", type=float, help="Annual rent growth (decimal)")
    o.add_argument("--property-tax-rate", type=float, help="Annual property tax rate (decimal)")
    o.add_argument("--investment-return", type=float, help="Annual investment return (decimal)")
    o.add_argument("--insurance", type=float, default=1500.0, help="Annual home insurance (default 1500)")
    o.add_argument("--hoa", type=float, default=0.0, help="Monthly HOA/condo fee (default 0)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    params = build_engine_params(args)

    projection = projections.build_projection(params)
    summary = projections.compute_summary(projection, params)

    chart_paths: list[str] = []
    if not args.no_charts:
        chart_paths = charts.generate_charts(projection, params, args.out)

    print_report(params, summary, chart_paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
