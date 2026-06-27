"""Regional + national-default financial assumptions.

Sourced by the `canada-housing-financial-scraper` agent (2026-06-25) for
postal code M2J 0E8 (North York, Toronto, ON) at a $1,000,000 target price,
plus Canada-wide fallbacks used when a different postal code is supplied.

All rates are annual decimals unless noted. Monetary values are CAD.

See module-level SOURCES for citations.
"""

# Toronto / North York (M2J) — researched values
TORONTO_M2J = {
    "home_appreciation_rate": 0.05,    # 5.0%/yr; hist GTA CAGR ~7%, set lower for fwd realism (range 4-7%)
    "maintenance_pct_of_value": 0.01,  # 1% of value/yr; industry range 1-3%
    "current_5yr_fixed_rate": 0.044,   # realistic uninsured $1M; best-available headline ~3.94%
    "mortgage_rate_30yr_avg": 0.055,   # Cdn 5yr fixed 30yr avg; range ~1.9%-8%
    "current_monthly_rent": 3800,      # CAD, comparable detached/large unit in M2J/North York
    "rent_growth_rate": 0.035,         # 3.5%/yr; CMHC Toronto long-term
    "property_tax_rate": 0.007673,     # Toronto 2026 total residential rate 0.7673% -> $7,673 on $1M
    "property_tax_growth_rate": 0.035, # 3.5%/yr long-run
    "sp500_nominal_cagr": 0.10,        # 10.0%; 30yr nominal w/ dividends = 10.31% (real 7.57%)
    "ltt_region": "toronto",           # land-transfer tax: Ontario provincial + Toronto municipal
}

# Ontario-wide defaults for any Ontario postal code outside the City of Toronto.
# These are province-level aggregates (researched, slow-moving) — sit between the
# Toronto figures and the national fallback. Land-transfer tax is already correct
# province-wide: tax.land_transfer_tax() charges the Ontario provincial LTT for
# ltt_region="ontario" and only adds the municipal LTT for "toronto".
ONTARIO_DEFAULTS = {
    "home_appreciation_rate": 0.05,    # Ontario long-run ~5%/yr (GTA-weighted; Teranet ON HPI)
    "maintenance_pct_of_value": 0.01,  # 1% of value/yr
    "current_5yr_fixed_rate": 0.044,   # baseline; overlaid by live BoC rate when --live
    "mortgage_rate_30yr_avg": 0.055,   # Cdn 5yr fixed 30yr avg
    "current_monthly_rent": 2800,      # CAD, Ontario single-family proxy (between national & Toronto)
    "rent_growth_rate": 0.035,         # 3.5%/yr; Ontario has run hot
    "property_tax_rate": 0.011,        # ~1.1% Ontario municipal avg (Toronto low ~0.77%, many cities 1-1.5%)
    "property_tax_growth_rate": 0.03,  # 3%/yr long-run
    "sp500_nominal_cagr": 0.10,        # 10% nominal w/ dividends
    "ltt_region": "ontario",           # Ontario provincial LTT only (no municipal LTT outside Toronto)
}

# Canada-wide fallbacks for any non-Ontario postal code
NATIONAL_DEFAULTS = {
    "home_appreciation_rate": 0.045,   # Canada-wide long-term ~4-5%/yr
    "maintenance_pct_of_value": 0.01,  # 1% of value/yr
    "current_5yr_fixed_rate": 0.044,   # Cdn best-discounted 5yr fixed, mid-2026
    "mortgage_rate_30yr_avg": 0.055,   # Cdn 5yr fixed 30yr avg
    "current_monthly_rent": 2200,      # CAD, national single-family proxy
    "rent_growth_rate": 0.03,          # 3%/yr national
    "property_tax_rate": 0.01,         # 1% of value/yr Canada-wide avg (varies 0.3%-2.5% by city)
    "property_tax_growth_rate": 0.03,  # 3%/yr
    "sp500_nominal_cagr": 0.10,        # 10% nominal w/ dividends
    "ltt_region": "ontario",           # land-transfer tax: Ontario provincial used as a national proxy
}

# First 3 chars of a Canadian postal code = Forward Sortation Area (FSA).
# Map known FSAs to their region param set. Extend as more areas are scraped.
_FSA_TO_PARAMS = {
    # North York FSAs around M2J
    "M2J": TORONTO_M2J,
    "M2H": TORONTO_M2J,
    "M2K": TORONTO_M2J,
    "M2N": TORONTO_M2J,
}

# Canadian postal districts (first letter) that fall inside Ontario. "M" is the
# City of Toronto specifically (municipal LTT applies), so it's handled
# separately from the rest of the province (K, L, N, P).
_ONTARIO_DISTRICTS = {"K", "L", "N", "P"}


def _fsa(postal_code: str) -> str:
    """Return the uppercased 3-char Forward Sortation Area of a postal code."""
    cleaned = (postal_code or "").replace(" ", "").upper()
    return cleaned[:3]


def get_params(postal_code: str, *, live: bool = False) -> dict:
    """Return the assumption set for a postal code.

    Routing, most specific first:
      1. a researched FSA (e.g. North York M2J);
      2. any other ``M`` FSA -> City of Toronto (Toronto property tax + municipal
         land-transfer tax), using the North York figures as the city proxy;
      3. any other Ontario district (K/L/N/P) -> ONTARIO_DEFAULTS;
      4. everything else -> NATIONAL_DEFAULTS.

    With ``live=True`` the volatile 5-year mortgage rate is overlaid from the
    Bank of Canada (see ``evaluator.live``); if that fetch fails the baked-in
    rate is kept. The returned dict is a fresh copy, safe for the caller to
    mutate (e.g. apply CLI overrides).
    """
    fsa = _fsa(postal_code)
    if fsa in _FSA_TO_PARAMS:
        params, label = _FSA_TO_PARAMS[fsa], "Toronto / North York (M2J)"
    elif fsa[:1] == "M":
        params, label = TORONTO_M2J, "Toronto (city default)"
    elif fsa[:1] in _ONTARIO_DISTRICTS:
        params, label = ONTARIO_DEFAULTS, "Ontario (provincial default)"
    else:
        params, label = NATIONAL_DEFAULTS, "Canada (national default)"

    result = dict(params)
    result["_region"] = label

    if live:
        from evaluator import live as live_data   # local import: keep data layer network-free by default
        rate = live_data.live_mortgage_rate()
        if rate:
            result["current_5yr_fixed_rate"] = rate["rate"]
            result["_live"] = rate

    return result


SOURCES = {
    "property_tax": "https://www.toronto.ca/services-payments/property-taxes-utilities/property-tax/property-tax-rates-and-fees/",
    "mortgage_rates": "https://www.ratehub.ca/best-mortgage-rates/5-year/fixed ; Statcan 34-10-0145-01",
    "appreciation": "https://rates.ca/resources/toronto-home-prices-20-years-growth ; TRREB market data",
    "rent": "https://www.zumper.com/rent-research/toronto-on ; CMHC HMIP rental tables",
    "sp500": "https://www.slickcharts.com/sp500/returns ; https://www.macrotrends.net/2526/sp-500-historical-annual-returns",
    "ontario_appreciation": "Teranet-National Bank HPI (Ontario CMAs) ; StatCan New Housing Price Index",
    "ontario_rent": "CMHC Housing Market Information Portal (Ontario CMAs)",
    "ontario_property_tax": "Municipal residential rate tables (MPAC assessed) ; rates vary by municipality",
    "live_mortgage_rate": "Bank of Canada Valet API series BD.CDN.5YR.DQ.YLD (5yr GoC benchmark yield) + lender spread",
}
