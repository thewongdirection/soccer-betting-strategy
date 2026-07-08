"""Strategy 3 -- fitted Dixon-Coles value betting (EPL).

A rolling, look-ahead-free Dixon-Coles model (soccer_backtest/strategy_3.py)
prices 1X2 and Over/Under 2.5. Following the Strategy 2 review:

  * BLEND the model with the de-vigged market consensus (curbs adverse selection);
  * VALUE-bet at the BEST available opening price (market-max) when
    blended_p * best_odds - 1 > min_edge;
  * measure CLOSING-LINE VALUE (CLV) vs the de-vigged closing consensus -- the
    real, low-variance edge signal;
  * bet the low-margin O/U 2.5 market, not the ~29%-margin exact-total-goals book.

The model is fitted once (the slow step); betting configs are then evaluated on
the cached per-selection signals, so ``--sweep`` can grid blend x min_edge for
free.

    python scripts/strategy-3.py --markets 1X2 --kelly 0.25 --sweep

Prerequisite: a data pull (reads data/processed/*.parquet).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soccer_backtest import config, strategy_3 as dc  # noqa: E402

K1 = ["H", "D", "A"]


def _od(odds, market, book, phase, line=None):
    q = ((odds.source == "footballdata") & (odds.market == market) &
         (odds.bookmaker == book) & (odds.phase == phase))
    if line is not None:
        q &= (odds.line == line)
    sub = odds[q]
    if sub.empty:
        return {}
    return sub.pivot_table(index="match_key", columns="selection",
                           values="odds", aggfunc="first").to_dict("index")


def _devig(d, keys):
    if not d or any(d.get(k, 0) <= 1 for k in keys):
        return None
    inv = np.array([1.0 / d[k] for k in keys])
    return inv / inv.sum()


def collect_signals(m, odds, model, train, bet_seasons):
    """One model pass -> per-selection signal rows (model/market probs, best price,
    closing prob, outcome). Independent of blend/min_edge/staking."""
    px1, cons1 = _od(odds, "1X2", "MarketMax", "open"), _od(odds, "1X2", "MarketAvg", "open")
    clo1 = _od(odds, "1X2", "MarketAvg", "close") or _od(odds, "1X2", "Bet365", "close")
    pxo = _od(odds, "OU", "MarketMax", "open", 2.5)
    conso = _od(odds, "OU", "MarketAvg", "open", 2.5)
    cloo = _od(odds, "OU", "MarketAvg", "close", 2.5) or _od(odds, "OU", "Bet365", "close", 2.5)

    rows = []
    for r in m.itertuples(index=False):
        if model.needs_refit(r.dt):
            model.fit(train, r.dt)
        if not model.fitted or r.season not in bet_seasons:
            continue
        eg = model.expected_goals(r.home_team, r.away_team)
        if eg is None:
            continue
        mp = dc.market_probs(dc.scoreline_matrix(*eg, model.rho))
        total = int(r.fthg) + int(r.ftag)
        base = dict(mk=r.match_key, date=r.date, season=r.season)

        cons_p = _devig(cons1.get(r.match_key), K1)
        clo_p = _devig(clo1.get(r.match_key), K1)
        px = px1.get(r.match_key)
        if px and cons_p is not None:
            for i, s in enumerate(K1):
                o = px.get(s)
                if o and o > 1:
                    rows.append({**base, "market": "1X2", "sel": s,
                                 "model_p": [mp["home"], mp["draw"], mp["away"]][i],
                                 "cons_p": cons_p[i], "best_odds": float(o),
                                 "close_p": clo_p[i] if clo_p is not None else np.nan,
                                 "win": r.ftr == s})

        cons_o = _devig(conso.get(r.match_key), ["OVER", "UNDER"])
        clo_o = _devig(cloo.get(r.match_key), ["OVER", "UNDER"])
        pxu = pxo.get(r.match_key)
        if pxu and cons_o is not None:
            for i, s in enumerate(["OVER", "UNDER"]):
                o = pxu.get(s)
                if o and o > 1:
                    win = (total >= 3) if s == "OVER" else (total <= 2)
                    rows.append({**base, "market": "OU2.5", "sel": s,
                                 "model_p": [mp["over25"], mp["under25"]][i],
                                 "cons_p": cons_o[i], "best_odds": float(o),
                                 "close_p": clo_o[i] if clo_o is not None else np.nan,
                                 "win": win})
    return pd.DataFrame(rows)


def evaluate(cdf, markets, blend, min_edge, kelly=None, flat=1.0,
             max_stake=0.05, capital=1000.0):
    d = cdf[cdf.market.isin(markets)].copy()
    if d.empty:
        return None, d
    d["mix"] = blend * d.model_p + (1 - blend) * d.cons_p
    d["fair"] = d.mix / d.groupby(["mk", "market"])["mix"].transform("sum")
    d["edge"] = d.fair * d.best_odds - 1
    d = d[d.edge > min_edge].sort_values(["date", "mk"]).reset_index(drop=True)
    if d.empty:
        return None, d

    bankroll = capital
    stakes, pnls, banks = [], [], []
    for e in d.itertuples(index=False):
        if kelly is not None:
            f = e.edge / (e.best_odds - 1)
            stake = round(min(kelly * f, max_stake) * max(bankroll, 0.0), 2)
        else:
            stake = flat
        pnl = round(stake * (e.best_odds - 1) if e.win else -stake, 2)
        bankroll = round(bankroll + pnl, 2)
        stakes.append(stake); pnls.append(pnl); banks.append(bankroll)
    d["stake"], d["pnl"], d["bankroll"] = stakes, pnls, banks
    d["clv"] = d.close_p * d.best_odds - 1

    staked, pnl = d.stake.sum(), d.pnl.sum()
    peak = d.bankroll.cummax(); dd = (d.bankroll - peak)
    clv = d.clv.dropna()
    summ = dict(bets=len(d), staked=staked, pnl=pnl,
                yield_=100 * pnl / staked if staked else 0,
                hit=100 * d.win.mean(), final=capital + pnl,
                mdd=float(dd.min()), mdd_pct=100 * float(dd.min()) / float(peak[dd.idxmin()]),
                clv=100 * clv.mean(), clv_pos=100 * (clv > 0).mean())
    return summ, d


def _print(tag, s):
    print(f"[{tag}]  bets {s['bets']:,} | yield {s['yield_']:+.2f}% | "
          f"CLV {s['clv']:+.2f}% (CLV+ {s['clv_pos']:.0f}%) | hit {s['hit']:.1f}% | "
          f"final ${s['final']:,.0f} | maxDD {s['mdd_pct']:+.1f}%")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--league", default="ENG-PREM")
    p.add_argument("--start-year", type=int, default=2023, dest="start_year")
    p.add_argument("--end-year", type=int, default=2025, dest="end_year")
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--markets", default="1X2,OU2.5")
    p.add_argument("--blend", type=float, default=0.5)
    p.add_argument("--min-edge", type=float, default=0.02, dest="min_edge")
    p.add_argument("--kelly", type=float, default=None, help="fraction; omit for flat $1")
    p.add_argument("--max-stake", type=float, default=0.05, dest="max_stake")
    p.add_argument("--capital", type=float, default=1000.0)
    p.add_argument("--half-life", type=float, default=180, dest="half_life")
    p.add_argument("--ridge", type=float, default=0.05)
    p.add_argument("--sweep", action="store_true", help="grid blend x min-edge on 1X2 (CLV)")
    args = p.parse_args()
    markets = set(args.markets.replace("OU", "OU2.5").replace("OU2.52.5", "OU2.5").split(","))

    matches = pd.read_parquet(config.PROCESSED_DIR / "matches_latest.parquet")
    odds = pd.read_parquet(config.PROCESSED_DIR / "odds_latest.parquet")
    bet_seasons = {f"{y}-{y+1}" for y in range(args.start_year, args.end_year + 1)}
    load_seasons = {f"{y}-{y+1}"
                    for y in range(args.start_year - args.warmup, args.end_year + 1)}
    m = matches[(matches.source == "footballdata") & (matches.league == args.league) &
                (matches.season.isin(load_seasons)) &
                matches.fthg.notna() & matches.ftag.notna()].copy()
    m["dt"] = pd.to_datetime(m["date"])
    m = m.sort_values(["dt", "match_key"]).reset_index(drop=True)
    train = m[["home_team", "away_team", "fthg", "ftag", "dt"]].rename(
        columns={"home_team": "home", "away_team": "away", "dt": "date"})

    model = dc.DixonColes(half_life_days=args.half_life, ridge=args.ridge)
    cdf = collect_signals(m, odds, model, train, bet_seasons)
    print(f"STRATEGY 3 (Dixon-Coles value betting) | {args.league} | "
          f"{args.start_year}-{args.end_year} | best price (Max open) vs close")
    print(f"fitted home-adv gamma={model.gamma:.3f}  rho={model.rho:.3f}  "
          f"| {len(cdf):,} candidate selections\n")

    stake_desc = f"{args.kelly:g}x Kelly" if args.kelly is not None else "flat $1"
    summ, d = evaluate(cdf, markets, args.blend, args.min_edge,
                       kelly=args.kelly, max_stake=args.max_stake, capital=args.capital)
    print(f"=== HEADLINE: markets={sorted(markets)} blend={args.blend:.0%} "
          f"min-edge={args.min_edge:.0%} stake={stake_desc} ===")
    if summ:
        _print("all", summ)
        if len(markets) > 1:
            for mk in sorted(markets):
                s1, _ = evaluate(cdf, {mk}, args.blend, args.min_edge,
                                 kelly=args.kelly, max_stake=args.max_stake, capital=args.capital)
                if s1:
                    _print(mk, s1)
        d.to_csv(config.PROCESSED_DIR / "strategy-3-bet-log.csv", index=False)
    else:
        print("  no bets")

    if args.sweep:
        print("\n=== SWEEP (1X2 only, flat $1) -- optimise on CLV ===")
        print(f"{'blend':>6} {'min-edge':>9} {'bets':>6} {'yield%':>8} {'CLV%':>7} {'CLV+%':>6}")
        for bl in (0.3, 0.5, 0.7, 1.0):
            for me in (0.00, 0.02, 0.04, 0.06):
                s, _ = evaluate(cdf, {"1X2"}, bl, me, capital=args.capital)
                if s:
                    print(f"{bl:>6.1f} {me:>9.0%} {s['bets']:>6,} {s['yield_']:>+8.2f} "
                          f"{s['clv']:>+7.2f} {s['clv_pos']:>6.0f}")


if __name__ == "__main__":
    main()
