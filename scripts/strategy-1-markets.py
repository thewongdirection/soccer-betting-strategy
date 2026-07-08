"""Strategy 1 -- broad cross-source / cross-market P&L comparison.

Applies the Strategy 1 rules (see ``soccer_backtest/strategy_1.py``) to the full
normalized dataset and prints one P&L line per (market, source, bookmaker,
phase) configuration -- e.g. 1X2 favourites priced at Bet365 open vs close vs
market-average across all of football-data's history, and the Singapore Pools
1X2 and total-goals books from sgodds.

  * 1X2 -> $1 on the top outcome iff its de-vigged prob > threshold (default 70%).
  * TG  -> $1 on each of the 2 most probable exact totals (Strategy 1 default).

This is the "how does the strategy fare in general" view; the game-selection,
weekly, bankroll-tracked EPL run lives in ``scripts/strategy-1.py``.

Examples:
    python scripts/strategy-1-markets.py
    python scripts/strategy-1-markets.py --threshold 0.70
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soccer_backtest import config, strategy_1 as engine  # noqa: E402

# (market, source, bookmaker, phase, label)
CONFIGS = [
    ("1X2", "footballdata", "Bet365",         "open",  "1X2 |football-data |Bet365 open"),
    ("1X2", "footballdata", "Bet365",         "close", "1X2 |football-data |Bet365 close"),
    ("1X2", "footballdata", "MarketAvg",      "close", "1X2 |football-data |market-avg close"),
    ("1X2", "sgodds",       "SingaporePools", "open",  "1X2 |sgodds |SG Pools open"),
    ("TG",  "sgodds",       "SingaporePools", "open",  "TG  |sgodds |SG Pools open (top-2)"),
]


def _fmt(s: dict) -> str:
    freq = (100 * s["matches_bet"] / s["considered"]) if s["considered"] else 0
    return (f"{s['considered']:>7,} {s['matches_bet']:>8,} {freq:>5.1f}% "
            f"{s['stakes']:>9,} {s['returned']:>12,.0f} {s['profit']:>+11,.1f} "
            f"{100*s['roi']:>+7.2f}% {100*s['hit_rate']:>7.1f}%")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--threshold", type=float, default=0.70, help="1X2 favourite cutoff")
    p.add_argument("--stake", type=float, default=1.0)
    args = p.parse_args()

    matches = pd.read_parquet(config.PROCESSED_DIR / "matches_latest.parquet")
    odds = pd.read_parquet(config.PROCESSED_DIR / "odds_latest.parquet")

    print(f"1X2 threshold p>{args.threshold:.0%}   TG top-2   flat stake ${args.stake:g}\n")
    hdr = (f"{'strategy |source |book/phase':45s} {'univ':>7} {'bets':>8} "
           f"{'freq':>6} {'$stk':>9} {'$ret':>12} {'$profit':>11} {'ROI':>8} {'hit':>8}")
    print(hdr); print("-" * len(hdr))

    summaries = {}
    for market, source, book, phase, label in CONFIGS:
        if market == "1X2":
            res, s = engine.backtest_1x2(matches, odds, source, book, phase,
                                         threshold=args.threshold, stake=args.stake)
        else:
            res, s = engine.backtest_tg(matches, odds, source, book, phase,
                                        top_n=2, stake=args.stake)
        summaries[label] = (res, s)
        print(f"{label:45s} {_fmt(s)}")

    # combined "as-described on one book" = SG Pools 1X2 + SG Pools TG
    sg1 = summaries["1X2 |sgodds |SG Pools open"][1]
    sgt = summaries["TG  |sgodds |SG Pools open (top-2)"][1]
    cs = sg1["staked"] + sgt["staked"]
    cp = sg1["profit"] + sgt["profit"]
    print("-" * len(hdr))
    print(f"{'COMBINED |sgodds (1X2 + TG)':45s} "
          f"{'':>7} {sg1['matches_bet']+sgt['matches_bet']:>8,} {'':>6} "
          f"{int(cs):>9,} {sg1['returned']+sgt['returned']:>12,.0f} "
          f"{cp:>+11,.1f} {100*cp/cs if cs else 0:>+7.2f}% {'':>8}")

    # drawdown / span on the deepest 1X2 config
    label = "1X2 |football-data |Bet365 close"
    res, s = summaries[label]
    if not res.empty:
        eq = engine.equity_curve(res, matches, "footballdata")
        dd = (eq.cum_profit.cummax() - eq.cum_profit).max()
        print(f"\n[{label}] span {eq.date.min()}..{eq.date.max()}  "
              f"final P&L ${eq.cum_profit.iloc[-1]:+,.0f}  max drawdown ${dd:,.0f}")

    print("\nNote: bets settle at the same odds used to estimate probabilities, so "
          "each bet's expected value is the book's overround working against you; "
          "these results measure the strategy's realised edge (or lack of it), not "
          "a modelling error.")


if __name__ == "__main__":
    main()
