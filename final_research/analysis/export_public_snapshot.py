"""Eksport de-identyfikowanego snapshotu do release publicznego (GitHub).

ZOSTAJE: wyjścia modeli (przedmiot analizy) + zagregowane PnL.
USUWANE: wyrenderowany upstream (manager_system/user, trader_*) = know-how
funduszai.pl, oraz wszelkie credentiale. Skrypt ASERTUJE, że nic wrażliwego
nie wyciekło, i liczy sha256 wyniku do README.

Użycie: uv run python final_research/analysis/export_public_snapshot.py
Wynik: final_research/data/public_snapshot.db  (+ hash na stdout)
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from _common import DB, _KEYS_SQL

OUT = Path(__file__).resolve().parents[1] / "data" / "public_snapshot.db"

# Kolumny decisions, które WOLNO opublikować (wyjścia modelu + klucze).
PUBLIC_DECISION_COLS = [
    "id",                # klucz do JOIN-a z pnl_marks.decision_id
    "upstream_run_id",   # nieodwracalny UUID runu (nie ujawnia treści)
    "ticker", "as_of_date", "model_key", "sample_idx", "wire_model", "variant",
    "director_stance", "stance", "confidence", "time_horizon",
    "investment_style", "thesis_one_liner", "key_risks",
    "manager_structured",  # JSON wyjścia v2 (stance/conf/p_wrong/confession/reversal/...)
    "decision_json",
    "mgr_output_tokens", "mgr_duration_s", "reasoning_used", "source", "created_at",
]
# Kolumny, których NIE eksportujemy (upstream/know-how/prompty):
FORBIDDEN = {"manager_system", "manager_user", "trader_system", "trader_user",
             "manager_output_md", "mgr_input_tokens", "trd_input_tokens",
             "trd_output_tokens", "trd_duration_s"}

PNL_COLS = ["decision_id", "position", "week_monday", "week_friday",
            "gross_return_pct", "signed_return_pct", "bench_key",
            "bench_return_pct", "alpha_pct", "status"]

# Wzorce, które nie mogą pojawić się w żadnej wartości tekstowej wyjścia.
LEAK_PATTERNS = ["postgresql", "asyncpg", "abc.666", "192.168.", "Bearer ",
                 "api_key", "AGENTS_DB", "@127.0.0.1", "password"]


def main() -> None:
    if OUT.exists():
        OUT.unlink()
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(OUT)

    # --- decisions (tylko dozwolone kolumny) ---
    have = {r[1] for r in src.execute("PRAGMA table_info(decisions)")}
    cols = [c for c in PUBLIC_DECISION_COLS if c in have]
    assert not (set(cols) & FORBIDDEN), "FORBIDDEN kolumna w eksporcie!"
    dst.execute(f"CREATE TABLE decisions ({', '.join(c+' TEXT' for c in cols)})")
    # Tylko kohorty z whitelisty (_common.MODEL_KEYS) — skażony bieg
    # final-gemma4-abl nie wchodzi do release'u (PROVENANCE.md 2026-07-06).
    rows = src.execute(
        f"SELECT {', '.join(cols)} FROM decisions WHERE {_KEYS_SQL}").fetchall()
    ph = ", ".join("?" for _ in cols)
    dst.executemany(f"INSERT INTO decisions VALUES ({ph})",
                    [[r[c] for c in cols] for r in rows])
    n_dec = len(rows)

    # --- pnl_marks (agregaty, bez surowych cen wejścia/wyjścia) ---
    have_p = {r[1] for r in src.execute("PRAGMA table_info(pnl_marks)")}
    pcols = [c for c in PNL_COLS if c in have_p]
    dst.execute(f"CREATE TABLE pnl_marks ({', '.join(c+' TEXT' for c in pcols)})")
    prows = src.execute(
        f"SELECT {', '.join('p.'+c for c in pcols)} FROM pnl_marks p "
        f"JOIN decisions d ON d.id = p.decision_id WHERE d.{_KEYS_SQL}"
    ).fetchall()
    php = ", ".join("?" for _ in pcols)
    dst.executemany(f"INSERT INTO pnl_marks VALUES ({php})",
                    [[r[c] for c in pcols] for r in prows])
    dst.commit()

    # --- SANITY: żaden leak-pattern w tekstowych wartościach decisions ---
    leaks = []
    for r in dst.execute(f"SELECT {', '.join(cols)} FROM decisions"):
        blob = " ".join(str(x) for x in r if x is not None)
        for pat in LEAK_PATTERNS:
            if pat.lower() in blob.lower():
                leaks.append(pat)
    src.close(); dst.close()
    assert not leaks, f"LEAK wykryty ({set(leaks)}) — przerywam, nie publikuj!"

    h = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"OK  public_snapshot.db: {n_dec} decyzji, {len(prows)} marek PnL")
    print(f"    kolumny decisions: {cols}")
    print(f"    upstream/prompty USUNIĘTE, leak-check czysty")
    print(f"    sha256 = {h}")


if __name__ == "__main__":
    main()
