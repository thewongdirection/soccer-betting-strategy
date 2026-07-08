"""Strategy 1 -- weekly EPL backtest (last 2 seasons, $1,000 bankroll).

WHAT THIS RUNS
--------------
The Strategy 1 betting rules (see ``soccer_backtest/strategy_1.py`` for the full
description) applied to English Premier League matches, with a weekly
game-selection wrapper and bankroll tracking:

  Per match, using de-vigged implied probs p_i = (1/odds_i) / sum_j(1/odds_j):
    * 1X2  -> candidate bet: $1 on the top outcome IFF its prob > 70%.
    * TG   -> candidate bet: $1 on each of the 2 most probable exact totals.

  Selection: within each ISO calendar week, rank the candidate matches by
  "confidence" (the highest qualifying probability available on the match) and
  bet only the top ``MAX_GAMES_PER_WEEK`` (5) games. On each chosen game place
  every qualifying leg. Flat $1 per bet. Bankroll starts at $1,000; drawdown is
  measured on the running per-bet equity in match-date order.

DATA
----
  * 1X2 odds  -> football-data, Bet365, closing price (both seasons).
  * TG  odds  -> Singapore Pools / sgodds, joined to the football-data fixture
                 by ``fixture_key`` (available for 2025-26 only; no exact-total-
                 goals data exists anywhere for 2024-25, so that season is
                 1X2-only).
  * Scores/results come from football-data.

OUTPUT (data/processed/, git-ignored)
  * strategy-1-bet-log.csv     -- every bet: teams, score, category, selection,
                                  est. prob, odds, stake, W/L, profit, bankroll.
  * strategy-1-weekly-pnl.csv  -- per-week games/bets/staked/P&L/bankroll.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soccer_backtest import config, strategy_1 as engine  # noqa: E402

SEASONS = ["2024-2025", "2025-2026"]
START_CAPITAL = 1000.0
STAKE = 1.0
THRESHOLD = 0.70          # 1X2: only bet a favourite above this prob
TG_N = 2                  # total goals: bet the N most probable outcomes
MAX_GAMES_PER_WEEK = 5


def isoweek(date_str: str) -> tuple[str, dt.date]:
    d = dt.date.fromisoformat(date_str)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}", d


def main() -> None:
    matches = pd.read_parquet(config.PROCESSED_DIR / "matches_latest.parquet")
    odds = pd.read_parquet(config.PROCESSED_DIR / "odds_latest.parquet")

    epl = matches[(matches.league == "ENG-PREM") &
                  (matches.source == "footballdata") &
                  (matches.season.isin(SEASONS))].copy()
    epl = epl[epl.fthg.notna() & epl.ftag.notna()].set_index("match_key")

    # 1X2 odds: football-data Bet365 closing
    x = odds[(odds.source == "footballdata") & (odds.market == "1X2") &
             (odds.bookmaker == "Bet365") & (odds.phase == "close")]
    x = x.pivot_table(index="match_key", columns="selection", values="odds", aggfunc="first")

    # TG odds: sgodds, keyed by fixture_key (cross-source join to football-data)
    sgtg = odds[(odds.source == "sgodds") & (odds.market == "TG")]
    sgtg_piv = sgtg.pivot_table(index="fixture_key", columns="selection",
                                values="odds", aggfunc="first")
    tg_by_fk = {fk: row.dropna().to_dict() for fk, row in sgtg_piv.iterrows()}

    # ---- build per-match candidate bets ----
    cands = []
    for mk, r in epl.iterrows():
        # 1X2 leg: only a favourite above THRESHOLD qualifies
        onex2 = None
        if mk in x.index:
            od = x.loc[mk, ["H", "D", "A"]]
            if od.notna().all():
                p = engine.implied_probs(od)
                if p.max() > THRESHOLD:
                    onex2 = dict(sel=p.idxmax(), odds=float(od[p.idxmax()]),
                                 prob=float(p.max()))
        # TG leg: the TG_N most probable exact totals
        tg = None
        d = tg_by_fk.get(r.fixture_key)
        if d and len(d) >= TG_N:
            s = pd.Series(d, dtype=float)
            p = engine.implied_probs(s).sort_values(ascending=False)
            basket = list(p.index[:TG_N])
            tg = dict(basket=basket, odds={g: float(s[g]) for g in basket},
                      prob=float(p.iloc[:TG_N].sum()))
        if onex2 or tg:
            wk, _d = isoweek(r.date)
            cands.append(dict(mk=mk, week=wk, date=r.date, season=r.season,
                              home=r.home_team, away=r.away_team,
                              fthg=int(r.fthg), ftag=int(r.ftag), ftr=r.ftr,
                              conf=max([b["prob"] for b in (onex2, tg) if b]),
                              onex2=onex2, tg=tg))

    cdf = pd.DataFrame(cands)

    # ---- weekly selection (top 5 games by confidence) + settlement ----
    bet_rows = []
    for wk, grp in cdf.groupby("week"):
        chosen = grp.sort_values("conf", ascending=False).head(MAX_GAMES_PER_WEEK)
        for c in chosen.itertuples(index=False):
            score = f"{c.fthg}-{c.ftag}"
            if c.onex2:
                win = c.ftr == c.onex2["sel"]
                bet_rows.append(dict(
                    week=wk, date=c.date, season=c.season, home=c.home, away=c.away,
                    score=score, category="1X2", selection=c.onex2["sel"],
                    est_prob=round(c.onex2["prob"], 3), odds=c.onex2["odds"],
                    stake=STAKE, result="W" if win else "L",
                    profit=round((c.onex2["odds"] - 1) * STAKE if win else -STAKE, 2)))
            if c.tg:
                bucket = min(c.fthg + c.ftag, 9)
                for g in c.tg["basket"]:
                    win = str(bucket) == g
                    o = c.tg["odds"][g]
                    bet_rows.append(dict(
                        week=wk, date=c.date, season=c.season, home=c.home, away=c.away,
                        score=score, category=f"TG={'9+' if g == '9' else g}",
                        selection=g, est_prob=round(c.tg["prob"], 3), odds=o,
                        stake=STAKE, result="W" if win else "L",
                        profit=round((o - 1) * STAKE if win else -STAKE, 2)))

    bl = pd.DataFrame(bet_rows).sort_values(["date", "category"]).reset_index(drop=True)

    # ---- bankroll / drawdown (settle in match-date order) ----
    bl["cum_profit"] = bl["profit"].cumsum()
    bl["bankroll"] = START_CAPITAL + bl["cum_profit"]
    peak = bl["bankroll"].cummax()
    dd = bl["bankroll"] - peak
    max_dd = float(dd.min())
    max_dd_pct = 100 * max_dd / float(peak[dd.idxmin()]) if len(bl) else 0.0

    # ---- weekly breakdown ----
    wk = (bl.groupby("week")
            .agg(games=("home", "nunique"), bets=("stake", "size"),
                 staked=("stake", "sum"), pnl=("profit", "sum"))
            .reset_index())
    wk["cum_pnl"] = wk["pnl"].cumsum()
    wk["bankroll"] = START_CAPITAL + wk["cum_pnl"]
    order = bl.groupby("week")["date"].min()
    wk = wk.set_index("week").loc[order.sort_values().index].reset_index()

    # ---- outputs ----
    out_bl = config.PROCESSED_DIR / "strategy-1-bet-log.csv"
    out_wk = config.PROCESSED_DIR / "strategy-1-weekly-pnl.csv"
    bl.drop(columns=["cum_profit"]).to_csv(out_bl, index=False)
    wk.to_csv(out_wk, index=False)

    staked = bl.stake.sum()
    profit = bl.profit.sum()
    print(f"STRATEGY 1 | EPL {SEASONS[0]} + {SEASONS[1]} | start ${START_CAPITAL:.0f} | "
          f"flat ${STAKE:.0f}/bet | <= {MAX_GAMES_PER_WEEK} games/week | "
          f"1X2 p>{THRESHOLD:.0%} | TG top-{TG_N}\n")
    print("=== OVERALL ===")
    print(f"weeks active     : {len(wk)}")
    print(f"games bet        : {bl.groupby(['date','home']).ngroups}")
    print(f"total bets        : {len(bl)}   (1X2: {(bl.category=='1X2').sum()}, "
          f"TG legs: {(bl.category!='1X2').sum()})")
    print(f"total staked      : ${staked:,.0f}")
    print(f"total P&L         : ${profit:+,.1f}   ROI {100*profit/staked:+.2f}%")
    print(f"final bankroll    : ${START_CAPITAL+profit:,.1f}")
    print(f"max drawdown      : ${max_dd:,.1f}  ({max_dd_pct:+.2f}% of peak)")
    print(f"hit rate 1X2      : {100*bl[bl.category=='1X2'].result.eq('W').mean():.1f}%")
    print(f"hit rate TG legs  : {100*bl[bl.category!='1X2'].result.eq('W').mean():.1f}%")

    for seas in SEASONS:
        s = bl[bl.season == seas]
        if len(s):
            has_tg = (s.category != "1X2").any()
            print(f"  {seas}: {len(s)} bets, staked ${s.stake.sum():.0f}, "
                  f"P&L ${s.profit.sum():+.1f}"
                  f"{'' if has_tg else '  (1X2 only - no TG data)'}")

    print("\n=== WEEK-BY-WEEK ===")
    print(f"{'week':9s} {'seas':4s} {'games':>5} {'bets':>4} {'staked':>7} "
          f"{'P&L':>8} {'bankroll':>9}")
    for w in wk.itertuples(index=False):
        seas = "25/26" if w.week >= "2025-W30" else "24/25"
        print(f"{w.week:9s} {seas:4s} {w.games:>5} {w.bets:>4} "
              f"${w.staked:>6.0f} {w.pnl:>+8.1f} ${w.bankroll:>8.1f}")

    print(f"\nbet log  -> {out_bl}")
    print(f"weekly   -> {out_wk}")


if __name__ == "__main__":
    main()
