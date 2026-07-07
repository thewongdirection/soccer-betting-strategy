# soccer-backtest

Data pipeline for backtesting soccer betting strategies. Pulls **historical
odds** and **final outcomes** from two sources into one normalized schema, so a
backtester can settle bets without caring where the data came from.

## Sources

| Source | What | History | Odds |
|--------|------|---------|------|
| `footballdata` — [football-data.co.uk](https://www.football-data.co.uk/data.php) | 25+ leagues, results + odds | 2000/01 → current | Bet365, Pinnacle, market avg/max; **opening + closing**; 1X2, O/U 2.5, Asian handicap |
| `sgodds` — [sgodds.com](https://sgodds.com/football/data) | Singapore Pools opening odds + HT/FT scores | ~2025 → current (shallow) | Singapore Pools **opening** only; 1X2, O/U, Asian handicap (+ many exotics available in raw) |

Use `footballdata` for statistically meaningful backtests (deep history, sharp
Pinnacle prices). Use `sgodds` if the strategy is specifically about betting
through Singapore Pools. Both normalize into the same tables.

## Install

```bash
pip install -r requirements.txt
```

## Usage

Enumerate what's available (footballdata builds URLs offline; `--probe` verifies
each over HTTP):

```bash
python scripts/enumerate_sources.py sgodds
python scripts/enumerate_sources.py footballdata --leagues ENG-PREM ITA-SA
```

Pull data into `data/processed/`:

```bash
# Big-5 leagues, last 10 seasons
python scripts/pull_data.py footballdata \
    --leagues ENG-PREM ESP-LL ITA-SA GER-BL1 FRA-L1 \
    --start-year 2015 --end-year 2025

# Current Singapore Pools odds + results
python scripts/pull_data.py sgodds

# Everything from both sources
python scripts/pull_data.py all --format both
```

Raw downloads are cached under `data/raw/` (keyed by URL) so re-runs are free
and don't re-hit the servers; `--no-cache` forces a refresh.

## Output schema (`data/processed/`)

`matches` — one row per fixture per source:

```
match_key, source, league, season, date, kickoff, home_team, away_team,
fthg, ftag, ftr, hthg, htag, htr
```

`odds` — long/tidy, one row per priced selection:

```
match_key, source, league, season, date, home_team, away_team,
bookmaker, market, selection, line, phase, odds
```

| market | selection | line | notes |
|--------|-----------|------|-------|
| `1X2` | `H` / `D` / `A` | — | match result |
| `OU` | `OVER` / `UNDER` | goal line, e.g. `2.5` | total goals |
| `AH` | `HOME` / `AWAY` | handicap, e.g. `-0.25` | Asian handicap |

`phase` is `open` or `close` (football-data has both; sgodds is `open` only).

Written to both Parquet (`matches_latest.parquet`, `odds_latest.parquet`) and
SQLite (`soccer.db`, tables `matches` + `odds`, indexed, idempotent upserts).

### Quick load

```python
import pandas as pd
odds = pd.read_parquet("data/processed/odds_latest.parquet")
matches = pd.read_parquet("data/processed/matches_latest.parquet")

# Bet365 closing home-win prices joined to the actual result:
home = odds.query("bookmaker=='Bet365' and market=='1X2' "
                  "and selection=='H' and phase=='close'")
df = home.merge(matches[['match_key','source','ftr']],
                on=['match_key','source'])
```

## Cross-source joins

`match_key` is source-agnostic: it hashes `league + season + canonical home +
canonical away` (see `naming.py`), deliberately **not** the date — sgodds stamps
are SGT while football-data dates are match-local, so the same fixture can fall
on different calendar days. Team spellings are reconciled through
`naming.ALIASES` (e.g. sgodds `Manchester Utd` → football-data `Man United`).

Result: the same fixture from both sources shares a `match_key`, so you can join
Singapore Pools' opening price to football-data's deep history / closing line:

```python
import pandas as pd
o = pd.read_parquet("data/processed/odds_latest.parquet")
sp = o.query("source=='sgodds' and market=='1X2' and selection=='H'")
b365 = o.query("source=='footballdata' and bookmaker=='Bet365' "
               "and market=='1X2' and selection=='H' and phase=='close'")
cmp = sp.merge(b365, on='match_key', suffixes=('_sp', '_b365'))  # SP open vs B365 close
```

Reconciliation is ~100% on shared top-flight fixtures (EPL 350/350, La Liga
349/349, MLS 285/285, J-League 98/98). The few residual misses are genuine data
gaps — relegation-playoff games or lower-division fixtures football-data's file
doesn't carry — not aliasing failures.

Rebuild/extend the alias table for any league with:

```bash
python scripts/build_aliases.py --leagues ENG-PREM ESP-LL
```

It diffs both sources' team lists, fuzzy-matches the gaps, and prints ready-to-
paste `ALIASES` rows for review.

## Known limitations / next steps

- Season labels are derived uniformly via `season_from_date` (Aug–Jul boundary)
  so keys align across sources. For calendar-year leagues (MLS, Allsvenskan,
  J-League) this label is an approximation, not the official season name.
- sgodds history is shallow (~2025-onward); for long backtests rely on
  footballdata.
- The backtester/strategy engine is the next module — this repo currently
  covers **enumerate + pull + normalize + store** only.
