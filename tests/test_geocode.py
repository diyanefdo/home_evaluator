"""Tests for the geocoder's pure helpers (no network calls)."""

from __future__ import annotations

from evaluator import geocode


class TestNormalizePostal:
    def test_inserts_single_space(self):
        assert geocode._normalize_postal("v6b1a1") == "V6B 1A1"

    def test_collapses_whitespace_and_uppercases(self):
        assert geocode._normalize_postal(" v6b  1a1 ") == "V6B 1A1"

    def test_leaves_partial_untouched(self):
        assert geocode._normalize_postal("v6b") == "V6B"


class TestExtract:
    def test_pulls_postal_from_structured_address(self):
        hits = [{
            "address": {"postcode": "M5V 3L9", "city": "Toronto", "state": "Ontario"},
            "display_name": "CN Tower, Toronto, Ontario, Canada",
            "lat": "43.64", "lon": "-79.38",
        }]
        out = geocode._extract(hits, "290 Bremner Blvd, Toronto")
        assert out["postal"] == "M5V 3L9"
        assert out["fsa"] == "M5V"
        assert out["city"] == "Toronto"
        assert out["province"] == "Ontario"
        assert out["lat"] == 43.64 and out["lon"] == -79.38

    def test_falls_back_to_postal_in_query(self):
        # No structured postcode, but the user typed one.
        hits = [{"address": {"city": "Vancouver"}, "display_name": "x"}]
        out = geocode._extract(hits, "some place V6B 1A1")
        assert out["postal"] == "V6B 1A1"

    def test_none_when_no_postal_anywhere(self):
        hits = [{"address": {"city": "Nowhere"}, "display_name": "x"}]
        assert geocode._extract(hits, "no postal here") is None

    def test_none_on_empty_hits(self):
        assert geocode._extract([], "V6B 1A1 present") is not None  # postal from query
        assert geocode._extract([], "nothing") is None

    def test_city_falls_back_through_admin_levels(self):
        hits = [{"address": {"postcode": "K7L 1A1", "town": "Kingston"}, "display_name": "x"}]
        assert geocode._extract(hits, "q")["city"] == "Kingston"


class TestPostalRegex:
    def test_matches_with_and_without_space(self):
        assert geocode._POSTAL_RE.search("m5v 3l9")
        assert geocode._POSTAL_RE.search("M5V3L9")

    def test_rejects_non_postal(self):
        assert geocode._POSTAL_RE.search("12345") is None


def test_short_query_returns_none_without_network():
    # Guard clause returns before any HTTP call.
    assert geocode.geocode_address("ab") is None
    assert geocode.geocode_address("") is None
