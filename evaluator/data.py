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

# Canada-wide fallbacks for any non-Toronto postal code
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


def _fsa(postal_code: str) -> str:
    """Return the uppercased 3-char Forward Sortation Area of a postal code."""
    cleaned = (postal_code or "").replace(" ", "").upper()
    return cleaned[:3]


def get_params(postal_code: str) -> dict:
    """Return the assumption set for a postal code.

    Falls back to NATIONAL_DEFAULTS when the FSA is unknown. The returned dict
    is a fresh copy, safe for the caller to mutate (e.g. apply CLI overrides).
    """
    params = _FSA_TO_PARAMS.get(_fsa(postal_code), NATIONAL_DEFAULTS)
    result = dict(params)
    result["_region"] = "Toronto / North York (M2J)" if params is TORONTO_M2J else "Canada (national default)"
    return result


SOURCES = {
    "property_tax": "https://www.toronto.ca/services-payments/property-taxes-utilities/property-tax/property-tax-rates-and-fees/",
    "mortgage_rates": "https://www.ratehub.ca/best-mortgage-rates/5-year/fixed ; Statcan 34-10-0145-01",
    "appreciation": "https://rates.ca/resources/toronto-home-prices-20-years-growth ; TRREB market data",
    "rent": "https://www.zumper.com/rent-research/toronto-on ; CMHC HMIP rental tables",
    "sp500": "https://www.slickcharts.com/sp500/returns ; https://www.macrotrends.net/2526/sp-500-historical-annual-returns",
}
