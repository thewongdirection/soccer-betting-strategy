"""Soccer betting data pipeline: enumerate + pull historical odds and outcomes.

Two data sources, normalized into one source-agnostic schema:

* ``footballdata`` -- football-data.co.uk. Deep history (2000/01 -> current),
  many European bookmakers (Bet365, Pinnacle, ...), opening + closing odds,
  1X2 / Over-Under / Asian-handicap, plus final scores.
* ``sgodds`` -- sgodds.com. Singapore Pools *opening* odds (1X2, O/U, AH, and
  many exotic markets) plus HT/FT scores. Shallow history (~2025-onward).

The pull produces two tidy tables (see :mod:`soccer_backtest.schema`):

* ``matches`` -- one row per fixture: identity + final/half-time scores.
* ``odds``    -- long format: one row per (match, source, bookmaker, market,
  selection, phase). Extensible to any market without schema changes.
"""

__version__ = "0.1.0"
