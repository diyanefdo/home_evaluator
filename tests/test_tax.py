"""Tests for the tax layer: land-transfer tax, CMHC insurance, income-tax room.

The land-transfer values are checked against the legislated marginal schedules
(the whole point of the Phase 3 province work), so a bracket edit that changes a
number will fail loudly here.
"""

from __future__ import annotations

import math

import pytest

from evaluator import tax


# --------------------------------------------------------------------------- #
# Land-transfer / property-transfer tax
# --------------------------------------------------------------------------- #
class TestLandTransferTax:
    def test_ontario_1m(self):
        # 0.5%*55k + 1%*195k + 1.5%*150k + 2%*1.6M-portion... = 16,475 on $1M.
        assert tax.land_transfer_tax(1_000_000, "ontario") == pytest.approx(16_475.0)

    def test_toronto_doubles_ontario(self):
        # Toronto adds a municipal LTT mirroring the provincial one.
        assert tax.land_transfer_tax(1_000_000, "toronto") == pytest.approx(32_950.0)

    def test_toronto_first_time_rebate(self):
        # $4,000 (ON) + $4,475 (Toronto) off the $32,950.
        assert tax.land_transfer_tax(1_000_000, "toronto", first_time=True) == pytest.approx(24_475.0)

    def test_bc_ptt(self):
        # 1%*200k + 2%*800k = 18,000 on $1M.
        assert tax.land_transfer_tax(1_000_000, "bc") == pytest.approx(18_000.0)

    def test_bc_luxury_over_3m(self):
        # 2000 + 36000 + 30000 + 5%*500k = 93,000 on $3.5M.
        assert tax.land_transfer_tax(3_500_000, "bc") == pytest.approx(93_000.0)

    def test_bc_first_time_exemption_below_threshold(self):
        # <=$835k: exemption = PTT on the first $500k ($8,000). Full PTT on $700k
        # is 2000 + 2%*500k = 12,000; net 4,000.
        assert tax.land_transfer_tax(700_000, "bc", first_time=True) == pytest.approx(4_000.0)

    def test_bc_first_time_no_exemption_above_threshold(self):
        # Above $835k the exemption does not apply.
        assert tax.land_transfer_tax(900_000, "bc", first_time=True) == \
            tax.land_transfer_tax(900_000, "bc", first_time=False)

    def test_quebec_base(self):
        # 0.5%*58,900 + 1%*(294,600-58,900) + 1.5%*(500k-294,600).
        assert tax.land_transfer_tax(500_000, "quebec") == pytest.approx(5_732.5)

    def test_montreal_higher_than_base_quebec(self):
        # Montreal's luxury tiers make it strictly costlier above $500k.
        assert tax.land_transfer_tax(1_500_000, "quebec_montreal") > \
            tax.land_transfer_tax(1_500_000, "quebec")

    def test_manitoba(self):
        # nil*30k + 0.5%*60k + 1%*60k + 1.5%*50k + 2%*200k = 5,650 on $400k.
        assert tax.land_transfer_tax(400_000, "manitoba") == pytest.approx(5_650.0)

    def test_saskatchewan_flat_title_fee(self):
        assert tax.land_transfer_tax(700_000, "saskatchewan") == pytest.approx(2_100.0)

    def test_nova_scotia_flat(self):
        assert tax.land_transfer_tax(700_000, "nova_scotia") == pytest.approx(10_500.0)

    def test_new_brunswick_flat(self):
        assert tax.land_transfer_tax(700_000, "new_brunswick") == pytest.approx(7_000.0)

    def test_alberta_has_no_ltt_only_title_fee(self):
        # $50 + $5 per $5,000 of value; tiny relative to a real LTT.
        expected = 50.0 + 5.0 * math.ceil(700_000 / 5_000.0)
        assert tax.land_transfer_tax(700_000, "alberta") == pytest.approx(expected)
        assert tax.land_transfer_tax(700_000, "alberta") < 1_000

    def test_alberta_ignores_first_time_flag(self):
        assert tax.land_transfer_tax(700_000, "alberta", first_time=True) == \
            tax.land_transfer_tax(700_000, "alberta", first_time=False)

    def test_unknown_region_falls_back_to_ontario(self):
        assert tax.land_transfer_tax(1_000_000, "atlantis") == \
            tax.land_transfer_tax(1_000_000, "ontario")

    def test_monotonic_in_price(self):
        for region in ("ontario", "bc", "quebec", "manitoba", "nova_scotia"):
            a = tax.land_transfer_tax(400_000, region)
            b = tax.land_transfer_tax(800_000, region)
            assert b >= a, region


