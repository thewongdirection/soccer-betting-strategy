"""Validate the pulled dataset against the live source files.

For a stratified-random sample of matches, re-download the *live* source file
(HTTP cache bypassed) and compare our stored, normalized values cell-by-cell
against the raw CSV that the website serves right now:

* scores   -- FTHG/FTAG/FTR/HTHG/HTAG (football-data) or the Result string
              (sgodds), read directly from the raw row.
* odds     -- every stored odds row is mapped back to the exact raw column it
              came from and the two values compared.

This independently confirms (a) our data equals what the site serves, and
(b) the ETL (melt / typing / storage round-trip) is faithful. A field is
"checked" only when it exists in the raw file; missing raw columns are skipped,
not failed.

Usage:
    python scripts/validate_dataset.py --n 100 --seed 7
"""
from __future__ import annotations

import argparse
import io
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soccer_backtest import config, http  # noqa: E402
from soccer_backtest.naming import normalize_team  # noqa: E402
from soccer_backtest.sources import sgodds as sg  # noqa: E402

# ---- reverse league maps for reconstructing football-data URLs -------------
FD_LEAGUE_TO_DIV = {lid: div for div, (lid, _) in config.FD_MAIN_LEAGUES.items()}
FD_LEAGUE_TO_EXTRA = {lid: code for code, (lid, _) in config.FD_EXTRA_LEAGUES.items()}

# canonical bookmaker name -> football-data column prefix, per market family
_FD_PREFIX_1X2 = {"Bet365": "B365", "Pinnacle": "PS", "MarketMax": "Max",
                  "MarketAvg": "Avg", "BetWin": "BW", "Interwetten": "IW",
                  "WilliamHill": "WH", "VCBet": "VC"}
_FD_PREFIX_OUAH = {"Bet365": "B365", "Pinnacle": "P", "MarketMax": "Max",
                   "MarketAvg": "Avg"}


def _fd_url(league: str, season_label: str) -> str | None:
    if league in FD_LEAGUE_TO_DIV:
        start = int(season_label.split("-")[0])
        code = f"{start % 100:02d}{(start + 1) % 100:02d}"
        return config.FD_MAIN_URL.format(season=code, div=FD_LEAGUE_TO_DIV[league])
    if league in FD_LEAGUE_TO_EXTRA:
        return config.FD_EXTRA_URL.format(code=FD_LEAGUE_TO_EXTRA[league])
    return None


