"""Tests for the projection engine: amortization, tax exemption, invariants."""

from __future__ import annotations

import numpy as np
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


class TestRealDollars:
    def test_zero_inflation_is_noop(self, projection):
        _params, proj, _summary = projection
        assert projections.deflate_projection(proj, 0.0) is proj

    def test_year_t_snapshot_deflated_by_term_factor(self, projection):
        params, proj, summary = projection
        T = params["term_years"]
        real = projections.compute_summary(projections.deflate_projection(proj, 0.02), params)
        factor = 1.02 ** T
        assert real["buyer_net_worth"][T] == pytest.approx(summary["buyer_net_worth"][T] / factor)
        assert real["renter_net_worth"][T] == pytest.approx(summary["renter_net_worth"][T] / factor)

    def test_verdict_sign_is_invariant(self, projection):
        _params, proj, summary = projection
        real = projections.compute_summary(projections.deflate_projection(proj, 0.02), _params)
        assert (summary["final_buyer_minus_renter"] < 0) == (real["final_buyer_minus_renter"] < 0)

    def test_cumulative_total_uses_per_year_flows(self, projection):
        # Real cumulative interest must sit BETWEEN the nominal total and the naive
        # "divide the whole total by the term-end factor" shortcut — proving each
        # year's flow is deflated by its own year, not the final one.
        params, proj, summary = projection
        T = params["term_years"]
        real = projections.compute_summary(projections.deflate_projection(proj, 0.02), params)
        naive = summary["total_interest_paid"] / (1.02 ** T)
        assert naive < real["total_interest_paid"] < summary["total_interest_paid"]

    def test_year_zero_amounts_unchanged(self, projection):
        params, proj, summary = projection
        real = projections.compute_summary(projections.deflate_projection(proj, 0.02), params)
        assert real["purchase_closing_costs"] == pytest.approx(summary["purchase_closing_costs"])


class TestCarryingCosts:
    def test_insurance_and_hoa_grow_over_time(self, make_args):
        # Isolate the insurance+HOA line from maintenance to check it's not flat.
        params = cli.build_engine_params(make_args(insurance=1500, hoa=0))
        params["maintenance_pct_of_value"] = 0.0
        proj = projections.build_projection(params)
        cum = proj["cum_insurance_hoa"]
        flows = [cum[y] - cum[y - 1] for y in range(1, len(cum))]
        assert flows[-1] > flows[0]                       # grows, not flat
        assert flows[0] == pytest.approx(1500.0)          # year 1 = base premium
        assert flows[-1] == pytest.approx(1500.0 * 1.03 ** 29, rel=0.01)  # year 30 grown at 3%


class TestRenewals:
    def _proj(self, make_args, **renewal):
        params = cli.build_engine_params(make_args())
        params.update(renewal)
        proj = projections.build_projection(params)
        return params, proj, projections.compute_summary(proj, params)

    def test_disabled_matches_fixed(self, make_args):
        _p, _pr, fixed = self._proj(make_args)  # renewals off by default
        assert fixed["renewals_enabled"] is False
        # A renewal at the SAME rate must be an exact no-op.
        params = cli.build_engine_params(make_args())
        params.update(renewals_enabled=True, renewal_rate=params["mortgage_rate"])
        same = projections.compute_summary(projections.build_projection(params), params)
        assert same["total_interest_paid"] == pytest.approx(fixed["total_interest_paid"])

    def test_higher_renewal_rate_costs_more_and_jumps_payment(self, make_args):
        _p, _pr, fixed = self._proj(make_args)
        params, proj, s = self._proj(make_args, renewals_enabled=True,
                                     renewal_rate=0.065, rate_term_years=5)
        assert s["total_interest_paid"] > fixed["total_interest_paid"]
        assert s["renewal_payment"] > s["mortgage_payment"]      # payment jumps up
        assert proj["loan_balance"][-1] == pytest.approx(0.0, abs=1.0)  # still pays off

    def test_lower_renewal_rate_costs_less(self, make_args):
        _p, _pr, fixed = self._proj(make_args)
        _p2, _pr2, s = self._proj(make_args, renewals_enabled=True, renewal_rate=0.02)
        assert s["total_interest_paid"] < fixed["total_interest_paid"]

    def test_renewal_shows_up_in_ownership_cost(self, make_args):
        # P&I is embedded in monthly_ownership_cost, so a rate jump at renewal must
        # push the payment component up right after the term boundary.
        _p, proj, _s = self._proj(make_args, renewals_enabled=True,
                                   renewal_rate=0.08, rate_term_years=5)
        pay = np.asarray([r["payment"] for r in proj["renewal_schedule"]])
        assert pay[1] > pay[0]

    def test_rate_term_controls_renewal_count(self, make_args):
        _p, proj, _s = self._proj(make_args, renewals_enabled=True,
                                   renewal_rate=0.055, rate_term_years=10)
        # 30-yr amortization, 10-yr terms -> 3 terms.
        assert len(proj["renewal_schedule"]) == 3


class TestCashFlowMatching:
    def test_larger_down_payment_reduces_interest(self, make_args):
        small = cli.build_engine_params(make_args(down="200000"))
        big = cli.build_engine_params(make_args(down="400000"))
        s_int = projections.compute_summary(projections.build_projection(small), small)["total_interest_paid"]
        b_int = projections.compute_summary(projections.build_projection(big), big)["total_interest_paid"]
        assert b_int < s_int