# --------------------------------------------------------------------------- #
# CMHC mortgage default insurance
# --------------------------------------------------------------------------- #
class TestCMHC:
    def test_not_required_at_20_percent_down(self):
        out = tax.cmhc_insurance(500_000, 100_000)
        assert out["required"] is False
        assert out["premium"] == 0.0 and out["pst"] == 0.0

    def test_high_ratio_premium_and_pst_ontario(self):
        # 5% down on $500k -> 95% LTV -> 4.00% premium on the $475k loan.
        out = tax.cmhc_insurance(500_000, 25_000, "ontario")
        assert out["required"] is True and out["insurable"] is True
        assert out["rate"] == pytest.approx(0.04)
        assert out["premium"] == pytest.approx(19_000.0)
        assert out["pst"] == pytest.approx(1_520.0)  # 8% ON PST on the premium

    @pytest.mark.parametrize("region,pst_rate", [
        ("quebec", 0.09975), ("saskatchewan", 0.06), ("manitoba", 0.08),
        ("bc", 0.0), ("alberta", 0.0), ("nova_scotia", 0.0),
    ])
    def test_pst_rate_by_province(self, region, pst_rate):
        out = tax.cmhc_insurance(500_000, 25_000, region)
        assert out["pst_rate"] == pytest.approx(pst_rate)
        assert out["pst"] == pytest.approx(out["premium"] * pst_rate)

    def test_uninsurable_over_1_5m(self):
        out = tax.cmhc_insurance(1_600_000, 100_000)  # 6.25% down, but price too high
        assert out["required"] is True and out["insurable"] is False
        assert out["reason"]

    def test_uninsurable_below_min_down(self):
        # $600k min down = 5%*500k + 10%*100k = $35k; $30k is below it.
        out = tax.cmhc_insurance(600_000, 30_000)
        assert out["insurable"] is False

    def test_min_down_payment_schedule(self):
        assert tax.cmhc_min_down_payment(400_000) == pytest.approx(20_000.0)
        assert tax.cmhc_min_down_payment(600_000) == pytest.approx(35_000.0)
        assert tax.cmhc_min_down_payment(1_600_000) == pytest.approx(320_000.0)  # 20%


# --------------------------------------------------------------------------- #
# Income tax: marginal rate, TFSA / RRSP room
# --------------------------------------------------------------------------- #
class TestIncomeTax:
    def test_marginal_rate_monotonic(self):
        rates = [tax.marginal_rate(i) for i in range(0, 300_000, 10_000)]
        assert rates == sorted(rates)

    def test_marginal_rate_known_points(self):
        assert tax.marginal_rate(0) == pytest.approx(0.2005)
        assert tax.marginal_rate(120_000) == pytest.approx(0.4341)

    def test_rrsp_room_capped(self):
        assert tax.rrsp_annual_room(100_000) == pytest.approx(18_000.0)
        assert tax.rrsp_annual_room(1_000_000) == pytest.approx(tax.RRSP_ANNUAL_DOLLAR_CAP)

    def test_tfsa_room_accumulates(self):
        assert tax.tfsa_cumulative_room(35, current_year=2026) == pytest.approx(109_000.0)

    def test_tfsa_room_zero_under_18(self):
        assert tax.tfsa_cumulative_room(16) == 0.0

    def test_sources_are_strings(self):
        assert all(isinstance(v, str) for v in tax.SOURCES.values())


