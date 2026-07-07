"""Propose cross-source team-name aliases for a league (maintenance helper).

For each league it pulls the current-season team list from both football-data
and sgodds, finds sgodds names that don't already normalize onto a football-data
name, and fuzzy-matches each to its most likely football-data counterpart. Print
the proposed rows, eyeball them, and paste the good ones into
``soccer_backtest/naming.py`` (ALIASES maps the sgodds-normalized form -> the
football-data-normalized form).

Examples
--------
    python scripts/build_aliases.py --leagues ENG-PREM ESP-LL
    python scripts/build_aliases.py            # every league sgodds offers
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soccer_backtest import config  # noqa: E402
from soccer_backtest.naming import ALIASES, normalize_team  # noqa: E402
from soccer_backtest.sources import footballdata as fd  # noqa: E402
from soccer_backtest.sources import sgodds as sg  # noqa: E402

# Which normalized league ids live in football-data's "extra" (/new/) files.
_EXTRA_IDS = {lid for lid, _ in config.FD_EXTRA_LEAGUES.values()}


def _teams(rows) -> set[str]:
    df = pd.DataFrame(rows)
    if df.empty:
        return set()
    return set(df.home_team) | set(df.away_team)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--leagues", nargs="*", help="normalized league ids (default: all sgodds)")
    p.add_argument("--year", type=int, default=2025, help="season start year to compare")
    p.add_argument("--cutoff", type=float, default=0.4, help="fuzzy match cutoff")
    args = p.parse_args()

    sg_files = {d.league: d for d in sg.enumerate_downloads()}
    leagues = args.leagues or sorted(sg_files)

    for lid in leagues:
        if lid not in sg_files:
            print(f"# {lid}: not offered by sgodds"); continue
        items = fd.enumerate_items(leagues=[lid], start_year=args.year,
                                   end_year=args.year,
                                   include_extra=(lid in _EXTRA_IDS))
        if not items:
            print(f"# {lid}: no football-data mapping"); continue
        try:
            fd_teams = _teams(fd.pull_item(items[0])[0])
            sg_teams = _teams(sg.pull_download(sg_files[lid])[0])
        except Exception as exc:  # noqa: BLE001
            print(f"# {lid}: pull failed ({exc})"); continue

        fd_norm = {normalize_team(t): t for t in fd_teams}
        sg_norm = {normalize_team(t): t for t in sg_teams}
        # sgodds names not already resolving to a football-data name
        need = [k for k in sg_norm
                if k not in fd_norm and ALIASES.get(k, k) not in fd_norm]
        if not need:
            print(f"# {lid}: fully reconciled ({len(sg_teams)} teams)"); continue

        print(f"\n# {lid}: {len(need)} sgodds name(s) need an alias")
        pool = [k for k in fd_norm if k not in sg_norm]
        for k in sorted(need):
            cand = difflib.get_close_matches(k, pool, n=1, cutoff=args.cutoff)
            target = cand[0] if cand else "???  # no fuzzy match -- check by hand"
            print(f'    "{k}": "{target}",'
                  f'    # sgodds {sg_norm[k]!r} ~= FD {fd_norm.get(cand[0]) if cand else "?"!r}')


if __name__ == "__main__":
    main()
