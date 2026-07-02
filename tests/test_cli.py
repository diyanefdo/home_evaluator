"""Tests for CLI input parsing and the param-building choke point."""

from __future__ import annotations

import pytest

from evaluator import cli


class TestParseDownPayment:
    def test_plain_number(self):
        assert cli._parse_down_payment("200000", 1_000_000) == 200_000.0

    def test_dollar_and_commas(self):
        assert cli._parse_down_payment("$200,000", 1_000_000) == 200_000.0

    def test_percent(self):
        assert cli._parse_down_payment("20%", 1_000_000) == 200_000.0

    def test_percent_fractional(self):
        assert cli._parse_down_payment("6.25%", 800_000) == 50_000.0


class TestBuildEngineParams:
    def test_rent_is_price_derived(self, make_args):
        # No --rent: rent comes from estimate_monthly_rent for the region/price.
        params = cli.build_engine_params(make_args(price=1_000_000, postal="M2J 0E8"))
        assert params["rent_monthly"] > 0
        # A pricier home in the same region gets a higher comparable rent.
        cheaper = cli.build_engine_params(make_args(price=700_000, postal="M2J 0E8"))
        assert params["rent_monthly"] > cheaper["rent_monthly"]

    def test_explicit_rent_overrides(self, make_args):
        params = cli.build_engine_params(make_args(rent=4321))
        assert params["rent_monthly"] == pytest.approx(4321.0)

    def test_non_ontario_ltt_region_flows(self, make_args):
        params = cli.build_engine_params(make_args(postal="V6B 1A1"))
        assert params["ltt_region"] == "bc"
        # BC PTT on a $1M home, plus legal, with no transaction-cost suppression.
        assert params["purchase_closing_costs"] > 0

    def test_alberta_closing_costs_are_small(self, make_args):
        # Alberta has no LTT, so closing costs are basically the legal fee.
        params = cli.build_engine_params(make_args(postal="T2P 1J9"))
        assert params["ltt_region"] == "alberta"
        assert params["purchase_closing_costs"] < 5_000

    def test_high_ratio_down_finances_cmhc_premium(self, make_args):
        params = cli.build_engine_params(make_args(price=500_000, down="25000"))  # 5% down
        assert params["cmhc"]["required"] is True
        assert params["cmhc_premium"] > 0

    def test_no_transaction_costs_zeroes_closing(self, make_args):
        params = cli.build_engine_params(make_args(no_transaction_costs=True))
        assert params["purchase_closing_costs"] == 0.0

    def test_uninsurable_low_down_raises(self, make_args):
        with pytest.raises(SystemExit):
            cli.build_engine_params(make_args(price=2_000_000, down="100000"))  # 5% on >$1.5M


class TestMonteCarloCli:
    _BASE = ["--price", "1000000", "--down", "20%", "--years", "30", "--postal", "M2J 0E8"]

    def test_monte_carlo_mode_runs_and_prints(self, capsys):
        rc = cli.main(self._BASE + ["--monte-carlo", "150", "--mc-seed", "1", "--no-charts"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "MONTE CARLO SIMULATION" in out
        assert "P(buying wins)" in out

    def test_monte_carlo_bare_flag_uses_default_sims(self):
        # `--monte-carlo` with no value should fall back to the default sim count.
        args = cli.build_arg_parser().parse_args(self._BASE + ["--monte-carlo"])
        assert args.monte_carlo == cli.projections.DEFAULT_MC_SIMS

    def test_no_flag_leaves_monte_carlo_none(self):
        args = cli.build_arg_parser().parse_args(self._BASE)
        assert args.monte_carlo is None

    def test_signed_money_tags_leader(self):
        assert "buyer" in cli._signed_money(1000.0)
        assert "renter" in cli._signed_money(-1000.0)
