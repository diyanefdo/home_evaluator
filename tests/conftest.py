"""Shared pytest fixtures for the evaluator engine tests."""

from __future__ import annotations

import argparse

import pytest


# Defaults mirror evaluator.cli.build_arg_parser + webapp's Namespace so
# build_engine_params has every attribute it reads.
_ARG_DEFAULTS = dict(
    price=1_000_000, down="200000", years=30, postal="M2J 0E8",
    age=35, income=120_000, account_strategy="shelter-first", retirement_rate=None,
    first_time_buyer=False, commission_rate=None, purchase_legal=None,
    no_transaction_costs=False, live=False,
    rate=None, appreciation=None, rent=None, rent_growth=None,
    property_tax_rate=None, investment_return=None, insurance=1500.0, hoa=0.0,
    out=None, no_charts=True,
)


@pytest.fixture
def make_args():
    """Return a factory that builds a CLI args Namespace with overrides."""
    def _make(**overrides):
        return argparse.Namespace(**{**_ARG_DEFAULTS, **overrides})
    return _make
