"""football-data.co.uk adapter.

Enumerates the available (league, season) CSVs and pulls them into the
normalized schema. Handles both the per-season "main" league files
(mmz4281/<season>/<div>.csv) and the all-seasons "extra" league files
(new/<code>.csv), which have a slightly different layout.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pandas as pd

from .. import config, http
from ..schema import (
    make_fixture_key, make_match_key, result_char, season_from_date,
)

SOURCE = "footballdata"


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FDItem:
    league: str          # normalized league id
    league_name: str
    season: str          # label, e.g. "2025-2026"; "" for extra (all-seasons)
    url: str
    kind: str            # "main" | "extra"
    div: str             # div or country code


def enumerate_items(
    leagues: list[str] | None = None,
    start_year: int | None = None,
    end_year: int = 2025,
    include_extra: bool = True,
) -> list[FDItem]:
    """List every candidate CSV. ``leagues`` filters by normalized id.

    This does NOT hit the network -- it builds the URL space from config. Use
    :func:`probe` if you want to confirm a file actually exists.
    """
    start_year = start_year or config.FD_FIRST_SEASON_YEAR
    want = set(leagues) if leagues else None
    items: list[FDItem] = []

    seasons = config.fd_season_codes(start_year, end_year)
    for div, (lid, name) in config.FD_MAIN_LEAGUES.items():
        if want and lid not in want:
            continue
        for code, label in seasons:
            items.append(FDItem(
                league=lid, league_name=name, season=label,
                url=config.FD_MAIN_URL.format(season=code, div=div),
                kind="main", div=div,
            ))

    if include_extra:
        for code, (lid, name) in config.FD_EXTRA_LEAGUES.items():
            if want and lid not in want:
                continue
            items.append(FDItem(
                league=lid, league_name=name, season="(all)",
                url=config.FD_EXTRA_URL.format(code=code),
                kind="extra", div=code,
            ))
    return items


def probe(item: FDItem) -> bool:
    return http.url_exists(item.url)


# ---------------------------------------------------------------------------
# Odds-column classification
# ---------------------------------------------------------------------------
# Bookmaker prefix -> canonical name. "Max"/"Avg" are the market-wide best/mean.
_BOOK = {
    "B365": "Bet365", "BW": "BetWin", "IW": "Interwetten", "PS": "Pinnacle",
    "P": "Pinnacle", "WH": "WilliamHill", "VC": "VCBet", "LB": "Ladbrokes",
    "SB": "Sportingbet", "SJ": "StanJames", "GB": "Gamebookers",
    "BS": "BlueSquare", "SO": "Sporting", "BF": "Betfair",
    "Max": "MarketMax", "Avg": "MarketAvg",
}

_1X2_BOOKS = "B365|BW|IW|PS|WH|VC|LB|SB|SJ|GB|BS|SO|BF|Max|Avg"
_OU_AH_BOOKS = "B365|P|Max|Avg"

_RE_1X2 = re.compile(rf"^(?P<bk>{_1X2_BOOKS})(?P<c>C?)(?P<sel>[HDA])$")
_RE_OU = re.compile(rf"^(?P<bk>{_OU_AH_BOOKS})(?P<c>C?)(?P<side>[<>])(?P<line>\d+(?:\.\d+)?)$")
_RE_AH = re.compile(rf"^(?P<bk>{_OU_AH_BOOKS})(?P<c>C?)AH(?P<sel>[HA])$")


def _classify(col: str):
    """Return (bookmaker, market, selection, phase) or None for a column name.

    The Asian-handicap *line* itself lives in separate columns (AHh open /
    AHCh close) and is attached later, so AH odds carry line=None here.
    """
    m = _RE_1X2.match(col)
    if m:
        phase = "close" if m["c"] else "open"
        return _BOOK.get(m["bk"], m["bk"]), "1X2", m["sel"], phase, None
    m = _RE_OU.match(col)
    if m:
        phase = "close" if m["c"] else "open"
        sel = "OVER" if m["side"] == ">" else "UNDER"
        return _BOOK.get(m["bk"], m["bk"]), "OU", sel, phase, float(m["line"])
    m = _RE_AH.match(col)
    if m:
        phase = "close" if m["c"] else "open"
        sel = "HOME" if m["sel"] == "H" else "AWAY"
        return _BOOK.get(m["bk"], m["bk"]), "AH", sel, phase, None
    return None


# football-data uses several date formats over the years.
def _parse_dates(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    missing = d.isna()
    if missing.any():  # older files used 2-digit years
        d2 = pd.to_datetime(s[missing], format="%d/%m/%y", errors="coerce")
        d.loc[missing] = d2
    return d


def _coerce_num(df: pd.DataFrame, cols) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")


# Column name differences between "main" and "extra" file layouts.
_EXTRA_RENAME = {
    "Home": "HomeTeam", "Away": "AwayTeam",
    "HG": "FTHG", "AG": "FTAG", "Res": "FTR",
    "PH": "PSH", "PD": "PSD", "PA": "PSA",   # extra files: Pinnacle 1X2 as P*
    "PCH": "PSCH", "PCD": "PSCD", "PCA": "PSCA",
}


def _normalize_frame(df: pd.DataFrame, league: str, kind: str,
                     odds_cols: list[str], classify_map: dict[str, tuple]):
    """Turn one raw football-data CSV frame into (matches, odds) rows."""
    df = df.copy()
    if kind == "extra":
        df = df.rename(columns={k: v for k, v in _EXTRA_RENAME.items()
                                if k in df.columns})

    df = df.dropna(how="all")
    if "HomeTeam" not in df.columns or "Date" not in df.columns:
        return [], []

    df["_date"] = _parse_dates(df["Date"])
    df = df[df["_date"].notna() & df["HomeTeam"].notna()].copy()
    if df.empty:
        return [], []

    _coerce_num(df, ["FTHG", "FTAG", "HTHG", "HTAG", "AHh", "AHCh"])

    match_rows = []
    odds_rows = []

    for _, r in df.iterrows():
        home, away = str(r["HomeTeam"]).strip(), str(r["AwayTeam"]).strip()
        date_iso = r["_date"].date().isoformat()
        # Derive the season label the same way for every source and file so that
        # match_key aligns cross-source. (football-data's extra files carry their
        # own Season column, but using it would diverge from sgodds' derivation.)
        season = season_from_date(r["_date"])
        mkey = make_match_key(league, season, date_iso, home, away)
        fkey = make_fixture_key(league, season, home, away)

        fthg, ftag = r.get("FTHG"), r.get("FTAG")
        ftr = r.get("FTR")
        if not isinstance(ftr, str) or ftr not in ("H", "D", "A"):
            ftr = result_char(fthg, ftag)

        match_rows.append({
            "match_key": mkey, "fixture_key": fkey, "source": SOURCE,
            "league": league, "season": season, "date": date_iso,
            "kickoff": (str(r["Time"]) if "Time" in df.columns
                        and pd.notna(r.get("Time")) else None),
            "home_team": home, "away_team": away,
            "fthg": fthg, "ftag": ftag, "ftr": ftr,
            "hthg": r.get("HTHG"), "htag": r.get("HTAG"),
            "htr": (r.get("HTR") if isinstance(r.get("HTR"), str)
                    else result_char(r.get("HTHG"), r.get("HTAG"))),
        })

        ah_open_line = r.get("AHh")
        ah_close_line = r.get("AHCh")
        base = {
            "match_key": mkey, "fixture_key": fkey, "source": SOURCE,
            "league": league, "season": season, "date": date_iso,
            "home_team": home, "away_team": away,
        }
        for col in odds_cols:
            val = r.get(col)
            if pd.isna(val):
                continue
            try:
                odds = float(val)
            except (TypeError, ValueError):
                continue
            if odds <= 1.0:      # invalid / void price
                continue
            bk, market, sel, phase, line = classify_map[col]
            if market == "AH":
                line = ah_close_line if phase == "close" else ah_open_line
                if pd.isna(line):
                    line = None
            odds_rows.append({**base, "bookmaker": bk, "market": market,
                              "selection": sel, "line": line,
                              "phase": phase, "odds": odds})
    return match_rows, odds_rows


def pull_item(item: FDItem, use_cache: bool = True):
    """Download + normalize a single enumerated item -> (matches, odds) rows."""
    raw = http.get_bytes(item.url, use_cache=use_cache, suffix=".csv")
    # football-data CSVs are latin-1 and sometimes have trailing junk columns.
    df = pd.read_csv(io.BytesIO(raw), encoding="latin-1",
                     on_bad_lines="skip", low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]

    classify_map: dict[str, tuple] = {}
    odds_cols: list[str] = []
    for c in df.columns:
        cls = _classify(c)
        if cls is not None:
            classify_map[c] = cls
            odds_cols.append(c)

    return _normalize_frame(df, item.league, item.kind, odds_cols, classify_map)
