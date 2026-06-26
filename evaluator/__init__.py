"""Canadian buy-vs-rent home evaluator.

A reusable pipeline that turns four inputs — house price, down payment,
mortgage term, and a Canadian postal code — into four financial-comparison
charts plus an executive summary.

Pipeline (one layer per project agent):
  data.py         regional + national assumptions   (canada-housing-financial-scraper)
  projections.py  year/month projection engine       (future-projection-analyst)
  charts.py       four-chart renderer                 (mortgage-investment-charter)
  cli.py          orchestrates the above
"""

__all__ = ["data", "projections", "charts", "cli"]