def _fd_raw(league: str, season_label: str) -> pd.DataFrame:
    url = _fd_url(league, season_label)
    raw = http.get_bytes(url, use_cache=False, suffix=".csv")  # LIVE fetch
    df = pd.read_csv(io.BytesIO(raw), encoding="latin-1",
                     on_bad_lines="skip", low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    ren = {"Home": "HomeTeam", "Away": "AwayTeam", "HG": "FTHG",
           "AG": "FTAG", "Res": "FTR"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    df["_h"] = df["HomeTeam"].map(normalize_team)
    df["_a"] = df["AwayTeam"].map(normalize_team)
    d = pd.to_datetime(df["Date"], format="%d/%m/%Y", errors="coerce")
    miss = d.isna()
    if miss.any():
        d.loc[miss] = pd.to_datetime(df["Date"][miss], format="%d/%m/%y",
                                     errors="coerce")
    df["_date"] = d.dt.date.astype(str)
    return df


def _fd_col_for(o) -> str | None:
    """Reconstruct the raw football-data column name a stored odds row came from."""
    c = "C" if o.phase == "close" else ""
    if o.market == "1X2":
        p = _FD_PREFIX_1X2.get(o.bookmaker)
        return f"{p}{c}{o.selection}" if p else None
    if o.market == "OU":
        p = _FD_PREFIX_OUAH.get(o.bookmaker)
        if not p or pd.isna(o.line):
            return None
        side = ">" if o.selection == "OVER" else "<"
        ln = f"{o.line:g}"
        return f"{p}{c}{side}{ln}"
    if o.market == "AH":
        p = _FD_PREFIX_OUAH.get(o.bookmaker)
        if not p:
            return None
        s = "H" if o.selection == "HOME" else "A"
        return f"{p}{c}AH{s}"
    return None


def _num_eq(a, b, tol=0.005) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _validate_fd_match(mrow, odds_for_match, raw_df, report):
    cand = raw_df[(raw_df["_h"] == normalize_team(mrow.home_team)) &
                  (raw_df["_a"] == normalize_team(mrow.away_team)) &
                  (raw_df["_date"] == mrow.date)]
    if cand.empty:  # fall back to team-only (date offset in extra files is rare)
        cand = raw_df[(raw_df["_h"] == normalize_team(mrow.home_team)) &
                      (raw_df["_a"] == normalize_team(mrow.away_team))]
    if cand.empty:
        report["match_not_found"] += 1
        return ["ROW NOT FOUND in live file"]
    rr = cand.iloc[0]
    errs = []

    # scores
    for stored_col, raw_col in [("fthg", "FTHG"), ("ftag", "FTAG"),
                                ("hthg", "HTHG"), ("htag", "HTAG")]:
        if raw_col in rr and pd.notna(rr[raw_col]) and pd.notna(getattr(mrow, stored_col)):
            report["fields_checked"] += 1
            if not _num_eq(rr[raw_col], getattr(mrow, stored_col)):
                report["fields_mismatch"] += 1
                errs.append(f"{stored_col}: stored={getattr(mrow, stored_col)} raw={rr[raw_col]}")
    if "FTR" in rr and pd.notna(rr["FTR"]) and pd.notna(mrow.ftr):
        report["fields_checked"] += 1
        if str(rr["FTR"]).strip() != str(mrow.ftr):
            report["fields_mismatch"] += 1
            errs.append(f"ftr: stored={mrow.ftr} raw={rr['FTR']}")

    # odds
    for o in odds_for_match.itertuples(index=False):
        col = _fd_col_for(o)
        if col is None or col not in rr or pd.isna(rr[col]):
            continue
        report["fields_checked"] += 1
        report["odds_checked"] += 1
        if not _num_eq(rr[col], o.odds):
            report["fields_mismatch"] += 1
            errs.append(f"odds {o.bookmaker}/{o.market}/{o.selection}/{o.phase}"
                        f"[{col}]: stored={o.odds} raw={rr[col]}")
    return errs


# ---- sgodds --------------------------------------------------------------
def _sg_raw_map():
    """slug-normalized-league -> raw sgodds DataFrame (live)."""
    out = {}
    for d in sg.enumerate_downloads(use_cache=False):
        raw = http.get_bytes(d.url, use_cache=False, suffix=".csv")
        df = pd.read_csv(io.BytesIO(raw), low_memory=False)
        df.columns = [str(c).strip() for c in df.columns]
        h, a = [], []
        for m in df["Match"].astype(str):
            hh, aa = sg._split_teams(m)
            h.append(normalize_team(hh)); a.append(normalize_team(aa))
        df["_h"], df["_a"] = h, a
        df["_date"] = pd.to_datetime(df["Start Time"], errors="coerce").dt.date.astype(str)
        out[d.league] = df
    return out


_SG_1X2 = {"H": "Ft1X2_01", "D": "Ft1X2_02", "A": "Ft1X2_03"}
_SG_OU = {"OVER": "Ou_01", "UNDER": "Ou_02"}
_SG_AH = {"HOME": "Ah_01", "AWAY": "Ah_02"}


def _validate_sg_match(mrow, odds_for_match, raw_df, report):
    cand = raw_df[(raw_df["_h"] == normalize_team(mrow.home_team)) &
                  (raw_df["_a"] == normalize_team(mrow.away_team)) &
                  (raw_df["_date"] == mrow.date)]
    if cand.empty:
        report["match_not_found"] += 1
        return ["ROW NOT FOUND in live file"]
    rr = cand.iloc[0]
    errs = []
    fth, fta, ftr, hth, hta, htr = sg._parse_result(rr.get("Result"))
    for label, stored, live in [("fthg", mrow.fthg, fth), ("ftag", mrow.ftag, fta),
                                ("ftr", mrow.ftr, ftr), ("hthg", mrow.hthg, hth),
                                ("htag", mrow.htag, hta)]:
        if pd.notna(stored) and live is not None:
            report["fields_checked"] += 1
            ok = (str(stored) == str(live)) if label == "ftr" else _num_eq(stored, live)
            if not ok:
                report["fields_mismatch"] += 1
                errs.append(f"{label}: stored={stored} live={live}")
    for o in odds_for_match.itertuples(index=False):
        col = (_SG_1X2.get(o.selection) if o.market == "1X2" else
               _SG_OU.get(o.selection) if o.market == "OU" else
               _SG_AH.get(o.selection) if o.market == "AH" else None)
        if col is None or col not in rr or pd.isna(rr[col]):
            continue
        report["fields_checked"] += 1
        report["odds_checked"] += 1
        if not _num_eq(rr[col], o.odds):
            report["fields_mismatch"] += 1
            errs.append(f"odds {o.market}/{o.selection}[{col}]: stored={o.odds} raw={rr[col]}")
    return errs


# expected fixture counts for round-robin leagues (n teams -> n*(n-1) matches)
_EXPECTED_MATCHES = {
    "ENG-PREM": 380, "ESP-LL": 380, "ITA-SA": 380, "FRA-L1": 380,
    "GER-BL1": 306, "NED-ERE": 306,
}


def run_invariants(matches: pd.DataFrame, odds: pd.DataFrame) -> None:
    """Whole-dataset consistency checks (no network)."""
    print("=" * 60)
    print("WHOLE-DATASET INVARIANTS")
    print("=" * 60)
    played = matches[matches.fthg.notna() & matches.ftag.notna()]

    # 1. FTR consistent with the score
    exp = played.apply(lambda r: "H" if r.fthg > r.ftag else
                       ("A" if r.fthg < r.ftag else "D"), axis=1)
    bad_ftr = int((played.ftr != exp).sum())
    print(f"FTR vs score mismatches         : {bad_ftr:,} / {len(played):,}")

    # 2. odds sanity
    bad_odds = int((odds.odds <= 1.0).sum())
    print(f"odds <= 1.0 (invalid price)     : {bad_odds:,} / {len(odds):,}")

    # 3. duplicate match_key within a source
    dup = int(matches.duplicated(["match_key", "source"]).sum())
    print(f"duplicate (match_key, source)   : {dup:,}")

    # 4. 1X2 overround band (Bet365 / SingaporePools open)
    x = odds[(odds.market == "1X2") & (odds.phase == "open") &
             (odds.bookmaker.isin(["Bet365", "SingaporePools"]))]
    piv = x.pivot_table(index=["match_key", "source"], columns="selection",
                        values="odds", aggfunc="first").dropna(subset=["H", "D", "A"])
    over = (1 / piv.H + 1 / piv.D + 1 / piv.A)
    in_band = int(((over >= 1.0) & (over <= 1.20)).sum())
    print(f"1X2 overround in [1.00,1.20]    : {in_band:,} / {len(over):,}  "
          f"(median {over.median():.4f})")

    # 5. expected fixture counts, spot leagues (full seasons only)
    print("fixture-count spot checks (full seasons):")
    for lg, n in _EXPECTED_MATCHES.items():
        sub = matches[(matches.league == lg) & (matches.source == "footballdata")]
        counts = sub.groupby("season").size()
        full = counts[counts == n]
        print(f"  {lg:9s}: {len(full)}/{len(counts)} seasons == {n} matches")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--sg-frac", type=float, default=0.3,
                   help="fraction of sample drawn from sgodds")
    p.add_argument("--skip-invariants", action="store_true")
    args = p.parse_args()

    matches = pd.read_parquet(config.PROCESSED_DIR / "matches_latest.parquet")
    odds = pd.read_parquet(config.PROCESSED_DIR / "odds_latest.parquet")
    print(f"dataset: {len(matches):,} match rows, {len(odds):,} odds rows\n")

    if not args.skip_invariants:
        run_invariants(matches, odds)

    fd_m = matches[matches.source == "footballdata"]
    sg_m = matches[matches.source == "sgodds"]
    n_sg = min(int(round(args.n * args.sg_frac)), len(sg_m))
    n_fd = args.n - n_sg
    sample = pd.concat([
        fd_m.sample(n=min(n_fd, len(fd_m)), random_state=args.seed),
        sg_m.sample(n=n_sg, random_state=args.seed),
    ])
    print(f"stratified sample: {n_fd} football-data + {n_sg} sgodds = {len(sample)}\n")

    odds_idx = {k: g for k, g in odds.groupby(["match_key", "source"])}
    report = Counter()
    all_errs = []

    # football-data: fetch one live file per (league, season)
    fd_sample = sample[sample.source == "footballdata"]
    fd_files = fd_sample.groupby(["league", "season"])
    for (league, season), grp in fd_files:
        try:
            raw_df = _fd_raw(league, season)
        except Exception as exc:  # noqa: BLE001
            report["file_fetch_failed"] += len(grp)
            all_errs.append(f"[FD {league} {season}] file fetch failed: {exc}")
            continue
        for mrow in grp.itertuples(index=False):
            report["matches_checked"] += 1
            om = odds_idx.get((mrow.match_key, "footballdata"), odds.iloc[0:0])
            errs = _validate_fd_match(mrow, om, raw_df, report)
            if errs:
                all_errs.append(f"[FD {league} {season}] {mrow.home_team} v {mrow.away_team} {mrow.date}: "
                                + "; ".join(errs))

    # sgodds: one live file per league
    sg_sample = sample[sample.source == "sgodds"]
    if len(sg_sample):
        try:
            sg_raw = _sg_raw_map()
        except Exception as exc:  # noqa: BLE001
            sg_raw = {}
            all_errs.append(f"[SG] file map fetch failed: {exc}")
        for mrow in sg_sample.itertuples(index=False):
            report["matches_checked"] += 1
            raw_df = sg_raw.get(mrow.league)
            if raw_df is None:
                report["match_not_found"] += 1
                continue
            om = odds_idx.get((mrow.match_key, "sgodds"), odds.iloc[0:0])
            errs = _validate_sg_match(mrow, om, raw_df, report)
            if errs:
                all_errs.append(f"[SG {mrow.league}] {mrow.home_team} v {mrow.away_team} {mrow.date}: "
                                + "; ".join(errs))

    print("=" * 60)
    print("VALIDATION REPORT")
    print("=" * 60)
    print(f"matches checked      : {report['matches_checked']}")
    print(f"  rows not found     : {report['match_not_found']}")
    print(f"  file fetch failed  : {report['file_fetch_failed']}")
    print(f"fields compared      : {report['fields_checked']}  "
          f"(of which odds: {report['odds_checked']})")
    print(f"field mismatches     : {report['fields_mismatch']}")
    acc = (100 * (report['fields_checked'] - report['fields_mismatch'])
           / report['fields_checked']) if report['fields_checked'] else 0
    print(f"field-level accuracy : {acc:.3f}%")
    print()
    if all_errs:
        print(f"--- {len(all_errs)} discrepancy line(s) ---")
        for e in all_errs[:40]:
            print("  " + e)
    else:
        print("No discrepancies: every checked field matched the live source.")


if __name__ == "__main__":
    main()
