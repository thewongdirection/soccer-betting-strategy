"""List what data each source can provide.

Examples
--------
    python scripts/enumerate_sources.py sgodds
    python scripts/enumerate_sources.py footballdata --leagues ENG-PREM ITA-SA
    python scripts/enumerate_sources.py footballdata --probe --leagues ENG-PREM
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soccer_backtest.sources import footballdata as fd  # noqa: E402
from soccer_backtest.sources import sgodds as sg  # noqa: E402


def _footballdata(args) -> None:
    items = fd.enumerate_items(
        leagues=args.leagues,
        start_year=args.start_year,
        end_year=args.end_year,
        include_extra=not args.no_extra,
    )
    by_league: dict[str, list] = {}
    for it in items:
        by_league.setdefault(f"{it.league}  ({it.league_name})", []).append(it)

    print(f"football-data.co.uk: {len(items)} candidate files "
          f"across {len(by_league)} leagues\n")
    for league, its in sorted(by_league.items()):
        seasons = [i.season for i in its]
        span = (f"{seasons[0]} .. {seasons[-1]}" if len(seasons) > 1
                else seasons[0])
        print(f"  {league:38s} {len(its):3d} file(s)  [{span}]")
        if args.probe:
            for it in its:
                ok = fd.probe(it)
                print(f"        {'OK ' if ok else 'MISS'}  {it.season:10s} {it.url}")


def _sgodds(args) -> None:
    dls = sg.enumerate_downloads()
    print(f"sgodds.com: {len(dls)} downloadable league CSVs (Singapore Pools "
          f"opening odds + results)\n")
    for d in sorted(dls, key=lambda x: x.league):
        print(f"  {d.league:12s} {d.slug:32s} {d.url}")
    try:
        pages = sg.results_page_count()
        print(f"\n  results-past-odds HTML: ~{pages} page(s) available")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  (could not read results pagination: {exc})")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", choices=["footballdata", "sgodds"])
    p.add_argument("--leagues", nargs="*", help="filter to these normalized league ids")
    p.add_argument("--start-year", type=int, default=None, dest="start_year")
    p.add_argument("--end-year", type=int, default=2025, dest="end_year")
    p.add_argument("--no-extra", action="store_true",
                   help="footballdata: skip the extra/non-European leagues")
    p.add_argument("--probe", action="store_true",
                   help="footballdata: HTTP-probe each file for existence (slow)")
    args = p.parse_args()

    if args.source == "footballdata":
        _footballdata(args)
    else:
        _sgodds(args)


if __name__ == "__main__":
    main()
