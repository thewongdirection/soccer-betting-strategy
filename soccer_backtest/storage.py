"""Persist normalized (matches, odds) frames to Parquet and/or SQLite.

Both writers are idempotent: matches are keyed by (match_key, source) and odds
by (match_key, source, bookmaker, market, selection, line, phase), so re-pulling
the same data replaces rather than duplicates rows.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from . import config
from .schema import MATCH_COLUMNS, ODDS_COLUMNS

MATCH_PK = ["match_key", "source"]
ODDS_PK = ["match_key", "source", "bookmaker", "market", "selection", "line", "phase"]


def _dedup(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    # line is part of the odds key but is nullable; fill for stable dedup.
    tmp = df.copy()
    for k in keys:
        if k == "line":
            tmp[k] = tmp[k].fillna(-999.0)
    tmp = tmp.drop_duplicates(subset=keys, keep="last")
    return df.loc[tmp.index]


def write_parquet(matches: pd.DataFrame, odds: pd.DataFrame, tag: str = "latest") -> None:
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    matches = _dedup(matches[MATCH_COLUMNS], MATCH_PK)
    odds = _dedup(odds[ODDS_COLUMNS], ODDS_PK)
    matches.to_parquet(config.PROCESSED_DIR / f"matches_{tag}.parquet", index=False)
    odds.to_parquet(config.PROCESSED_DIR / f"odds_{tag}.parquet", index=False)


_MATCH_DDL = f"""
CREATE TABLE IF NOT EXISTS matches (
    {", ".join(MATCH_COLUMNS)},
    PRIMARY KEY (match_key, source)
);
"""
_ODDS_DDL = f"""
CREATE TABLE IF NOT EXISTS odds (
    {", ".join(ODDS_COLUMNS)},
    PRIMARY KEY (match_key, source, bookmaker, market, selection, line, phase)
);
"""


def _upsert(conn: sqlite3.Connection, table: str, df: pd.DataFrame, cols: list[str]) -> int:
    if df.empty:
        return 0
    placeholders = ", ".join("?" for _ in cols)
    sql = (f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
           f"VALUES ({placeholders})")
    rows = [tuple(None if pd.isna(v) else v for v in rec)
            for rec in df[cols].itertuples(index=False, name=None)]
    conn.executemany(sql, rows)
    return len(rows)


def write_sqlite(matches: pd.DataFrame, odds: pd.DataFrame,
                 db_path=None) -> tuple[int, int]:
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    db_path = db_path or (config.PROCESSED_DIR / "soccer.db")
    matches = _dedup(matches[MATCH_COLUMNS], MATCH_PK)
    odds = _dedup(odds[ODDS_COLUMNS], ODDS_PK)
    with sqlite3.connect(db_path) as conn:
        conn.execute(_MATCH_DDL)
        conn.execute(_ODDS_DDL)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_odds_match ON odds(match_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_matches_date ON matches(date)")
        n_m = _upsert(conn, "matches", matches, MATCH_COLUMNS)
        n_o = _upsert(conn, "odds", odds, ODDS_COLUMNS)
        conn.commit()
    return n_m, n_o
