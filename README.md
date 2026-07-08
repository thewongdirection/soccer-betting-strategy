# soccer-betting-strategy

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

## Prerequisites

- **Python 3.10+**.
- **Dependencies** — install once from the repo root:
  ```bash
  pip install -r requirements.txt
  ```
  `requests` + `beautifulsoup4` + `lxml` (fetching/scraping), `pandas` +
  `pyarrow` (data + Parquet), and `openpyxl` (only used by
  `scripts/strategy-1-report.py`, the Excel builder).
- **Run every command from the repo root** (`soccer-betting-strategy/`), as
  `python scripts/<name>.py`. Each script prepends the project root to
  `sys.path`, so `import soccer_backtest` and the `data/` paths resolve
  correctly; running from elsewhere will not find the package or the data.
- **Network access** — only `pull_data.py` and `validate_dataset.py` reach the
  internet (football-data.co.uk and sgodds.com). Requests are throttled
  (~1.5 s/host) and cached under `data/raw/`, so re-runs are offline and free;
  pass `--no-cache` to force a refresh. Every other script is fully offline.
- **Pull before you backtest** — `data/` is git-ignored, so a fresh clone has no
  data. The backtest/report scripts read `data/processed/*.parquet`, which the
  pull produces. A full two-source pull is ~600 files (~15–20 min the first
  time, then seconds from cache); the raw cache is ~65 MB and the SQLite DB
  ~1.7 GB. Ensure `data/` is writable.

## Running the scripts

End-to-end, from a fresh clone:

```bash
pip install -r requirements.txt                # 1. dependencies
python scripts/pull_data.py all --format both  # 2. fetch + normalize -> data/processed/
python scripts/validate_dataset.py             # 3. (optional) verify vs live sources
python scripts/strategy-1.py                    # 4. run Strategy 1 -> bet-log + weekly CSVs
python scripts/strategy-1-report.py             # 5. build the Excel workbook
```

| Script | What it does | Network | Needs first |
|--------|--------------|:-------:|-------------|
| `enumerate_sources.py <src>` | List the leagues/seasons a source offers | scrape (sgodds only) | — |
| `pull_data.py <src>` | Fetch, normalize and store odds + results | yes | — |
| `validate_dataset.py` | Re-check a sample vs live source files + run invariants | yes | a pull |
| `build_aliases.py` | Propose cross-source team-name aliases | yes | — |
| `strategy-1.py` | Strategy 1 EPL backtest → CSVs | no | a pull |
| `strategy-1-markets.py` | Strategy 1 across all sources/markets | no | a pull |
| `strategy-1-report.py` | Excel workbook from the Strategy 1 CSVs | no | `strategy-1.py` |

`<src>` is `footballdata`, `sgodds`, or `all`. Add `-h`/`--help` to any script
for its full options.

**Enumerate** (footballdata builds URLs offline; `--probe` verifies each over HTTP):
```bash
python scripts/enumerate_sources.py sgodds
python scripts/enumerate_sources.py footballdata --leagues ENG-PREM ITA-SA
```

**Pull** into `data/processed/`:
```bash
python scripts/pull_data.py all --format both                     # everything, Parquet + SQLite
python scripts/pull_data.py footballdata --leagues ENG-PREM ESP-LL \
    ITA-SA GER-BL1 FRA-L1 --start-year 2015 --end-year 2025       # subset of leagues/seasons
python scripts/pull_data.py sgodds                                # current Singapore Pools odds
```
Flags: `--format {parquet,sqlite,both}`, `--leagues`, `--start-year/--end-year`,
`--no-cache`.

**Strategy 1** reads `matches_latest.parquet` + `odds_latest.parquet`; tune the
constants at the top of `scripts/strategy-1.py` (`SEASONS`, `START_CAPITAL`,
`THRESHOLD`, `TG_N`, `MAX_GAMES_PER_WEEK` — `None` = every qualifying game).
`strategy-1-report.py` reads the two CSVs that `strategy-1.py` writes, so run it
second.

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

## Strategy 1 — favourite / total-goals coverage

A flat-stake strategy that turns a bookmaker's own prices into de-vigged implied
probabilities `p_i = (1/odds_i) / Σ(1/odds_j)` and backs the outcomes the market
itself rates most likely (no independent model):

- **1X2** — stake $1 on the single most likely result (H/D/A) only if its
  probability exceeds **70%** (i.e. only strong favourites).
- **Total goals** (exact full-time total, 0…9 where 9 = "9 or more") — stake $1
  on each of the **2 most probable** goal totals. (A legacy "coverage" mode bets
  the fewest top totals summing past 70%; set `top_n=None` in the engine.)

Bets settle at the same odds used to price them, so each bet's expected value is
negative by the book's margin (~5–7% on 1X2, ~25–30% on exact totals) — the
backtest measures the strategy's realised edge against that margin, not a
mispricing model.

Files:

| File | Role |
|------|------|
| `soccer_backtest/strategy_1.py` | Engine: `implied_probs`, `backtest_1x2`, `backtest_tg`, `equity_curve`. Full rules in the module docstring. |
| `scripts/strategy-1.py` | Canonical run — EPL, last 2 seasons, $1,000 bankroll, ≤5 games/week (top confidence), weekly P&L + drawdown. |
| `scripts/strategy-1-markets.py` | Broad cross-source / cross-market P&L comparison over the full dataset. |
| `scripts/strategy-1-report.py` | Builds the formatted Excel workbook from the run's CSVs. |

Run these with `python scripts/strategy-1.py` then `scripts/strategy-1-report.py`
(see [Running the scripts](#running-the-scripts); a data pull must exist first).

Result on EPL 2024-25 + 2025-26 (Bet365 closing 1X2, Singapore Pools total goals
for 2025-26): **−11.4% ROI**, final bankroll **$953.7 / $1,000**, max drawdown
**−$53.8 (−5.4%)**. The 1X2 favourite leg is ≈break-even; the entire loss is the
total-goals leg paying into the exotic market's large margin.

## Known limitations / next steps

- Season labels are derived uniformly via `season_from_date` (Aug–Jul boundary)
  so keys align across sources. For calendar-year leagues (MLS, Allsvenskan,
  J-League) this label is an approximation, not the official season name.
- sgodds history is shallow (~2025-onward); for long backtests rely on
  footballdata. Exact total-goals odds are sgodds-only (from 2025-26).
- Data outputs (`data/`) are git-ignored — regenerate with the scripts above.
