"""Strategy 1 -- flat-stake "favourite / total-goals coverage" betting engine.

================================  HOW IT WORKS  ============================
Strategy 1 turns a bookmaker's own prices into probabilities and bets the
outcomes the market itself rates as most likely. It never uses an independent
model -- the edge (if any) comes purely from *which* high-probability outcomes
it backs and how it stakes them.

For a match, take the decimal odds for every outcome in a market and convert
them to **de-vigged implied probabilities**, i.e. strip the bookmaker's margin
by normalising the inverse odds so they sum to 1:

        p_i = (1 / odds_i) / Sigma_j (1 / odds_j)

Two markets are played, each an independent leg:

* **1X2 (match result: Home / Draw / Away)**
      Bet $1 on the single most likely outcome *only if* its probability
      exceeds a threshold (default 70%). Most matches have no 70% favourite and
      so get no 1X2 bet -- the leg only fires on strong favourites.

* **TG (exact full-time total goals: 0,1,2,...,9 where 9 means "9 or more")**
      Bet $1 on each of the ``top_n`` most probable goal totals (default 2 --
      the Strategy 1 rule; almost always the "1 goal" and "2 goals" buckets).
      A legacy "coverage" mode (``top_n=None``) instead keeps adding the next
      most likely total until the covered probability exceeds ``threshold``.

**Settlement.** Bets settle at the *same* odds used to derive the probabilities.
Decimal-odds convention: a winning $1 bet returns ``odds`` (stake included), so
its profit is ``odds - 1``; a losing bet returns 0 (profit ``-1``). For TG, the
match's actual total goals (capped at 9) either matches one bought bucket (that
$1 wins, the others lose) or none (all lose).

**Staking.** Flat $1 per bet -- deliberately simple so the P&L reflects the
selection rule, not a staking scheme.

**Note on expected value.** Because bets settle at the same prices used to price
them, every bet's expected value is negative by exactly the bookmaker's margin
(overround). 1X2 margins are ~5-7%; exact-total-goals margins are much larger
(~25-30%). So this engine measures the strategy's *realised* edge against those
margins, not a mispricing model. See ``scripts/strategy-1.py`` for the weekly
game-selection wrapper and ``scripts/strategy-1-markets.py`` for the broad
cross-source/market comparison.
===========================================================================
"""
from __future__ import annotations

import pandas as pd


def implied_probs(odds: pd.Series | pd.DataFrame):
    """De-vigged implied probabilities from decimal odds along the last axis."""
    inv = 1.0 / odds
    if isinstance(odds, pd.DataFrame):
        return inv.div(inv.sum(axis=1), axis=0)
    return inv / inv.sum()


def _played(matches: pd.DataFrame, source: str) -> pd.DataFrame:
    m = matches[matches.source == source]
    m = m[m.fthg.notna() & m.ftag.notna() & m.ftr.notna()]
    return m.set_index("match_key")


def _pivot(odds, market, source, bookmaker, phase):
    o = odds[(odds.market == market) & (odds.source == source) &
             (odds.bookmaker == bookmaker) & (odds.phase == phase)]
    if o.empty:
        return o
    return o.pivot_table(index="match_key", columns="selection",
                         values="odds", aggfunc="first")


def _summary(market, source, book, phase, thr, res, considered) -> dict:
    base = dict(market=market, source=source, book=book, phase=phase,
                threshold=thr, considered=considered, matches_bet=0,
                stakes=0, staked=0.0, returned=0.0, profit=0.0,
                roi=0.0, hit_rate=0.0)
    if res is None or res.empty:
        return base
    staked = float(res.staked.sum())
    returned = float(res.returned.sum())
    base.update(matches_bet=len(res),
                stakes=int(res.staked.sum()),  # flat $1 -> #stakes == $ staked
                staked=staked, returned=returned, profit=returned - staked,
                roi=(returned - staked) / staked if staked else 0.0,
                hit_rate=float(res.win.mean()))
    return base


def backtest_1x2(matches, odds, source, bookmaker, phase,
                 threshold=0.70, stake=1.0):
    """1X2 leg: stake $1 on the top outcome iff its de-vigged prob > ``threshold``."""
    piv = _pivot(odds, "1X2", source, bookmaker, phase)
    if len(piv) == 0 or not {"H", "D", "A"}.issubset(piv.columns):
        return pd.DataFrame(), _summary("1X2", source, bookmaker, phase, threshold, None, 0)
    piv = piv[["H", "D", "A"]].dropna()
    probs = implied_probs(piv)
    pmax = probs.max(axis=1)
    pick = probs.idxmax(axis=1)
    qual = pmax[pmax > threshold].index          # only strong favourites qualify

    m = _played(matches, source)
    rows = []
    for mk in qual:
        if mk not in m.index:
            continue
        sel = pick[mk]
        od = float(piv.at[mk, sel])
        win = bool(m.at[mk, "ftr"] == sel)
        rows.append((mk, sel, float(pmax[mk]), od, win,
                     stake, od * stake if win else 0.0))
    res = pd.DataFrame(rows, columns=["match_key", "pick", "p", "odds",
                                      "win", "staked", "returned"])
    if not res.empty:
        res["profit"] = res.returned - res.staked
    return res, _summary("1X2", source, bookmaker, phase, threshold, res, len(piv))


def backtest_tg(matches, odds, source, bookmaker, phase,
                top_n=2, threshold=0.70, stake=1.0):
    """TG leg (exact total goals).

    ``top_n`` set (default 2 -- the Strategy 1 rule): stake $1 on each of the
    ``top_n`` most probable goal totals. ``top_n=None``: legacy coverage mode --
    keep adding the next most likely total until covered prob exceeds
    ``threshold``.
    """
    piv = _pivot(odds, "TG", source, bookmaker, phase)
    cols = [str(g) for g in range(10)
            if len(piv) and str(g) in piv.columns]
    need = top_n if top_n is not None else 2
    if len(piv) == 0 or len(cols) < need:
        return pd.DataFrame(), _summary("TG", source, bookmaker, phase, threshold, None, 0)
    piv = piv[cols].dropna()
    m = _played(matches, source)

    rows = []
    for mk, row in piv.iterrows():
        if mk not in m.index:
            continue
        bucket = min(int(m.at[mk, "fthg"] + m.at[mk, "ftag"]), 9)
        prob = implied_probs(row).sort_values(ascending=False)
        if top_n is not None:
            sel = list(prob.index[:top_n])
        else:
            cum, sel = 0.0, []
            for g, p in prob.items():
                sel.append(g)
                cum += p
                if cum > threshold:
                    break
        covered = float(prob.loc[sel].sum())     # P(one of the bought totals hits)
        win = str(bucket) in sel
        returned = float(row[str(bucket)]) * stake if win else 0.0
        rows.append((mk, len(sel), bucket, win, stake * len(sel), returned, covered))
    res = pd.DataFrame(rows, columns=["match_key", "k", "bucket", "win",
                                      "staked", "returned", "covered_p"])
    if not res.empty:
        res["profit"] = res.returned - res.staked
    return res, _summary("TG", source, bookmaker, phase, threshold, res, len(piv))


def equity_curve(res: pd.DataFrame, matches: pd.DataFrame, source: str) -> pd.DataFrame:
    """Chronological cumulative profit for a leg's per-bet results."""
    if res.empty:
        return pd.DataFrame(columns=["date", "cum_profit"])
    d = matches[matches.source == source][["match_key", "date"]]
    r = res.merge(d, on="match_key", how="left").sort_values("date")
    r["cum_profit"] = r["profit"].cumsum()
    return r[["date", "profit", "cum_profit"]]
