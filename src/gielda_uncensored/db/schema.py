"""SQLite — nasz store wyników (baza gielda-agents pozostaje nietknięta).

Klucz próbki: `(upstream_run_id, model_key, sample_idx)`. `sample_idx` pozwala odpalić
TEN SAM model na TYM SAMYM upstreamie wiele razy → pomiar zmienności (temperatura > 0).
Porównanie modeli: grupujemy po `upstream_run_id`, agregujemy po `model_key` (średnia/odch.
po próbkach). To jest test hipotezy.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _decisions_ddl(table: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table} (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  upstream_run_id    TEXT NOT NULL,            -- gielda_agents runs.id (wspólny klucz)
  ticker             TEXT NOT NULL,
  as_of_date         TEXT NOT NULL,            -- ISO date (sobota decyzji)
  model_key          TEXT NOT NULL,            -- gb10-gemma4 | gb10-gemma4-uncensored | deepseek-v4-pro-baseline
  sample_idx         INTEGER NOT NULL DEFAULT 0,  -- numer próbki (pomiar zmienności)
  wire_model         TEXT,                     -- nazwa modelu wysłana na serwer
  variant            TEXT NOT NULL DEFAULT 'kierunkowy',
  -- wyrenderowane prompty (audyt / reprodukowalność)
  manager_system     TEXT, manager_user TEXT,
  trader_system      TEXT, trader_user  TEXT,
  -- output managera (Dyrektor Researchu)
  manager_output_md  TEXT,
  manager_structured TEXT,                     -- JSON
  director_stance    TEXT,                     -- synthesis_stance (pre-veto)
  director_strategy  TEXT,                     -- momentum|contrarian
  -- decyzja tradera (Szef Funduszu)
  stance             TEXT,
  confidence         REAL,
  time_horizon       TEXT,
  investment_style   TEXT,
  thesis_one_liner   TEXT,
  key_risks          TEXT,                     -- JSON array
  decision_json      TEXT,                     -- pełny Decision.model_dump()
  -- rozliczenie
  mgr_input_tokens   INTEGER, mgr_output_tokens INTEGER, mgr_duration_s REAL,
  trd_input_tokens   INTEGER, trd_output_tokens INTEGER, trd_duration_s REAL,
  cost_usd           REAL DEFAULT 0.0,
  reasoning_used     INTEGER DEFAULT 0,
  source             TEXT NOT NULL DEFAULT 'gemma',  -- gemma | baseline-import
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(upstream_run_id, model_key, sample_idx)
);
"""


_PNL_DDL = """
CREATE TABLE IF NOT EXISTS pnl_marks (
  decision_id        INTEGER PRIMARY KEY REFERENCES decisions(id) ON DELETE CASCADE,
  position           INTEGER NOT NULL,         -- +1 long / -1 short / 0 flat
  week_monday        TEXT, week_friday TEXT,
  entry_open         REAL, exit_close REAL,
  gross_return_pct   REAL,                     -- exit/entry-1 (bez znaku)
  signed_return_pct  REAL,                     -- position*gross
  bench_key          TEXT DEFAULT 'wig20',
  bench_entry_open   REAL, bench_exit_close REAL,
  bench_return_pct   REAL,
  alpha_pct          REAL,                     -- signed - bench
  status             TEXT NOT NULL,            -- closed | no_price
  computed_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_INDEX_DDL = "CREATE INDEX IF NOT EXISTS decisions_thread ON decisions(ticker, model_key, as_of_date);"

# B1 adversarial sycophancy: próby tradera z PERTURBOWANYM werdyktem Dyrektora.
# Osobna tabela — nie zanieczyszcza decisions/pnl_marks (te próby nie są „decyzjami
# funduszu", tylko pomiarem uległości; PnL się dla nich nie liczy).
_SYCO_DDL = """
CREATE TABLE IF NOT EXISTS syco_trials (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  upstream_run_id       TEXT NOT NULL,
  ticker                TEXT NOT NULL,
  as_of_date            TEXT NOT NULL,
  model_key             TEXT NOT NULL,
  condition             TEXT NOT NULL,           -- flip | flip_blind
  sample_idx            INTEGER NOT NULL,
  src_decision_id       INTEGER,                 -- decyzja źródłowa (dokument Dyrektora)
  director_stance_orig  TEXT,                    -- co Dyrektor naprawdę doradził
  director_stance_shown TEXT,                    -- co pokazaliśmy traderowi (odwrócone)
  stance                TEXT,
  confidence            REAL,
  followed_shown        INTEGER,                 -- 1 = poszedł za ODWRÓCONYM werdyktem
  followed_orig         INTEGER,                 -- 1 = poszedł za dowodami (oryg. kierunek)
  decision_json         TEXT,
  trader_user           TEXT,                    -- render audytowy
  trd_input_tokens      INTEGER, trd_output_tokens INTEGER, trd_duration_s REAL,
  created_at            TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(upstream_run_id, model_key, condition, sample_idx)
);
"""

DDL = _decisions_ddl("decisions") + _PNL_DDL + _INDEX_DDL + _SYCO_DDL


def connect(sqlite_path: str) -> sqlite3.Connection:
    p = Path(sqlite_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 10000;")  # znoś współbieżne zapisy przy sweepie
    return conn


def _needs_sample_migration(conn: sqlite3.Connection) -> bool:
    """True gdy tabela `decisions` istnieje, ale bez kolumny sample_idx (stary schemat)."""
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
    ).fetchone():
        return False
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()}
    return "sample_idx" not in cols


# Kolumny starego schematu (bez sample_idx) — do przepisania z zachowaniem id.
_OLD_COLS = [
    "id", "upstream_run_id", "ticker", "as_of_date", "model_key", "wire_model", "variant",
    "manager_system", "manager_user", "trader_system", "trader_user",
    "manager_output_md", "manager_structured", "director_stance", "director_strategy",
    "stance", "confidence", "time_horizon", "investment_style", "thesis_one_liner",
    "key_risks", "decision_json",
    "mgr_input_tokens", "mgr_output_tokens", "mgr_duration_s",
    "trd_input_tokens", "trd_output_tokens", "trd_duration_s",
    "cost_usd", "reasoning_used", "source", "created_at",
]


def _migrate_add_sample_idx(conn: sqlite3.Connection) -> None:
    """Przebuduj `decisions` dodając sample_idx do klucza UNIQUE, ZACHOWUJĄC dane + id
    (pnl_marks.decision_id → decisions.id pozostaje ważne). Istniejące wiersze → sample_idx=0."""
    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        conn.executescript(_decisions_ddl("decisions_new"))
        collist = ", ".join(_OLD_COLS)
        conn.execute(
            f"INSERT INTO decisions_new ({collist}, sample_idx) "
            f"SELECT {collist}, 0 FROM decisions"
        )
        conn.execute("DROP TABLE decisions")
        conn.execute("ALTER TABLE decisions_new RENAME TO decisions")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")


def init_db(sqlite_path: str) -> None:
    conn = connect(sqlite_path)
    try:
        if _needs_sample_migration(conn):
            _migrate_add_sample_idx(conn)
        conn.executescript(DDL)  # tworzy brakujące tabele/indeksy (idempotentnie)
        conn.commit()
    finally:
        conn.close()
