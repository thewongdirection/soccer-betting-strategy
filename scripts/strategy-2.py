"""Strategy 2 -- Poisson value-betting backtest (independent model + Kelly).

Builds a look-ahead-free Poisson goals model per league (rolling home/away
attack & defence ratings), turns it into 1X2 and Over/Under 2.5 probabilities,
and bets only positive-expected-value selections against the market, staking
fractional Kelly on a compounding bankroll. See soccer_backtest/strategy_2.py
for the full method.

Prerequisites: a data pull must exist (reads data/processed/*.parquet).

Examples:
    python scripts/strategy-2.py
    python scripts/strategy-2.py --book MarketMax --min-edge 0.03 --kelly 0.5
    python scripts/strategy-2.py --leagues ENG-PREM --start-year 2005
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soccer_backtest import config, strategy_2 as engine  # noqa: E402

BIG5 = ["ENG-PREM", "ESP-LL", "ITA-SA", "GER-BL1", "FRA-L1"]


def _odds_dict(odds, market, book, phase, line=None):
    q = ((odds.source == "footballdata") & (odds.market == market) &
         (odds.bookmaker == book) & (odds.phase == phase))
    if line is not None:
        q &= (odds.line == line)
    sub = odds[q]
    if sub.empty:
        return {}
    piv = sub.pivot_table(index="match_key", columns="selection",
                          values="odds", aggfunc="first")
    return piv.to_dict("index")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--leagues", nargs="*", default=BIG5)
    p.add_argument("--start-year", type=int, default=2010, dest="start_year")
    p.add_argument("--end-year", type=int, default=2025, dest="end_year")
    p.add_argument("--book", default="Bet365", help="bookmaker to bet into")
    p.add_argument("--phase", default="open", choices=["open", "close"],
                   help="open has deep history; close only ~2019+ in football-data")
    p.add_argument("--markets", default="1X2,OU")
    p.add_argument("--min-edge", type=float, default=0.05, dest="min_edge")
    p.add_argument("--kelly", type=float, default=0.25, help="Kelly fraction")
    p.add_argument("--max-stake", type=float, default=0.02, dest="max_stake",
                   help="cap on stake as fraction of bankroll")
    p.add_argument("--alpha", type=float, default=0.05, help="ratings EWMA rate")
    p.add_argument("--min-games", type=int, default=6, dest="min_games")
    p.add_argument("--shrink", type=float, default=1.0,
                   help="<1 regresses team ratings toward league mean (reduces overconfidence)")
    p.add_argument("--rho", type=float, default=0.0, help="Dixon-Coles low-score corr")
    p.add_argument("--capital", type=float, default=1000.0)
    p.add_argument("--pick", action="store_true",
                   help="bet the model's most-likely selection per market on EVERY "
                        "game (no value filter)")
    p.add_argument("--flat", type=float, default=None,
                   help="flat stake per bet (disables Kelly)")
    p.add_argument("--warmup", type=int, default=3,
                   help="extra prior seasons used only to warm up ratings")
    args = p.parse_args()
    markets = set(args.markets.split(","))

    matches = pd.read_parquet(config.PROCESSED_DIR / "matches_latest.parquet")
    odds = pd.read_parquet(config.PROCESSED_DIR / "odds_latest.parquet")
    bet_seasons = {f"{y}-{y+1}" for y in range(args.start_year, args.end_year + 1)}
    load_seasons = {f"{y}-{y+1}"
                    for y in range(args.start_year - args.warmup, args.end_year + 1)}

    m = matches[(matches.source == "footballdata") &
                (matches.league.isin(args.leagues)) &
                (matches.season.isin(load_seasons)) &
                matches.fthg.notna() & matches.ftag.notna()].copy()
    m = m.sort_values(["date", "league", "match_key"]).reset_index(drop=True)

    x1 = _odds_dict(odds, "1X2", args.book, args.phase)
    xou = _odds_dict(odds, "OU", args.book, args.phase, line=2.5)

    ledger: dict[str, engine.LeagueRatings] = {}
    bankroll = args.capital
    rows = []
    sel_map_1x2 = [("H", "home"), ("D", "draw"), ("A", "away")]
    sel_map_ou = [("OVER", "over25"), ("UNDER", "under25")]

    for r in m.itertuples(index=False):
        lg = r.league
        rate = ledger.setdefault(lg, engine.LeagueRatings(
            args.alpha, args.min_games, args.shrink))
        eg = rate.expected_goals(r.home_team, r.away_team)
        if eg is not None and r.season in bet_seasons:
            lh, la = eg
            mp = engine.market_probs(engine.scoreline_matrix(lh, la, args.rho))
            gh, ga = int(r.fthg), int(r.ftag)
            total = gh + ga
            o1 = x1.get(r.match_key, {})
            oo = xou.get(r.match_key, {})
            cands = []
            if "1X2" in markets:
                avail = [(s, k) for s, k in sel_map_1x2 if o1.get(s, 0) and o1[s] > 1]
                if args.pick:                       # only the model's top pick
                    avail = [max(avail, key=lambda sk: mp[sk[1]])] if avail else []
                for s, k in avail:
                    cands.append(("1X2", s, mp[k], float(o1[s]), r.ftr == s))
            if "OU" in markets:
                availo = [(s, k) for s, k in sel_map_ou if oo.get(s, 0) and oo[s] > 1]
                if args.pick:
                    availo = [max(availo, key=lambda sk: mp[sk[1]])] if availo else []
                for s, k in availo:
                    win = total >= 3 if s == "OVER" else total <= 2
                    cands.append(("OU2.5", s, mp[k], float(oo[s]), win))
            for market, sel, prob, od, win in cands:
                edge = prob * od - 1.0
                if not args.pick and edge <= args.min_edge:
                    continue                        # value filter (skipped in --pick)
                if args.flat is not None:
                    stake = args.flat
                else:
                    f = engine.kelly_fraction(prob, od)
                    stake = round(min(args.kelly * f, args.max_stake) * max(bankroll, 0.0), 2)
                pnl = round(stake * (od - 1) if win else -stake, 2)
                bankroll = round(bankroll + pnl, 2)
                rows.append(dict(date=r.date, league=lg, season=r.season,
                                 home=r.home_team, away=r.away_team,
                                 score=f"{gh}-{ga}", market=market, selection=sel,
                                 model_p=round(prob, 3), odds=od,
                                 edge=round(edge, 3), stake=stake,
                                 result="W" if win else "L", pnl=pnl,
                                 unit_pnl=round(od - 1 if win else -1.0, 3),
                                 bankroll=bankroll))
        rate.update(r.home_team, r.away_team, int(r.fthg), int(r.ftag))

    bl = pd.DataFrame(rows)
    out = config.PROCESSED_DIR / "strategy-2-bet-log.csv"
    bl.to_csv(out, index=False)

    mode = "model-pick, all games" if args.pick else f"value edge>{args.min_edge:.0%}"
    stake_desc = (f"flat ${args.flat:g}/bet" if args.flat is not None
                  else f"{args.kelly:g}x Kelly (cap {args.max_stake:.0%})")
    print(f"STRATEGY 2 | {'+'.join(args.leagues)} | seasons {args.start_year}-"
          f"{args.end_year} (+{args.warmup} warm-up) | vs {args.book} {args.phase} | "
          f"{mode} | {stake_desc}\n")
    if bl.empty:
        print("No bets placed."); return

    n = len(bl)
    nbet = int(m.season.isin(bet_seasons).sum())
    flat_yield = 100 * bl.unit_pnl.mean()
    peak = bl.bankroll.cummax(); dd = bl.bankroll - peak
    i = dd.idxmin(); mdd = float(dd.min()); mdd_pct = 100 * mdd / float(peak[i])
    print("=== OVERALL (flat $1 per bet) ===")
    print(f"games in window    : {nbet:,}")
    print(f"bets placed        : {n:,}")
    print(f"total staked       : ${bl.stake.sum():,.2f}")
    print(f"total P&L          : ${bl.pnl.sum():+,.2f}")
    print(f"yield (P&L/staked) : {flat_yield:+.2f}%")
    print(f"final bankroll     : ${args.capital+bl.pnl.sum():,.2f}  "
          f"(start ${args.capital:.0f}, {100*bl.pnl.sum()/args.capital:+.1f}%)")
    print(f"max drawdown       : ${mdd:,.2f}  ({mdd_pct:+.2f}% of peak)")
    print(f"hit rate           : {100*bl.result.eq('W').mean():.1f}%")
    print(f"avg model_p / hit  : {bl.model_p.mean():.3f} / {bl.result.eq('W').mean():.3f}")

    print("\n=== FLAT-STAKE YIELD BY MARKET/SELECTION ===")
    g = (bl.assign(ms=bl.market + " " + bl.selection)
           .groupby("ms")
           .agg(bets=("unit_pnl", "size"), hit=("result", lambda s: (s == "W").mean()),
                yld=("unit_pnl", lambda s: 100 * s.mean())))
    print(g.round(3).to_string())

    print("\n=== FLAT-STAKE YIELD BY SEASON ===")
    gs = bl.groupby("season").agg(bets=("unit_pnl", "size"),
                                  yld=("unit_pnl", lambda s: 100 * s.mean()))
    print(gs.round(2).to_string())
    print(f"\nbet log -> {out}")


if __name__ == "__main__":
    main()
