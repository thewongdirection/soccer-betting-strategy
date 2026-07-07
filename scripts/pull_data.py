"""Pull historical odds + outcomes into normalized tables.

Examples
--------
    # Deep history for the big-5 leagues from football-data:
    python scripts/pull_data.py footballdata --leagues ENG-PREM ESP-LL ITA-SA \
        GER-BL1 FRA-L1 --start-year 2015 --end-year 2025

    # All current Singapore Pools opening odds + results:
    python scripts/pull_data.py sgodds

    # Everything both sources have (respecting the season window), to sqlite+parquet:
    python scripts/pull_data.py all --format both

Output goes to data/processed/ (matches_<tag>.parquet, odds_<tag>.parquet,
soccer.db). Raw downloads are cached under data/raw/ so re-runs don't re-hit
the servers; pass --no-cache to force a refresh.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soccer_backtest import schema, storage  # noqa: E402
from soccer_backtest.sources import footballdata as fd  # noqa: E402
from soccer_backtest.sources import sgodds as sg  # noqa: E402


def _pull_footballdata(args, match_acc, odds_acc) -> None:
    items = fd.enumerate_items(
        leagues=args.leagues,
        start_year=args.start_year,
        end_year=args.end_year,
        include_extra=not args.no_extra,
    )
    print(f"[footballdata] {len(items)} files to pull ...")
    for i, it in enumerate(items, 1):
        try:
            m, o = fd.pull_item(it, use_cache=not args.no_cache)
        except Exception as exc:  # noqa: BLE001  (missing season files 404 -> skip)
            print(f"  [{i}/{len(items)}] SKIP {it.league} {it.season}: {exc}")
            continue
        match_acc.extend(m)
        odds_acc.extend(o)
        print(f"  [{i}/{len(items)}] {it.league} {it.season}: "
              f"{len(m)} matches, {len(o)} odds")


def _pull_sgodds(args, match_acc, odds_acc) -> None:
    dls = sg.enumerate_downloads(use_cache=not args.no_cache)
    if args.leagues:
        dls = [d for d in dls if d.league in set(args.leagues)]
    print(f"[sgodds] {len(dls)} league CSVs to pull ...")
    for i, d in enumerate(dls, 1):
        try:
            m, o = sg.pull_download(d, use_cache=not args.no_cache)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(dls)}] SKIP {d.league}: {exc}")
            continue
        match_acc.extend(m)
        odds_acc.extend(o)
        print(f"  [{i}/{len(dls)}] {d.league}: {len(m)} matches, {len(o)} odds")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", choices=["footballdata", "sgodds", "all"])
    p.add_argument("--leagues", nargs="*", help="normalized league ids to include")
    p.add_argument("--start-year", type=int, default=None, dest="start_year",
                   help="footballdata: first season start year (default 2000)")
    p.add_argument("--end-year", type=int, default=2025, dest="end_year")
    p.add_argument("--no-extra", action="store_true")
    p.add_argument("--no-cache", action="store_true", help="bypass raw download cache")
    p.add_argument("--format", choices=["parquet", "sqlite", "both"], default="both")
    p.add_argument("--tag", default="latest", help="suffix for parquet output files")
    args = p.parse_args()

    match_acc: list[dict] = []
    odds_acc: list[dict] = []

    if args.source in ("footballdata", "all"):
        _pull_footballdata(args, match_acc, odds_acc)
    if args.source in ("sgodds", "all"):
        _pull_sgodds(args, match_acc, odds_acc)

    matches = (pd.DataFrame(match_acc) if match_acc else schema.empty_matches())
    odds = (pd.DataFrame(odds_acc) if odds_acc else schema.empty_odds())

    # Ensure all schema columns exist even if a source omitted some.
    for c in schema.MATCH_COLUMNS:
        if c not in matches.columns:
            matches[c] = pd.NA
    for c in schema.ODDS_COLUMNS:
        if c not in odds.columns:
            odds[c] = pd.NA

    print(f"\nTotal: {len(matches)} match rows, {len(odds)} odds rows")

    if args.format in ("parquet", "both"):
        storage.write_parquet(matches, odds, tag=args.tag)
        print(f"  wrote parquet -> data/processed/matches_{args.tag}.parquet, "
              f"odds_{args.tag}.parquet")
    if args.format in ("sqlite", "both"):
        n_m, n_o = storage.write_sqlite(matches, odds)
        print(f"  wrote sqlite  -> data/processed/soccer.db "
              f"({n_m} matches, {n_o} odds upserted)")


if __name__ == "__main__":
    main()