# --------------------------------------------------------------------------- #
# GST/HST New Housing Rebate (new-build homes)
# --------------------------------------------------------------------------- #
class TestGstHstNewHousing:
    def test_resale_is_exempt(self):
        # A resale home (new_build False) owes no GST/HST regardless of region.
        d = tax.gst_hst_new_housing(1_000_000, "toronto", first_time=True, new_build=False)
        assert d["applies"] is False
        assert d["total"] == 0.0

    def test_ontario_new_build_not_first_time(self):
        # $1M new build in Ontario: full 5% GST (no federal rebate above $450k) +
        # 8% provincial less the $24k-capped 75% rebate = $50k + $56k = $106k.
        d = tax.gst_hst_new_housing(1_000_000, "toronto", first_time=False, new_build=True)
        assert d["gst_net"] == pytest.approx(50_000.0)
        assert d["prov_net"] == pytest.approx(56_000.0)
        assert d["total"] == pytest.approx(106_000.0)

    def test_first_time_gst_rebate_waives_federal_gst_under_1m(self):
        # The 2025 FTB GST rebate zeroes the 5% GST on a <=$1M new build.
        d = tax.gst_hst_new_housing(1_000_000, "ontario", first_time=True, new_build=True)
        assert d["gst_net"] == pytest.approx(0.0)
        assert d["prov_net"] == pytest.approx(56_000.0)

    def test_first_time_gst_rebate_phases_out_by_1_5m(self):
        # At $1.25M the FTB rebate is half phased out -> half the GST remains.
        d = tax.gst_hst_new_housing(1_250_000, "ontario", first_time=True, new_build=True)
        assert d["gst_net"] == pytest.approx(0.5 * 0.05 * 1_250_000)

    def test_federal_rebate_phases_out_between_350k_and_450k(self):
        below = tax.gst_hst_new_housing(350_000, "bc", first_time=False, new_build=True)
        assert below["gst_net"] == pytest.approx(0.05 * 350_000 * (1 - 0.36))
        above = tax.gst_hst_new_housing(450_000, "bc", first_time=False, new_build=True)
        assert above["gst_net"] == pytest.approx(0.05 * 450_000)  # no rebate left

    def test_gst_only_province_has_no_provincial_portion(self):
        # BC (and AB/SK/MB) are GST-only: no provincial HST rebate line.
        d = tax.gst_hst_new_housing(800_000, "bc", first_time=False, new_build=True)
        assert d["prov_net"] == 0.0
        assert d["total"] == pytest.approx(d["gst_net"])

    def test_ontario_provincial_rebate_is_capped_at_24k(self):
        # Provincial rebate maxes at $24k (reached by a $400k provincial base).
        d = tax.gst_hst_new_housing(2_000_000, "toronto", first_time=False, new_build=True)
        assert d["prov_net"] == pytest.approx(0.08 * 2_000_000 - 24_000.0)

    def test_closing_costs_include_new_build_gst(self):
        resale = tax.purchase_closing_costs(1_000_000, "toronto", False, new_build=False)
        newb = tax.purchase_closing_costs(1_000_000, "toronto", False, new_build=True)
        assert newb - resale == pytest.approx(106_000.0)


class TestOntario2026Rebate:
    def test_full_13_percent_waived_under_1m_for_all_buyers(self):
        # Not first-time, $1M new build, ON 2026 window -> full 13% HST rebated -> $0.
        d = tax.gst_hst_new_housing(1_000_000, "toronto", first_time=False,
                                    new_build=True, on2026=True)
        assert d["on2026"] is True
        assert d["total"] == pytest.approx(0.0)

    def test_flat_130k_rebate_between_1m_and_1_5m(self):
        # $1.25M: 13% HST = $162,500, flat $130k rebate -> $32,500 net.
        d = tax.gst_hst_new_housing(1_250_000, "ontario", first_time=False,
                                    new_build=True, on2026=True)
        assert d["total"] == pytest.approx(0.13 * 1_250_000 - 130_000)

    def test_declines_to_24k_by_1_85m(self):
        d = tax.gst_hst_new_housing(1_850_000, "toronto", first_time=False,
                                    new_build=True, on2026=True)
        assert d["total"] == pytest.approx(0.13 * 1_850_000 - 24_000)

    def test_only_applies_in_ontario(self):
        # A GST-only province ignores the Ontario program (falls back to federal).
        d = tax.gst_hst_new_housing(1_000_000, "bc", first_time=False,
                                    new_build=True, on2026=True)
        assert d["on2026"] is False
        assert d["total"] == pytest.approx(50_000.0)

    def test_no_effect_on_resale(self):
        d = tax.gst_hst_new_housing(1_000_000, "toronto", first_time=True,
                                    new_build=False, on2026=True)
        assert d["applies"] is False
        assert d["total"] == 0.0

    def test_more_generous_than_permanent_rules(self):
        permanent = tax.gst_hst_new_housing(1_000_000, "toronto", first_time=True, new_build=True)
        window = tax.gst_hst_new_housing(1_000_000, "toronto", first_time=True,
                                         new_build=True, on2026=True)
        assert window["total"] <= permanent["total"]
