"""Strategy 2 -- Poisson goals model + value betting + fractional Kelly.

================================  HOW IT WORKS  ============================
Unlike Strategy 1 (which prices probabilities *from* the odds it bets into, and
so can't beat the book), Strategy 2 builds an **independent** forecast from match
results only, then bets a selection *only when its own probability makes the
offered price positive expected value*.

1. RATINGS -- for every league, each team carries a time-decayed home/away
   attack and defence rating, estimated purely from matches played *before* the
   fixture (no look-ahead). Expected goals for a match are

       lambda_home = home.home_scored * away.away_conceded / mu_home
       lambda_away = away.away_scored * home.home_conceded / mu_away

   where mu_home / mu_away are the league's rolling average home / away goals and
   the per-team terms are EWMA (exponentially weighted) goal rates by venue. This
   is the classic Maher (1982) independent-Poisson ratings model, normalised by
   league scoring level.

2. SCORELINE MATRIX -- P(home i, away j) = Poisson(i; lambda_home) *
   Poisson(j; lambda_away), with an optional Dixon-Coles (1997) low-score
   correction. Collapse the matrix to market probabilities: 1X2 (home/draw/away)
   and Over/Under 2.5 total goals.

3. VALUE FILTER -- for each selection compare the model probability p to the
   market's decimal odds o. The expected value of a unit stake is p*o - 1; bet
   only when this exceeds ``min_edge`` (e.g. +5%).

4. STAKING -- fractional Kelly on the current bankroll. Full-Kelly fraction is
   f* = (p*o - 1) / (o - 1); stake ``kelly_frac * f*`` of bankroll, capped at
   ``max_stake_frac``. Bankroll compounds, so the equity curve and drawdown are
   real.

The honest benchmark is betting the closing line of a single book (hard to
beat); betting the best available price (market max) or opening lines is where
positive expected value is easier to realise. See ``scripts/strategy-2.py``.
Refs: Maher (1982); Dixon & Coles (1997); Kelly (1956); Strumbelj (2014).
===========================================================================
"""
from __future__ import annotations

import math

import numpy as np

_MAXG = 10
_FACT = np.array([math.factorial(k) for k in range(_MAXG + 1)], dtype=float)


def poisson_pmf(lam: float) -> np.ndarray:
    """Poisson pmf vector for k = 0.._MAXG."""
    k = np.arange(_MAXG + 1)
    return np.exp(-lam) * lam ** k / _FACT


def scoreline_matrix(lh: float, la: float, rho: float = 0.0) -> np.ndarray:
    """P(home=i, away=j) matrix, optional Dixon-Coles low-score correction."""
    M = np.outer(poisson_pmf(lh), poisson_pmf(la))
    if rho:
        M[0, 0] *= 1 - lh * la * rho
        M[0, 1] *= 1 + lh * rho
        M[1, 0] *= 1 + la * rho
        M[1, 1] *= 1 - rho
        M /= M.sum()
    return M


def market_probs(M: np.ndarray) -> dict:
    """1X2 and Over/Under 2.5 probabilities from a scoreline matrix."""
    n = M.shape[0]
    tot = np.arange(n)[:, None] + np.arange(n)[None, :]
    return {
        "home": float(np.tril(M, -1).sum()),   # home goals > away goals
        "draw": float(np.trace(M)),
        "away": float(np.triu(M, 1).sum()),
        "over25": float(M[tot >= 3].sum()),
        "under25": float(M[tot <= 2].sum()),
    }


def kelly_fraction(p: float, odds: float) -> float:
    """Full-Kelly stake fraction for prob p at decimal odds; 0 if no edge."""
    b = odds - 1.0
    if b <= 0:
        return 0.0
    f = (p * odds - 1.0) / b
    return max(0.0, f)


class LeagueRatings:
    """Rolling, look-ahead-free home/away attack & defence ratings for a league.

    ``expected_goals`` uses only what has been ``update``d so far, so callers must
    predict a fixture *before* feeding it back in.
    """

    def __init__(self, alpha: float = 0.05, min_games: int = 6, shrink: float = 1.0):
        self.a = alpha
        self.min_games = min_games
        self.shrink = shrink        # <1 regresses team ratings toward league mean
        self.muH: float | None = None
        self.muA: float | None = None
        self.t: dict[str, dict] = {}

    def _team(self, name: str) -> dict:
        if name not in self.t:
            self.t[name] = dict(hs=self.muH, hc=self.muA, as_=self.muA,
                                 ac=self.muH, hg=0, ag=0)
        return self.t[name]

    def expected_goals(self, home: str, away: str):
        if self.muH is None or home not in self.t or away not in self.t:
            return None
        h, a = self.t[home], self.t[away]
        if h["hg"] < self.min_games or a["ag"] < self.min_games:
            return None
        s = self.shrink
        atk_h = 1 + s * (h["hs"] / self.muH - 1)     # home team attack (home)
        def_a = 1 + s * (a["ac"] / self.muH - 1)     # away team defence (away)
        atk_a = 1 + s * (a["as_"] / self.muA - 1)    # away team attack (away)
        def_h = 1 + s * (h["hc"] / self.muA - 1)     # home team defence (home)
        lh = self.muH * atk_h * def_a
        la = self.muA * atk_a * def_h
        return float(np.clip(lh, 0.15, 6.0)), float(np.clip(la, 0.15, 6.0))

    def update(self, home: str, away: str, gh: int, ga: int) -> None:
        if self.muH is None:
            self.muH, self.muA = float(gh), float(ga)
        else:
            self.muH += self.a * (gh - self.muH)
            self.muA += self.a * (ga - self.muA)
        h, a = self._team(home), self._team(away)
        h["hs"] += self.a * (gh - h["hs"]); h["hc"] += self.a * (ga - h["hc"]); h["hg"] += 1
        a["as_"] += self.a * (ga - a["as_"]); a["ac"] += self.a * (gh - a["ac"]); a["ag"] += 1
