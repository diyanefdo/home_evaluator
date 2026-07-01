"""Regional + national-default financial assumptions.

Sourced by the `canada-housing-financial-scraper` agent (2026-06-25) for
postal code M2J 0E8 (North York, Toronto, ON) at a $1,000,000 target price,
plus Canada-wide fallbacks used when a different postal code is supplied.

All rates are annual decimals unless noted. Monetary values are CAD.

See module-level SOURCES for citations.
"""

# Rent does not scale 1:1 with home price: a home worth twice as much does NOT
# rent for twice as much (luxury homes have lower gross rental yields, starter
# homes higher). So instead of a flat regional rent, we scale a region's
# benchmark rent by the entered price using a sub-linear power law:
#
#     rent(price) = current_monthly_rent * (price / benchmark_price) ** RENT_PRICE_ELASTICITY
#
# Each region pairs current_monthly_rent with the benchmark_price it corresponds
# to (the region's typical single-family home, 2025-26). An elasticity of 0.7 is
# the empirical middle of the road for Canadian markets (rent ~ price^0.7); the
# implied gross yield therefore falls as price rises and rises as price falls.
RENT_PRICE_ELASTICITY = 0.7

# Toronto / North York (M2J) — researched values
TORONTO_M2J = {
    "home_appreciation_rate": 0.045,   # Teranet TO ~7-8% boom CAGR / CREA ~6%; haircut: affordability ceiling + rate norm. (high conf)
    "maintenance_pct_of_value": 0.01,  # 1% of value/yr; industry range 1-3%
    "current_5yr_fixed_rate": 0.044,   # realistic uninsured $1M; best-available headline ~3.94%
    "mortgage_rate_30yr_avg": 0.055,   # Cdn 5yr fixed 30yr avg; range ~1.9%-8%
    "current_monthly_rent": 3800,      # CAD, comparable detached/large unit in M2J/North York
    "benchmark_price": 1_500_000,      # the North York detached the $3,800 rent reflects (~3% gross yield)
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
    "home_appreciation_rate": 0.0425,  # Teranet/CREA ON aggregate ~6%+ boom, net of rate normalization. (high conf)
    "maintenance_pct_of_value": 0.01,  # 1% of value/yr
    "current_5yr_fixed_rate": 0.044,   # baseline; overlaid by live BoC rate when --live
    "mortgage_rate_30yr_avg": 0.055,   # Cdn 5yr fixed 30yr avg
    "current_monthly_rent": 2800,      # CAD, Ontario single-family proxy (between national & Toronto)
    "benchmark_price": 850_000,        # typical Ontario single-family the rent reflects (~4% gross yield)
    "rent_growth_rate": 0.035,         # 3.5%/yr; Ontario has run hot
    "property_tax_rate": 0.011,        # ~1.1% Ontario municipal avg (Toronto low ~0.77%, many cities 1-1.5%)
    "property_tax_growth_rate": 0.03,  # 3%/yr long-run
    "sp500_nominal_cagr": 0.10,        # 10% nominal w/ dividends
    "ltt_region": "ontario",           # Ontario provincial LTT only (no municipal LTT outside Toronto)
}

