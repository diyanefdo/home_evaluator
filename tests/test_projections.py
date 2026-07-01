"""Tests for the projection engine: amortization, tax exemption, invariants."""

from __future__ import annotations

import pytest

from evaluator import cli, projections


@pytest.fixture
def projection(make_args):
    params = cli.build_engine_params(make_args())
    proj = projections.build_projection(params)
    summary = projections.compute_summary(proj, params)
    return params, proj, summary


_SUMMARY_KEYS = {
    "crossover_year", "buyer_net_worth", "renter_net_worth",
    "final_buyer_minus_renter", "buyer_tax_paid", "renter_tax_paid",
    "total_cost_of_ownership", "total_interest_paid", "purchase_closing_costs",
}


class TestAmortization:
    def test_loan_pays_off_to_zero(self, projection):
        _params, proj, _summary = projection
        assert proj["loan_balance"][-1] == pytest.approx(0.0, abs=1.0)

    def test_balance_is_monotonic_non_increasing(self, projection):
        _params, proj, _summary = projection
        bal = proj["loan_balance"]
        assert all(bal[i + 1] <= bal[i] + 1e-6 for i in range(len(bal) - 1))

    def test_home_value_appreciates(self, projection):
        params, proj, _summary = projection
        assert proj["home_value"][-1] > params["purchase_price"]

    def test_zero_rate_loan_amortizes(self, make_args):
        # Guard path: a 0% mortgage must not divide by zero and still pays off.
        params = cli.build_engine_params(make_args(rate=0.0))
        proj = projections.build_projection(params)
        assert proj["loan_balance"][-1] == pytest.approx(0.0, abs=1.0)


class TestSummary:
    def test_has_expected_keys(self, projection):
        _params, _proj, summary = projection
        assert _SUMMARY_KEYS <= set(summary)

    def test_principal_residence_is_tax_free(self, projection):
        # The owner's home equity carries no capital-gains tax at sale.
        _params, _proj, summary = projection
        assert summary["buyer_tax_paid"] == pytest.approx(0.0)

    def test_renter_taxable_gains_are_taxed(self, projection):
        # A large shelter-first portfolio still overflows to a taxed account.
        _params, _proj, summary = projection
        assert summary["renter_tax_paid"] > 0

    def test_costs_are_positive(self, projection):
        _params, _proj, summary = projection
        assert summary["total_cost_of_ownership"] > 0
        assert summary["total_interest_paid"] > 0


class TestCashFlowMatching:
    def test_larger_down_payment_reduces_interest(self, make_args):
        small = cli.build_engine_params(make_args(down="200000"))
        big = cli.build_engine_params(make_args(down="400000"))
        s_int = projections.compute_summary(projections.build_projection(small), small)["total_interest_paid"]
        b_int = projections.compute_summary(projections.build_projection(big), big)["total_interest_paid"]
        assert b_int < s_int
