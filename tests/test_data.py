"""Tests for regional routing + rent estimation in evaluator.data."""

from __future__ import annotations

import pytest

from evaluator import data, tax


# Required keys every regional tier must carry for the engine + tax layer.
_REQUIRED_KEYS = {
    "home_appreciation_rate", "maintenance_pct_of_value", "current_5yr_fixed_rate",
    "mortgage_rate_30yr_avg", "current_monthly_rent", "benchmark_price",
    "rent_growth_rate", "property_tax_rate", "property_tax_growth_rate",
    "sp500_nominal_cagr", "ltt_region",
}

# postal -> (expected ltt_region, substring expected in the region label)
_ROUTING = [
    ("M2J 0E8", "toronto", "North York"),
    ("M5V 3A8", "toronto", "Toronto"),
    ("K1A 0B1", "ontario", "Ottawa"),
    ("L1H 1A1", "ontario", "Oshawa"),
    ("P3A 1A1", "ontario", "Ontario"),
    ("V6B 1A1", "bc", "Vancouver"),
    ("V8W 1A1", "bc", "British Columbia"),
    ("T2P 1J9", "alberta", "Calgary"),
    ("T5J 1A1", "alberta", "Edmonton"),
    ("T1A 1A1", "alberta", "Alberta"),
    ("H2Y 1A1", "quebec_montreal", "Montreal"),
    ("G1R 1A1", "quebec", "Quebec"),
    ("J4B 1A1", "quebec", "Quebec"),
    ("R3C 1A1", "manitoba", "Winnipeg"),
    ("R7A 1A1", "manitoba", "Manitoba"),
    ("S7K 1A1", "saskatchewan", "Saskatchewan"),
    ("B3J 1A1", "nova_scotia", "Halifax"),
    ("B4V 1A1", "nova_scotia", "Nova Scotia"),
    ("E1C 1A1", "new_brunswick", "New Brunswick"),
    ("C1A 1A1", "pei", "Prince Edward"),
    ("A1C 1A1", "newfoundland", "Newfoundland"),
    ("X1A 1A1", "saskatchewan", "Territories"),  # territories proxy: low title fee
]


@pytest.mark.parametrize("postal,ltt_region,label_sub", _ROUTING)
def test_routing(postal, ltt_region, label_sub):
    p = data.get_params(postal)
    assert p["ltt_region"] == ltt_region
    assert label_sub.lower() in p["_region"].lower()


@pytest.mark.parametrize("postal,_ltt,_lbl", _ROUTING)
def test_every_tier_has_required_keys(postal, _ltt, _lbl):
    p = data.get_params(postal)
    missing = _REQUIRED_KEYS - set(p)
    assert not missing, f"{postal} missing {missing}"


@pytest.mark.parametrize("postal,_ltt,_lbl", _ROUTING)
def test_ltt_region_is_computable(postal, _ltt, _lbl):
    # Every routed ltt_region must be one the tax engine understands (no KeyError,
    # non-negative result).
    p = data.get_params(postal)
    assert tax.land_transfer_tax(700_000, p["ltt_region"]) >= 0.0


def test_get_params_returns_fresh_copy():
    a = data.get_params("M2J 0E8")
    a["home_appreciation_rate"] = 999
    b = data.get_params("M2J 0E8")
    assert b["home_appreciation_rate"] != 999


class TestRentEstimate:
    def test_at_benchmark_returns_base_rent(self):
        params = {"current_monthly_rent": 3000.0, "benchmark_price": 800_000.0}
        assert data.estimate_monthly_rent(params, 800_000.0) == pytest.approx(3000.0)

    def test_sublinear_in_price(self):
        # A home at 2x the benchmark rents for MORE than base but LESS than 2x
        # (gross yields fall as price rises).
        params = {"current_monthly_rent": 3000.0, "benchmark_price": 800_000.0}
        rent = data.estimate_monthly_rent(params, 1_600_000.0)
        assert 3000.0 < rent < 6000.0

    def test_cheaper_home_rents_for_less(self):
        params = {"current_monthly_rent": 3000.0, "benchmark_price": 800_000.0}
        assert data.estimate_monthly_rent(params, 400_000.0) < 3000.0

    def test_falls_back_when_no_benchmark(self):
        params = {"current_monthly_rent": 2500.0, "benchmark_price": 0.0}
        assert data.estimate_monthly_rent(params, 900_000.0) == pytest.approx(2500.0)

    def test_nonpositive_price_returns_base(self):
        params = {"current_monthly_rent": 2500.0, "benchmark_price": 800_000.0}
        assert data.estimate_monthly_rent(params, 0.0) == pytest.approx(2500.0)


def test_fsa_normalizes():
    assert data._fsa("v6b 1a1") == "V6B"
    assert data._fsa("M2J0E8") == "M2J"


def test_sources_are_strings():
    assert all(isinstance(v, str) for v in data.SOURCES.values())