# Per-CMA Ontario tiers (researched by the canada-housing-financial-scraper agent).
# Appreciation = forward-sustainable long-run HPI (CREA/Teranet-NBC), deliberately
# below 2000-2022 boom CAGRs. Rent = blended single-family / 3-bed asking rents
# (Zumper/Rentals.ca/Zolo, 2025-26; ~+/-10%). property_tax_rate = the main city's
# 2025 total residential rate (WOWA + city by-laws); these are hard published
# figures and rise with each annual budget, so re-verify yearly. ltt_region stays
# "ontario" (only Toronto levies a municipal land-transfer tax). See SOURCES.
REGION_TIERS = {
    "ottawa": {
        "home_appreciation_rate": 0.04,    # Teranet Ottawa-Gatineau ~5% CAGR (2005-24); stable federal-employment base. (high conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2800,      # 3-bed/SFH; Zumper/Rentals.ca 2025-26
        "benchmark_price": 700_000,        # Ottawa benchmark SFH (~4.8% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.012271,     # City of Ottawa 2025 total residential 1.227103%
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "hamilton": {
        "home_appreciation_rate": 0.0425,  # Teranet Hamilton ~8% boom CAGR / CREA RAHB ~7%; stretched affordability + correction. (high conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2500,      # 3-bed/SFH; Rentals.ca/Zumper 2025-26
        "benchmark_price": 800_000,        # Hamilton benchmark SFH (~3.75% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.014970,     # City of Hamilton 2025 total residential 1.497000%
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "kitchener_waterloo": {
        "home_appreciation_rate": 0.045,   # CREA KWAR benchmark ~6-7% CAGR; tech base, cheaper than GTA but rate-sensitive. (med conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2450,      # 3-bed/SFH; Zumper 2025-26
        "benchmark_price": 780_000,        # Kitchener-Waterloo benchmark SFH (~3.77% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.013567,     # City of Kitchener 2025 total residential 1.356658% (CMA proxy)
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "london": {
        "home_appreciation_rate": 0.0425,  # CREA LSTAR ~7-8% peak CAGR off low base; elastic SW-Ont supply. (med conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2300,      # 3-bed/SFH; Zumper 2025-26
        "benchmark_price": 650_000,        # London benchmark SFH (~4.25% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.013889,     # City of London 2025 total residential 1.388893%
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "windsor": {
        "home_appreciation_rate": 0.0375,  # CREA Windsor-Essex ~7% peak off low base; auto/USMCA risk + elastic supply. (med conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2100,      # 3-bed/SFH; Zumper 2025-26 (cheapest CMA here)
        "benchmark_price": 580_000,        # Windsor benchmark SFH (~4.34% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.020953,     # City of Windsor 2025 total residential 2.095293% (highest in ON)
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "oshawa": {
        "home_appreciation_rate": 0.0425,  # CREA/TRREB Durham ~6-7% CAGR; GTA-commuter, rate/commute-sensitive. (med conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2600,      # 3-bed/SFH; Zumper/Apartments.com 2025-26
        "benchmark_price": 850_000,        # Oshawa/Durham benchmark SFH (~3.67% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.015245,     # City of Oshawa 2025 total residential 1.524475% (highest in GTA)
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "barrie": {
        "home_appreciation_rate": 0.0425,  # CREA Barrie ~6-7% boom, largest swing; commute/rate-sensitive. (med-low conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2500,      # 3-bed/SFH; Zumper/Zillow 2025-26
        "benchmark_price": 800_000,        # Barrie benchmark SFH (~3.75% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.014118,     # City of Barrie 2025 total residential 1.411754%
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "kingston": {
        "home_appreciation_rate": 0.0375,  # CREA Kingston ~5-6% CAGR; small stable institutional economy. (low conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2200,      # 3-bed/SFH; Zumper 2025-26
        "benchmark_price": 620_000,        # Kingston benchmark SFH (~4.26% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.015518,     # City of Kingston 2025 total residential 1.551784%
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "guelph": {
        "home_appreciation_rate": 0.045,   # CREA Guelph ~6-7% CAGR; low unemployment + Greenbelt supply constraint. (low-med conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2400,      # 3-bed/SFH; Rentals.ca/RentCafe 2025-26
        "benchmark_price": 800_000,        # Guelph benchmark SFH (~3.6% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.013977,     # City of Guelph 2025 total residential 1.397700%
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
    "st_catharines_niagara": {
        "home_appreciation_rate": 0.0425,  # Teranet/CREA Niagara ~7% peak off low base; retiree + GTA/Buffalo spillover. (med conf)
        "maintenance_pct_of_value": 0.01,
        "current_5yr_fixed_rate": 0.044,
        "mortgage_rate_30yr_avg": 0.055,
        "current_monthly_rent": 2400,      # 3-bed/SFH; Zumper/Zolo 2025-26
        "benchmark_price": 650_000,        # St. Catharines-Niagara benchmark SFH (~4.43% gross yield)
        "rent_growth_rate": 0.03,
        "property_tax_rate": 0.017749,     # City of St. Catharines 2025 total residential 1.774882% (CMA proxy)
        "property_tax_growth_rate": 0.03,
        "sp500_nominal_cagr": 0.10,
        "ltt_region": "ontario",
    },
}

# Human-readable region labels for each CMA tier.
_CMA_LABELS = {
    "ottawa": "Ottawa (Ontario CMA)",
    "hamilton": "Hamilton (Ontario CMA)",
    "kitchener_waterloo": "Kitchener-Waterloo (Ontario CMA)",
    "london": "London (Ontario CMA)",
    "windsor": "Windsor (Ontario CMA)",
    "oshawa": "Oshawa / Durham (Ontario CMA)",
    "barrie": "Barrie (Ontario CMA)",
    "kingston": "Kingston (Ontario CMA)",
    "guelph": "Guelph (Ontario CMA)",
    "st_catharines_niagara": "St. Catharines-Niagara (Ontario CMA)",
}

# Canada-wide fallbacks. With province routing (below) covering all ten provinces,
# this now only catches the territories (postal X/Y). ltt_region uses the low
# Saskatchewan title-fee (0.3%) as a proxy for the territories' small registration
# fees — far closer than Ontario's ~1.5% LTT.
NATIONAL_DEFAULTS = {
    "home_appreciation_rate": 0.0375,  # Teranet Composite-11 ~5.9% (2005-24)/~7.7% peak; blends slower Prairie/Atlantic. (high conf)
    "maintenance_pct_of_value": 0.01,  # 1% of value/yr
    "current_5yr_fixed_rate": 0.044,   # Cdn best-discounted 5yr fixed, mid-2026
    "mortgage_rate_30yr_avg": 0.055,   # Cdn 5yr fixed 30yr avg
    "current_monthly_rent": 2200,      # CAD, national single-family proxy
    "benchmark_price": 700_000,        # typical Canadian single-family the rent reflects (~3.8% gross yield)
    "rent_growth_rate": 0.03,          # 3%/yr national
    "property_tax_rate": 0.01,         # 1% of value/yr Canada-wide avg (varies 0.3%-2.5% by city)
    "property_tax_growth_rate": 0.03,  # 3%/yr
    "sp500_nominal_cagr": 0.10,        # 10% nominal w/ dividends
    "ltt_region": "saskatchewan",      # territories proxy: small registration fees, not a full LTT
}


# --------------------------------------------------------------------------- #
# Non-Ontario provinces & metros (Phase 3 — more regions + province-correct LTT).
# --------------------------------------------------------------------------- #
# These tiers were added to fix the biggest correctness gap for non-Ontario users:
# land-transfer tax and property district were previously an Ontario proxy. The
# LTT (ltt_region) and property-tax rates below are the headline fix; the
# appreciation / rent / benchmark_price macro figures are FIRST-PASS researched
# estimates (2025-26, med/low confidence) pending the same scraper-grounding the
# Ontario CMAs received — flagged inline. See SOURCES.
def _tier(**overrides) -> dict:
    """Build a region tier from NATIONAL_DEFAULTS, overriding only what differs.

    Keeps the shared, non-geographic constants (mortgage rate, S&P CAGR,
    maintenance, growth rates) in one place. The Ontario tiers above predate this
    helper and stay explicit; ltt_region is always set explicitly here.
    """
    return {**NATIONAL_DEFAULTS, **overrides}


# --- British Columbia (postal V) — BC Property Transfer Tax ------------------ #
VANCOUVER = _tier(
    home_appreciation_rate=0.045,  # constrained land + demand vs extreme affordability ceiling. (med conf, first-pass)
    current_monthly_rent=4600,     # Greater Vancouver detached blended asking, 2025-26
    benchmark_price=1_900_000,     # Greater Vancouver detached benchmark (~2.9% gross yield)
    property_tax_rate=0.00278,     # City of Vancouver ~0.278% (lowest in Canada; re-verify)
    ltt_region="bc",
)
BC_DEFAULTS = _tier(
    home_appreciation_rate=0.0425,  # BC ex-Vancouver long-run, land-constrained. (low-med conf, first-pass)
    current_monthly_rent=3200,
    benchmark_price=950_000,
    property_tax_rate=0.004,        # BC municipal avg ~0.4% (Victoria/Surrey/interior blend; re-verify)
    ltt_region="bc",
)

# --- Alberta (postal T) — no LTT, only land-title registration fees ---------- #
CALGARY = _tier(
    home_appreciation_rate=0.0375,  # elastic supply, oil-cyclical, strong 2022-24. (med conf, first-pass)
    current_monthly_rent=2400,
    benchmark_price=650_000,
    property_tax_rate=0.0066,       # City of Calgary 2024 residential ~0.66% (re-verify)
    ltt_region="alberta",
)
EDMONTON = _tier(
    home_appreciation_rate=0.03,    # very elastic supply, flat long-run real prices. (med conf, first-pass)
    current_monthly_rent=1950,
    benchmark_price=450_000,
    property_tax_rate=0.0096,       # City of Edmonton 2024 residential ~0.96% (re-verify)
    ltt_region="alberta",
)
ALBERTA_DEFAULTS = _tier(
    home_appreciation_rate=0.0325,
    current_monthly_rent=2100,
    benchmark_price=500_000,
    property_tax_rate=0.0085,       # AB municipal avg (Red Deer/Lethbridge/north blend; re-verify)
    ltt_region="alberta",
)

# --- Quebec (postal G/H/J) — transfer duties ("welcome tax") ----------------- #
MONTREAL = _tier(
    home_appreciation_rate=0.04,    # strong post-2020 off an affordable base. (med conf, first-pass)
    current_monthly_rent=2200,
    benchmark_price=650_000,
    property_tax_rate=0.0075,       # Ville de Montreal effective ~0.75% incl. services (re-verify)
    ltt_region="quebec_montreal",   # Montreal levies its own luxury-tier welcome tax
)
QUEBEC_DEFAULTS = _tier(
    home_appreciation_rate=0.035,   # Quebec ex-Montreal (Quebec City, regions). (low-med conf, first-pass)
    current_monthly_rent=1850,
    benchmark_price=450_000,
    property_tax_rate=0.0085,       # QC municipal avg (Quebec City ~0.87%; re-verify)
    ltt_region="quebec",
)

# --- Manitoba (postal R) — Manitoba Land Transfer Tax ------------------------ #
WINNIPEG = _tier(
    home_appreciation_rate=0.035,   # steady, affordable prairie market. (med conf, first-pass)
    current_monthly_rent=1900,
    benchmark_price=400_000,
    property_tax_rate=0.0125,       # City of Winnipeg municipal+education ~1.25% (re-verify)
    ltt_region="manitoba",
)
MANITOBA_DEFAULTS = _tier(
    home_appreciation_rate=0.0325,
    current_monthly_rent=1850,
    benchmark_price=380_000,
    property_tax_rate=0.013,
    ltt_region="manitoba",
)

# --- Saskatchewan (postal S) — no LTT, 0.3% land-title fee ------------------- #
SASKATCHEWAN_DEFAULTS = _tier(
    home_appreciation_rate=0.03,    # flat, resource-linked (Saskatoon/Regina). (low-med conf, first-pass)
    current_monthly_rent=1750,
    benchmark_price=380_000,
    property_tax_rate=0.011,        # Saskatoon ~1.03% / Regina ~1.16% blend (re-verify)
    ltt_region="saskatchewan",
)

# --- Atlantic (postal A/B/C/E) — per-province deed/transfer taxes ------------ #
HALIFAX = _tier(
    home_appreciation_rate=0.04,    # hot post-2020 in-migration. (med conf, first-pass)
    current_monthly_rent=2500,
    benchmark_price=550_000,
    property_tax_rate=0.0115,       # HRM residential ~1.15% incl. area rates (re-verify)
    ltt_region="nova_scotia",       # HRM deed transfer 1.5%
)
NOVA_SCOTIA_DEFAULTS = _tier(
    home_appreciation_rate=0.04,
    current_monthly_rent=2100,
    benchmark_price=480_000,
    property_tax_rate=0.0115,
    ltt_region="nova_scotia",
)
NEW_BRUNSWICK_DEFAULTS = _tier(
    home_appreciation_rate=0.035,
    current_monthly_rent=1750,
    benchmark_price=350_000,
    property_tax_rate=0.015,        # NB provincial+municipal residential ~1.5% (re-verify)
    ltt_region="new_brunswick",
)
PEI_DEFAULTS = _tier(
    home_appreciation_rate=0.04,
    current_monthly_rent=1900,
    benchmark_price=420_000,
    property_tax_rate=0.015,
    ltt_region="pei",
)
NEWFOUNDLAND_DEFAULTS = _tier(
    home_appreciation_rate=0.025,   # flat/declining outside St. John's. (low conf, first-pass)
    current_monthly_rent=1600,
    benchmark_price=350_000,
    property_tax_rate=0.0083,       # St. John's ~0.83% (re-verify)
    ltt_region="newfoundland",
)

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

# 3-char FSA overrides, checked before the 2-char map to disambiguate FSAs whose
# 2-char prefix spans more than one CMA (or a non-CMA area):
#   - L4 is mostly York Region (Vaughan/Aurora/...); only L4M/L4N are Barrie.
#   - N1 is Guelph except N1R/N1S/N1T, which are Cambridge (Kitchener-Waterloo CMA).
_FSA3_TO_CMA = {
    "L4M": "barrie", "L4N": "barrie",
    "N1R": "kitchener_waterloo", "N1S": "kitchener_waterloo", "N1T": "kitchener_waterloo",
}

# 2-char FSA prefix -> CMA key (researched routing; see canada-housing-financial-scraper).
_FSA2_TO_CMA = {
    "K1": "ottawa", "K2": "ottawa", "K4": "ottawa",   # central / Kanata-Nepean / Orleans
    "K7": "kingston",
    "L1": "oshawa",                                    # Durham: Oshawa/Whitby/Ajax/Pickering
    "L2": "st_catharines_niagara",                     # St. Catharines / Niagara Falls
    "L8": "hamilton", "L9": "hamilton",
    "N1": "guelph",
    "N2": "kitchener_waterloo", "N3": "kitchener_waterloo",  # N3 = Cambridge (Brantford partial)
    "N5": "london", "N6": "london",
    "N8": "windsor", "N9": "windsor",
}


# --- Non-Ontario routing (Phase 3) ------------------------------------------ #
# Metros routed by 2-char FSA prefix (checked before the province-letter default).
_FSA2_TO_METRO = {
    "V5": "vancouver", "V6": "vancouver", "V7": "vancouver", "V3": "vancouver", "V4": "vancouver",
    "T2": "calgary", "T3": "calgary",
    "T5": "edmonton", "T6": "edmonton",
    "R2": "winnipeg", "R3": "winnipeg",
    "B3": "halifax",
}
_METRO_TIERS = {
    "vancouver": VANCOUVER, "calgary": CALGARY, "edmonton": EDMONTON,
    "winnipeg": WINNIPEG, "halifax": HALIFAX,
}  # Montreal (all of postal district H) is routed by letter below
_METRO_LABELS = {
    "vancouver": "Vancouver (BC metro)", "calgary": "Calgary (Alberta metro)",
    "edmonton": "Edmonton (Alberta metro)", "winnipeg": "Winnipeg (Manitoba metro)",
    "halifax": "Halifax (Nova Scotia metro)", "montreal": "Montreal (Quebec metro)",
}
# Postal first-letter -> provincial default tier + label. Ontario (K/L/M/N/P) and
# Quebec's Montreal island (H) are handled separately in get_params; X/Y
# (territories) fall through to NATIONAL_DEFAULTS.
_DISTRICT_TO_PROVINCE = {
    "V": (BC_DEFAULTS, "British Columbia (provincial default)"),
    "T": (ALBERTA_DEFAULTS, "Alberta (provincial default)"),
    "S": (SASKATCHEWAN_DEFAULTS, "Saskatchewan (provincial default)"),
    "R": (MANITOBA_DEFAULTS, "Manitoba (provincial default)"),
    "G": (QUEBEC_DEFAULTS, "Quebec (provincial default)"),
    "J": (QUEBEC_DEFAULTS, "Quebec (provincial default)"),
    "B": (NOVA_SCOTIA_DEFAULTS, "Nova Scotia (provincial default)"),
    "E": (NEW_BRUNSWICK_DEFAULTS, "New Brunswick (provincial default)"),
    "C": (PEI_DEFAULTS, "Prince Edward Island (provincial default)"),
    "A": (NEWFOUNDLAND_DEFAULTS, "Newfoundland & Labrador (provincial default)"),
}


def _fsa(postal_code: str) -> str:
    """Return the uppercased 3-char Forward Sortation Area of a postal code."""
    cleaned = (postal_code or "").replace(" ", "").upper()
    return cleaned[:3]


def estimate_monthly_rent(params: dict, price: float) -> float:
    """Estimate the comparable monthly rent for a *specific* home in its region.

    The regional ``current_monthly_rent`` is the rent for that region's typical
    home (``benchmark_price``). Real homes differ: an expensive home rents for
    more than the regional average, a cheaper one for less — but NOT in lock-step
    with price, because gross rental yields fall as price rises. We scale by the
    price ratio raised to :data:`RENT_PRICE_ELASTICITY` (< 1, so rent grows
    sub-linearly with price). At ``price == benchmark_price`` the result is
    exactly ``current_monthly_rent``.

    Falls back to the flat regional rent if the benchmark is missing or the price
    is non-positive.
    """
    base_rent = float(params.get("current_monthly_rent", 0.0))
    benchmark = float(params.get("benchmark_price", 0.0))
    if base_rent <= 0 or benchmark <= 0 or price <= 0:
        return base_rent
    rent = base_rent * (price / benchmark) ** RENT_PRICE_ELASTICITY
    # Keep it sane for extreme inputs; the power law already dampens the tails.
    return round(max(500.0, rent), 2)


def get_params(postal_code: str, *, live: bool = False) -> dict:
    """Return the assumption set for a postal code.

    Routing, most specific first:
      1. a researched FSA (e.g. North York M2J);
      2. any other ``M`` FSA -> City of Toronto (Toronto property tax + municipal
         land-transfer tax), using the North York figures as the city proxy;
      3. a 3-char FSA override -> its Ontario CMA tier (L4M/L4N, N1R/S/T);
      4. a 2-char FSA prefix -> its Ontario CMA tier (Ottawa, Hamilton, ...);
      5. any other Ontario district (K/L/N/P) -> ONTARIO_DEFAULTS;
      6. a non-Ontario metro FSA (V5-7 Vancouver, T2/3 Calgary, ...) -> its metro tier;
      7. Quebec's Montreal island (H) -> MONTREAL (Montreal welcome-tax tiers);
      8. any other province letter (A/B/C/E/G/J/R/S/T/V) -> its provincial default;
      9. everything else (territories X/Y) -> NATIONAL_DEFAULTS.

    Each tier carries a province-correct ``ltt_region`` so land-transfer tax and
    the CMHC-premium PST are computed with the right provincial rules.

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
    elif fsa in _FSA3_TO_CMA:
        key = _FSA3_TO_CMA[fsa]
        params, label = REGION_TIERS[key], _CMA_LABELS[key]
    elif fsa[:2] in _FSA2_TO_CMA:
        key = _FSA2_TO_CMA[fsa[:2]]
        params, label = REGION_TIERS[key], _CMA_LABELS[key]
    elif fsa[:1] in _ONTARIO_DISTRICTS:
        params, label = ONTARIO_DEFAULTS, "Ontario (provincial default)"
    elif fsa[:2] in _FSA2_TO_METRO:
        key = _FSA2_TO_METRO[fsa[:2]]
        params, label = _METRO_TIERS[key], _METRO_LABELS[key]
    elif fsa[:1] == "H":
        params, label = MONTREAL, _METRO_LABELS["montreal"]
    elif fsa[:1] in _DISTRICT_TO_PROVINCE:
        params, label = _DISTRICT_TO_PROVINCE[fsa[:1]]
    else:
        params, label = NATIONAL_DEFAULTS, "Canada / Territories (national default)"

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
    "ontario_cma_property_tax": "Per-city 2025 total residential rates: wowa.ca/taxes/<city>-property-tax + city by-laws (re-verify annually)",
    "ontario_cma_rent": "Blended SFH/3-bed asking rents: rentals.ca, zumper.com/rent-research/<city>-on, CMHC HMIP",
    "ontario_cma_appreciation": "Per-region forward-sustainable rate derived from long-run HPI (see appreciation_methodology); anchors + confidence are inline beside each region's home_appreciation_rate.",
    # --- House-price-index sources behind the per-region appreciation rates ---
    "teranet_nb_hpi": "Teranet-National Bank House Price Index, composite-11 + extended CMAs, base June 2005=100 -- https://housepriceindex.ca/ (index history & monthly reports)",
    "crea_mls_hpi": "CREA MLS Home Price Index / benchmark price, by board -- https://www.crea.ca/housing-market-stats/mls-home-price-index/hpi-tool/ ; board series at https://creastats.crea.ca/",
    "trreb_hpi": "TRREB MLS HPI (Toronto/Durham/Oshawa) -- https://trreb.ca/market-data/mls-home-price-index/",
    "kwar_hpi": "Kitchener-Waterloo Assoc. of REALTORS HPI dashboard -- https://kwar.ca/hpi-dashboard/",
    "statcan_nhpi": "Statistics Canada, New Housing Price Index, table 18-10-0205 -- https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810020501",
    "nhpi_crosscheck": (
        "Cross-check (2026-06-30): the per-region forward rates were sanity-checked against actual "
        "StatCan NHPI CAGRs computed from table 18-10-0205 (Total house+land, 2005-01 -> 2026-05): "
        "Toronto 2.2%, Ottawa 3.3%, Kitchener-Waterloo 3.1%, London 2.9%, Windsor 2.0%, "
        "St. Catharines-Niagara 2.3%, Ontario 2.5%, Canada 2.6% (Barrie & Kingston not in NHPI). "
        "NHPI measures NEW-build contractor prices and is quality-controlled, so it structurally "
        "UNDERSTATES the resale appreciation an owner realizes (Teranet/CREA resale ran ~6-8% over the "
        "same window) -- it is a FLOOR, not the modelling basis. The forward rates (3.75-4.50%) sit "
        "sensibly above this quality-controlled floor and below the un-repeatable resale boom CAGRs."
    ),
    "teranet_nbc_report": "National Bank of Canada, Teranet-NB HPI monthly economic note -- https://www.nbc.ca/ (economic-news-teranet)",
    "appreciation_methodology": (
        "Forward rates were derived from each market's long-run Teranet-NB HPI and CREA MLS HPI "
        "benchmark history (June-2005=100 base, 2005-2024): take the boom-era CAGR (2005-2022) and the "
        "full-window CAGR, then apply a downward forward-sustainability haircut for a 20-30yr horizon "
        "reflecting the affordability ceiling, mortgage-rate normalization, softer immigration/demographics, "
        "and supply elasticity (larger haircut where land is abundant, smaller where constrained). Rounded "
        "to the nearest 0.0025, kept within a defensible ~3.75-4.50% band (~1-2% above expected inflation). "
        "Smaller CMAs (KW, London, Windsor, Oshawa, Barrie, Kingston, Guelph, St. Catharines-Niagara) rely "
        "on CREA board benchmarks rather than the core Teranet-11 -> lower confidence (flagged inline). "
        "Verified 2026-06-30; all markets were mid-correction at that time, so these are mid-cycle trend "
        "assumptions, not near-term forecasts."
    ),
    "live_mortgage_rate": "Bank of Canada Valet API series BD.CDN.5YR.DQ.YLD (5yr GoC benchmark yield) + lender spread",
    "rent_from_price": "Rent scaled from each region's benchmark price/rent pair via a sub-linear "
                       "price-to-rent elasticity (rent ~ price^0.7); gross yields fall as price rises.",
    "province_coverage": (
        "Phase 3 (2026-07-01): all ten provinces now route to a province-correct tier by postal "
        "first-letter (V=BC, T=AB, S=SK, R=MB, G/H/J=QC, A/B/C/E=Atlantic; K/L/M/N/P=ON), with metro "
        "overrides for Vancouver, Calgary, Edmonton, Montreal, Winnipeg, and Halifax. Each tier carries "
        "a province-correct ltt_region so land-transfer tax (BC PTT, MB LTT, QC/Montreal welcome tax, "
        "AB/SK title fees, NS/NB/PE/NL deed taxes) and the CMHC-premium PST are computed with the right "
        "provincial rules -- the headline correctness fix. NOTE: the appreciation / rent / benchmark_price "
        "macro figures for the non-Ontario tiers are FIRST-PASS researched estimates (med/low confidence, "
        "flagged inline) pending the same Teranet/CREA/CMHC scraper-grounding the Ontario CMAs received; "
        "property-tax rates are city figures to re-verify against current by-laws."
    ),
    "address_geocoding": (
        "Optional 'find by address' form helper geocodes a typed address to its "
        "Canadian postal code via OpenStreetMap Nominatim (https://nominatim.org/); "
        "only the postal code is used, to route regional assumptions. Cached, with "
        "graceful fallback to manual postal entry. See evaluator/geocode.py."
    ),
    "province_ltt": (
        "Land-transfer/property-transfer tax rules per province (see evaluator/tax.py SOURCES): "
        "BC Property Transfer Tax; Manitoba LTT; Quebec transfer duties + Ville de Montreal luxury tiers; "
        "Nova Scotia (Halifax 1.5%), New Brunswick 1%, PEI 1%, Newfoundland ~0.4%; Alberta & Saskatchewan "
        "levy no LTT (nominal land-title registration fees)."
    ),
}
