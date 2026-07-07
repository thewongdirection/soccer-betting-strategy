"""The source-agnostic normalized schema + helpers to build it.

Two tables:

``matches`` (identity + outcomes, one row per fixture per source)
    match_key, source, league, season, date, kickoff, home_team, away_team,
    fthg, ftag, ftr, hthg, htag, htr

``odds`` (long / tidy, one row per priced selection)
    match_key, source, league, season, date, home_team, away_team,
    bookmaker, market, selection, line, phase, odds

Markets and selections use a fixed vocabulary so a backtester can settle bets
uniformly regardless of which source the odds came from:

    market="1X2"  selection in {H, D, A}                 line=NaN
    market="OU"   selection in {OVER, UNDER}             line=goal line (2.5)
    market="AH"   selection in {HOME, AWAY}              line=handicap
    phase in {open, close}
"""
from __future__ import annotations

import hashlib

import pandas as pd

# Re-exported for backwards compatibility; canonicalization lives in naming.
from .naming import canonical_team, normalize_team  # noqa: F401

MATCH_COLUMNS = [
    "match_key", "fixture_key", "source", "league", "season", "date", "kickoff",
    "home_team", "away_team",
    "fthg", "ftag", "ftr", "hthg", "htag", "htr",
]

ODDS_COLUMNS = [
    "match_key", "fixture_key", "source", "league", "season", "date",
    "home_team", "away_team",
    "bookmaker", "market", "selection", "line", "phase", "odds",
]

def make_match_key(league: str, season: str, date: str, home: str, away: str) -> str:
    """Unique-within-source id: league + season + date + canonical teams.

    The date is included so that leagues where two teams meet more than once in
    the same orientation per season (Scotland, Austria, Switzerland, ... where
    clubs play 3-4 times) don't collide. This is the primary key and the key to
    settle bets on. For joining the *same* fixture across sources use
    :func:`make_fixture_key` instead (sgodds is SGT-stamped, so dates can differ
    by a day across sources).
    """
    raw = f"{league}|{season}|{date}|{canonical_team(home)}|{canonical_team(away)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def make_fixture_key(league: str, season: str, home: str, away: str) -> str:
    """Date-robust, source-agnostic id for cross-source joins.

    Keyed on league + season + canonical team names (via
    :func:`naming.canonical_team`), deliberately **not** the date so the same
    fixture from sgodds (SGT) and football-data (match-local) shares a key. This
    is unique per season in the round-robin-twice leagues where sgodds overlaps
    football-data (home and away legs differ); it can be ambiguous in leagues
    where the same pairing is played 3+ times, which sgodds rarely covers.
    """
    raw = f"{league}|{season}|{canonical_team(home)}|{canonical_team(away)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def result_char(hg, ag) -> str | None:
    """H/D/A from home/away goals."""
    if pd.isna(hg) or pd.isna(ag):
        return None
    if hg > ag:
        return "H"
    if hg < ag:
        return "A"
    return "D"


def season_from_date(date: pd.Timestamp) -> str:
    """Infer a season label from a match date (European Aug-May calendar)."""
    if pd.isna(date):
        return ""
    y = date.year
    # Season rolls over in summer; treat July+ as the start of a new season.
    if date.month >= 7:
        return f"{y}-{y + 1}"
    return f"{y - 1}-{y}"


def empty_matches() -> pd.DataFrame:
    return pd.DataFrame(columns=MATCH_COLUMNS)


def empty_odds() -> pd.DataFrame:
    return pd.DataFrame(columns=ODDS_COLUMNS)
