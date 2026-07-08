"""Strategy 3 -- fitted Dixon-Coles model (weighted MLE).

The Strategy 2 upgrade recommended as lever #1: replace the EWMA goal-rate
heuristic with a proper Dixon-Coles (1997) maximum-likelihood fit.

Model -- each team has an attack and a defence parameter; plus a global home
advantage ``gamma`` and low-score dependence ``rho``:

    log lambda_home = gamma + attack[home] - defence[away]
    log lambda_away =         attack[away] - defence[home]

Home goals ~ Poisson(lambda_home), away ~ Poisson(lambda_away), with the
Dixon-Coles ``tau`` correction on the four low-score cells (which fixes pure
Poisson's under-estimation of draws).

Fitting -- minimise the **time-decayed** negative log-likelihood with an L2
(ridge) penalty on attack/defence. The decay down-weights old matches; the ridge
**shrinks** team ratings toward the league average, which stabilises teams with
little data and gives promoted teams a sensible near-average prior. The model is
refit on a rolling window as the season progresses, always using only matches
*before* the fixture (no look-ahead).

The fitted scoreline matrix yields 1X2 probabilities and the full exact
total-goals distribution (see ``scoreline_matrix`` / ``market_probs`` reused from
Strategy 2, plus ``total_goals_probs`` here).
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from .strategy_2 import market_probs, scoreline_matrix  # noqa: F401  (re-exported)


def total_goals_probs(M: np.ndarray, cap: int = 9) -> np.ndarray:
    """Exact total-goals probabilities 0..cap from a scoreline matrix.

    Index ``cap`` is the tail "cap or more" bucket (so cap=9 -> "9+"), matching
    the sgodds Tg_00..Tg_09 encoding.
    """
    n = M.shape[0]
    tot = np.add.outer(np.arange(n), np.arange(n))
    p = np.array([float(M[tot == k].sum()) for k in range(cap)])
    return np.append(p, float(M[tot >= cap].sum()))


class DixonColes:
    """Rolling Dixon-Coles model: weighted-MLE attack/defence per team plus a
    global home advantage and low-score rho, refit on a time-decayed window.

    Usage per fixture (chronological, no look-ahead): ``fit(train, as_of)`` when
    ``needs_refit(as_of)``, then ``expected_goals(home, away)`` -> feed into
    ``scoreline_matrix`` / ``market_probs`` / ``total_goals_probs``.
    """

    def __init__(self, half_life_days: float = 180, window_days: float = 550,
                 ridge: float = 0.05, fit_every_days: int = 7,
                 min_matches: int = 150):
        self.half_life = half_life_days
        self.window = window_days
        self.ridge = ridge
        self.fit_every = fit_every_days
        self.min_matches = min_matches
        self.att: dict[str, float] = {}
        self.dfn: dict[str, float] = {}
        self.gamma = 0.3
        self.rho = 0.0
        self.fitted = False
        self._last_fit = None

    def needs_refit(self, as_of) -> bool:
        """True if never fitted or the last fit is older than ``fit_every`` days."""
        return self._last_fit is None or (as_of - self._last_fit).days >= self.fit_every

    def _nll(self, theta, hi, ai, x, y, w, T):
        att, dfn = theta[:T], theta[T:2 * T]
        gamma, rho = theta[2 * T], theta[2 * T + 1]
        log_lh = gamma + att[hi] - dfn[ai]
        log_la = att[ai] - dfn[hi]
        lh, la = np.exp(log_lh), np.exp(log_la)
        ll = x * log_lh - lh + y * log_la - la          # Poisson (drop factorials)
        tau = np.ones_like(lh)
        tau = np.where((x == 0) & (y == 0), 1 - lh * la * rho, tau)
        tau = np.where((x == 0) & (y == 1), 1 + lh * rho, tau)
        tau = np.where((x == 1) & (y == 0), 1 + la * rho, tau)
        tau = np.where((x == 1) & (y == 1), 1 - rho, tau)
        ll = ll + np.log(np.clip(tau, 1e-6, None))
        return -np.sum(w * ll) + self.ridge * (np.sum(att ** 2) + np.sum(dfn ** 2))

    def fit(self, train, as_of) -> bool:
        """``train`` has columns home, away, fthg, ftag, date (datetime64)."""
        tr = train[train["date"] < as_of]
        if self.window:
            tr = tr[tr["date"] >= as_of - np.timedelta64(int(self.window), "D")]
        if len(tr) < self.min_matches:
            return False
        names = sorted(set(tr["home"]) | set(tr["away"]))
        idx = {n: i for i, n in enumerate(names)}
        T = len(names)
        hi = tr["home"].map(idx).to_numpy()
        ai = tr["away"].map(idx).to_numpy()
        x = tr["fthg"].to_numpy(float)
        y = tr["ftag"].to_numpy(float)
        age = (as_of - tr["date"]).dt.days.to_numpy(float)
        w = np.exp(-np.log(2) * age / self.half_life)

        x0 = np.zeros(2 * T + 2)
        x0[2 * T] = self.gamma if self.fitted else 0.3
        x0[2 * T + 1] = self.rho
        if self.fitted:                                  # warm start from last fit
            for n, i in idx.items():
                x0[i] = self.att.get(n, 0.0)
                x0[T + i] = self.dfn.get(n, 0.0)
        bounds = [(-3, 3)] * (2 * T) + [(-1.0, 1.0), (-0.2, 0.2)]
        res = minimize(self._nll, x0, args=(hi, ai, x, y, w, T),
                       method="L-BFGS-B", bounds=bounds, options={"maxiter": 200})
        th = res.x
        self.att = {n: float(th[i]) for n, i in idx.items()}
        self.dfn = {n: float(th[T + i]) for n, i in idx.items()}
        self.gamma = float(th[2 * T])
        self.rho = float(th[2 * T + 1])
        self.fitted = True
        self._last_fit = as_of
        return True

    def expected_goals(self, home: str, away: str):
        """(lambda_home, lambda_away); unknown teams default to average (0)."""
        if not self.fitted:
            return None
        ah, dh = self.att.get(home, 0.0), self.dfn.get(home, 0.0)
        aa, da = self.att.get(away, 0.0), self.dfn.get(away, 0.0)
        lh = np.exp(self.gamma + ah - da)
        la = np.exp(aa - dh)
        return float(np.clip(lh, 0.15, 6.0)), float(np.clip(la, 0.15, 6.0))
