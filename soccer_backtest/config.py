"""Static configuration: source URLs, league maps, season codes.

Everything source-specific that a human might want to tweak lives here so the
fetchers stay generic.
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # unmodified downloads (also HTTP cache)
PROCESSED_DIR = DATA_DIR / "processed"  # normalized parquet / sqlite

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
USER_AGENT = (
    "soccer-backtest/0.1 (research; data pull for personal backtesting)"
)
# Minimum seconds between requests to the same host. football-data.co.uk will
# return HTTP 429 if hit too fast, so be polite.
REQUEST_MIN_INTERVAL = 1.5
REQUEST_TIMEOUT = 30

# ===========================================================================
# football-data.co.uk
# ===========================================================================
FD_BASE = "https://www.football-data.co.uk"
# Per-season "main" league files: {FD_BASE}/mmz4281/{season}/{div}.csv
FD_MAIN_URL = FD_BASE + "/mmz4281/{season}/{div}.csv"
# "Extra" leagues: one file spanning all seasons: {FD_BASE}/new/{code}.csv
FD_EXTRA_URL = FD_BASE + "/new/{code}.csv"

# div code -> (normalized league id, human name). The normalized id is shared
# with sgodds where the leagues line up, so the two sources can be joined.
FD_MAIN_LEAGUES: dict[str, tuple[str, str]] = {
    "E0": ("ENG-PREM", "England Premier League"),
    "E1": ("ENG-CHAMP", "England Championship"),
    "E2": ("ENG-L1", "England League One"),
    "E3": ("ENG-L2", "England League Two"),
    "EC": ("ENG-NL", "England National League"),
    "SC0": ("SCO-PREM", "Scotland Premiership"),
    "SC1": ("SCO-CHAMP", "Scotland Championship"),
    "SC2": ("SCO-L1", "Scotland League One"),
    "SC3": ("SCO-L2", "Scotland League Two"),
    "D1": ("GER-BL1", "Germany Bundesliga"),
    "D2": ("GER-BL2", "Germany 2. Bundesliga"),
    "I1": ("ITA-SA", "Italy Serie A"),
    "I2": ("ITA-SB", "Italy Serie B"),
    "SP1": ("ESP-LL", "Spain La Liga"),
    "SP2": ("ESP-LL2", "Spain La Liga 2"),
    "F1": ("FRA-L1", "France Ligue 1"),
    "F2": ("FRA-L2", "France Ligue 2"),
    "N1": ("NED-ERE", "Netherlands Eredivisie"),
    "B1": ("BEL-PRO", "Belgium Pro League"),
    "P1": ("POR-PRI", "Portugal Primeira Liga"),
    "T1": ("TUR-SL", "Turkey Super Lig"),
    "G1": ("GRE-SL", "Greece Super League"),
}

# country code -> (normalized league id, human name)
FD_EXTRA_LEAGUES: dict[str, tuple[str, str]] = {
    "ARG": ("ARG-PD", "Argentina Primera Division"),
    "AUT": ("AUT-BL", "Austria Bundesliga"),
    "BRA": ("BRA-SA", "Brazil Serie A"),
    "CHN": ("CHN-SL", "China Super League"),
    "DNK": ("DEN-SL", "Denmark Superliga"),
    "FIN": ("FIN-VL", "Finland Veikkausliiga"),
    "IRL": ("IRL-PD", "Ireland Premier Division"),
    "JPN": ("JPN-J1", "Japan J1 League"),
    "MEX": ("MEX-LMX", "Mexico Liga MX"),
    "NOR": ("NOR-EL", "Norway Eliteserien"),
    "POL": ("POL-EK", "Poland Ekstraklasa"),
    "ROU": ("ROU-L1", "Romania Liga 1"),
    "RUS": ("RUS-PL", "Russia Premier League"),
    "SWE": ("SWE-AS", "Sweden Allsvenskan"),
    "SWZ": ("SUI-SL", "Switzerland Super League"),
    "USA": ("USA-MLS", "USA MLS"),
}

# First season for which football-data has broad odds coverage.
FD_FIRST_SEASON_YEAR = 2000


def fd_season_codes(start_year: int, end_year: int) -> list[tuple[str, str]]:
    """Return [(code, label), ...] for seasons ``start_year``..``end_year``.

    ``start_year`` is the calendar year the season begins, so 2025 -> season
    "2526", labelled "2025-2026".
    """
    out = []
    for y in range(start_year, end_year + 1):
        code = f"{y % 100:02d}{(y + 1) % 100:02d}"
        out.append((code, f"{y}-{y + 1}"))
    return out


# ===========================================================================
# sgodds.com  (Singapore Pools opening odds)
# ===========================================================================
SG_BASE = "https://sgodds.com"
SG_DATA_PAGE = SG_BASE + "/football/data"          # per-league CSV download links
SG_RESULTS_PAGE = SG_BASE + "/football/results-past-odds"  # HTML results + odds

# Map a slug found inside a download filename (sgodds-<ts>-<slug>.csv) to a
# normalized league id. Slugs are matched by "startswith", longest-first, so
# order the more specific slugs before their prefixes.
SG_LEAGUE_SLUGS: dict[str, str] = {
    "english-league-championship": "ENG-CHAMP",
    "english-premier": "ENG-PREM",
    "spanish-league": "ESP-LL",
    "italian-league": "ITA-SA",
    "german-league": "GER-BL1",
    "french-league": "FRA-L1",
    "dutch-league": "NED-ERE",
    "swedish-league": "SWE-AS",
    "us-soccer-league": "USA-MLS",
    "j-league": "JPN-J1",
    "k-league": "KOR-K1",
    "a-league": "AUS-AL",
}
