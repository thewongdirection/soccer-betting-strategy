"""Team-name normalization + cross-source alias reconciliation.

``normalize_team`` does cheap string cleaning (lowercase, strip punctuation and
generic club suffixes). ``canonical_team`` additionally maps known source-
specific spellings onto a single canonical form so that a fixture from sgodds
and the same fixture from football-data produce the *same* ``match_key`` and can
be joined.

Canonical convention: the **football-data.co.uk** normalized spelling is the
canonical target (it is the deep-history backbone). ``ALIASES`` therefore maps a
normalized *variant* (mostly sgodds spellings) -> the football-data normalized
form. football-data's own names normalize to the canonical form already, so they
need no entries.

The table was generated data-driven by diffing the two sources' team lists per
league (see ``scripts/build_aliases.py``) and then reviewed by hand. Extend it
by running that script for a league and pasting the confirmed rows below.
"""
from __future__ import annotations

import re

_SUFFIXES = re.compile(
    r"\b(fc|afc|cf|sc|ac|ss|as|us|if|bk|fk|sk|ca|cd|ud)\b", re.IGNORECASE
)
_NONALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize_team(name: str) -> str:
    """Lowercase, strip punctuation and generic club suffixes."""
    if not isinstance(name, str):
        return ""
    n = name.strip().lower()
    n = _NONALNUM.sub(" ", n)
    n = _SUFFIXES.sub(" ", n)
    n = _SPACES.sub(" ", n).strip()
    return n


# normalized variant -> canonical (football-data normalized) form.
ALIASES: dict[str, str] = {
    # --- England Premier League ---
    "manchester city": "man city",
    "manchester utd": "man united",
    "nottingham": "nott m forest",
    "wolverhampton": "wolves",
    # --- Spain La Liga ---
    "athletic bilbao": "ath bilbao",
    "atletico madrid": "ath madrid",
    "celta de vigo": "celta",
    "espanyol": "espanol",
    "real betis": "betis",
    # --- Germany Bundesliga ---
    "cologne": "koln",
    "e frankfurt": "ein frankfurt",
    "monchengladbach": "m gladbach",
    # --- France Ligue 1 ---
    "angers sco": "angers",
    # --- Netherlands Eredivisie ---
    "alkmaar": "az alkmaar",
    "fortuna sittard": "for sittard",
    "psv": "psv eindhoven",
    "twente enschede": "twente",
    # --- Sweden Allsvenskan ---
    "aik stockholm": "aik",
    "gais gothenburg": "gais",          # NB: distinct club from IFK Goteborg
    "ifk gothenburg": "goteborg",
    "halmstads": "halmstad",
    "malmo": "malmo ff",
    "mjallby aif": "mjallby",
    # --- USA MLS ---
    "la galaxy": "los angeles galaxy",
    "minnesota utd": "minnesota united",
    "montreal impact": "montreal",       # renamed CF Montreal
    "ne revolution": "new england revolution",
    "ny city": "new york city",
    "ny red bulls": "new york red bulls",
    "philadelphia u": "philadelphia union",
    "portland t": "portland timbers",
    "san jose quakes": "san jose earthquakes",
    "seattle sndrs": "seattle sounders",
    "sporting kc": "sporting kansas city",
    "vancouver w": "vancouver whitecaps",
    # --- Japan J1 League ---
    "fagiano okayama": "okayama",
    "kawasaki f": "kawasaki frontale",
    "kyoto sanga": "kyoto",
    "machida zelvia": "machida",
    "s hiroshima": "sanfrecce hiroshima",
    "tokyo verdy": "verdy",
    "yokohama fm": "yokohama f marinos",
}


def canonical_team(name: str) -> str:
    """Normalized name mapped through the cross-source alias table."""
    n = normalize_team(name)
    return ALIASES.get(n, n)
