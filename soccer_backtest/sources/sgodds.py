"""sgodds.com adapter -- Singapore Pools *opening* odds + HT/FT scores.

Two access paths:

* ``enumerate_downloads`` / ``pull_download`` -- the per-league CSV downloads
  linked from /football/data. Each row carries opening odds across many
  markets plus the final result, so one file yields both odds and outcomes.
  The download filenames embed a timestamp that changes, so links must be
  scraped fresh rather than constructed.
* ``pull_results_page`` -- fallback scraper of the paginated HTML results pages
  (FT scores + 1X2), for when a league isn't offered as a CSV.

Column semantics confirmed from a real file header:
    Match       "Home vs Away"
    Start Time  "YYYY-MM-DD HH:MM:SS"  (SGT)
    Result      "HT:0-1, FT:1-1"
    Ft1X2_01/02/03   1X2 Home / Draw / Away
    Ou_hcap / Ou_01 / Ou_02   total-goals line / Over / Under
    Ah_01_Hcap / Ah_01        Asian handicap: home line / home odds
    Ah_02_Hcap / Ah_02        Asian handicap: away line / away odds
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pandas as pd
from bs4 import BeautifulSoup

from .. import config, http
from ..schema import (
    make_fixture_key, make_match_key, result_char, season_from_date,
)

SOURCE = "sgodds"
BOOKMAKER = "SingaporePools"

_DL_RE = re.compile(r"/downloads/sgodds-\d+-(?P<slug>[a-z0-9-]+)\.csv")
_RESULT_RE = re.compile(
    r"HT:\s*(?P<hth>\d+)\s*-\s*(?P<hta>\d+).*?FT:\s*(?P<fth>\d+)\s*-\s*(?P<fta>\d+)",
    re.IGNORECASE,
)
_VS_RE = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)


@dataclass(frozen=True)
class SGDownload:
    league: str          # normalized league id (or slug if unmapped)
    slug: str
    url: str


def _slug_to_league(slug: str) -> str:
    for known, lid in config.SG_LEAGUE_SLUGS.items():
        if slug.startswith(known):
            return lid
    return slug  # unmapped: keep slug so nothing is silently dropped


def enumerate_downloads(use_cache: bool = True) -> list[SGDownload]:
    """Scrape /football/data for the current per-league CSV download links."""
    html = http.get_text(config.SG_DATA_PAGE, use_cache=use_cache)
    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, SGDownload] = {}
    for a in soup.find_all("a", href=True):
        m = _DL_RE.search(a["href"])
        if not m:
            continue
        href = a["href"]
        url = href if href.startswith("http") else config.SG_BASE + href
        slug = m.group("slug")
        # keep the last (most recent) link per slug
        seen[slug] = SGDownload(league=_slug_to_league(slug), slug=slug, url=url)
    return list(seen.values())


def _split_teams(match: str) -> tuple[str, str]:
    parts = _VS_RE.split(str(match), maxsplit=1)
    if len(parts) != 2:
        return str(match).strip(), ""
    return parts[0].strip(), parts[1].strip()


def _parse_result(text) -> tuple:
    """-> (fthg, ftag, ftr, hthg, htag, htr). NaN-filled if unparseable."""
    if not isinstance(text, str):
        return (None, None, None, None, None, None)
    m = _RESULT_RE.search(text)
    if not m:
        return (None, None, None, None, None, None)
    hth, hta = int(m["hth"]), int(m["hta"])
    fth, fta = int(m["fth"]), int(m["fta"])
    return (fth, fta, result_char(fth, fta), hth, hta, result_char(hth, hta))


def _num(row, col):
    if col not in row:
        return None
    return pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]


def _normalize_download(df: pd.DataFrame, league: str):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    match_rows, odds_rows = [], []

    for _, r in df.iterrows():
        home, away = _split_teams(r.get("Match", ""))
        if not home or not away:
            continue
        start = pd.to_datetime(r.get("Start Time"), errors="coerce")
        if pd.isna(start):
            continue
        date_iso = start.date().isoformat()
        season = season_from_date(start)
        mkey = make_match_key(league, season, date_iso, home, away)
        fkey = make_fixture_key(league, season, home, away)
        fthg, ftag, ftr, hthg, htag, htr = _parse_result(r.get("Result"))

        match_rows.append({
            "match_key": mkey, "fixture_key": fkey, "source": SOURCE,
            "league": league, "season": season, "date": date_iso,
            "kickoff": start.strftime("%H:%M"),
            "home_team": home, "away_team": away,
            "fthg": fthg, "ftag": ftag, "ftr": ftr,
            "hthg": hthg, "htag": htag, "htr": htr,
        })

        base = {
            "match_key": mkey, "fixture_key": fkey, "source": SOURCE,
            "league": league, "season": season, "date": date_iso,
            "home_team": home, "away_team": away,
            "bookmaker": BOOKMAKER, "phase": "open",
        }

        def add(market, selection, line, odds):
            if odds is None or pd.isna(odds) or float(odds) <= 1.0:
                return
            odds_rows.append({**base, "market": market, "selection": selection,
                              "line": (None if line is None or pd.isna(line)
                                       else float(line)),
                              "odds": float(odds)})

        # 1X2
        add("1X2", "H", None, _num(r, "Ft1X2_01"))
        add("1X2", "D", None, _num(r, "Ft1X2_02"))
        add("1X2", "A", None, _num(r, "Ft1X2_03"))
        # Over / Under total goals
        ou_line = _num(r, "Ou_hcap")
        add("OU", "OVER", ou_line, _num(r, "Ou_01"))
        add("OU", "UNDER", ou_line, _num(r, "Ou_02"))
        # Asian handicap (per-side lines)
        add("AH", "HOME", _num(r, "Ah_01_Hcap"), _num(r, "Ah_01"))
        add("AH", "AWAY", _num(r, "Ah_02_Hcap"), _num(r, "Ah_02"))

    return match_rows, odds_rows


def pull_download(dl: SGDownload, use_cache: bool = True):
    raw = http.get_bytes(dl.url, use_cache=use_cache, suffix=".csv")
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    return _normalize_download(df, dl.league)


# ---------------------------------------------------------------------------
# HTML results pages (fallback outcome/1X2 source)
# ---------------------------------------------------------------------------
def results_page_count(use_cache: bool = True) -> int:
    """Best-effort read of the number of paginated results pages."""
    html = http.get_text(config.SG_RESULTS_PAGE, use_cache=use_cache)
    soup = BeautifulSoup(html, "lxml")
    pages = 1
    for a in soup.find_all("a", href=True):
        m = re.search(r"/results-past-odds/page/(\d+)", a["href"])
        if m:
            pages = max(pages, int(m.group(1)))
    return pages
