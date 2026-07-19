"""Zapis/odczyt naszego SQLite.

Klucz próbki: (upstream_run_id, model_key, sample_idx). Upsert jest idempotentny NA
POZIOMIE PRÓBKI — kolejne `decide` z nowym sample_idx dodają wiersze (pomiar zmienności),
a re-run konkretnej próbki nadpisuje ją.
"""
from __future__ import annotations

import json
from typing import Any

from gielda_uncensored.db.schema import connect

_COLS = [
    "upstream_run_id", "ticker", "as_of_date", "model_key", "sample_idx", "wire_model", "variant",
    "manager_system", "manager_user", "trader_system", "trader_user",
    "manager_output_md", "manager_structured", "director_stance", "director_strategy",
    "stance", "confidence", "time_horizon", "investment_style", "thesis_one_liner",
    "key_risks", "decision_json",
    "mgr_input_tokens", "mgr_output_tokens", "mgr_duration_s",
    "trd_input_tokens", "trd_output_tokens", "trd_duration_s",
    "cost_usd", "reasoning_used", "source",
]
_KEY = ("upstream_run_id", "model_key", "sample_idx")


def next_sample_idx(sqlite_path: str, *, upstream_run_id: str, model_key: str) -> int:
    """Następny wolny numer próbki dla (upstream, model). 0 gdy brak."""
    conn = connect(sqlite_path)
    try:
        r = conn.execute(
            "SELECT COALESCE(MAX(sample_idx)+1, 0) AS n FROM decisions "
            "WHERE upstream_run_id=? AND model_key=?",
            (upstream_run_id, model_key),
        ).fetchone()
        return int(r["n"])
    finally:
        conn.close()


def upsert_decision(sqlite_path: str, row: dict[str, Any]) -> int:
    vals = [row.get(c) for c in _COLS]
    placeholders = ", ".join("?" for _ in _COLS)
    update_set = ", ".join(f"{c}=excluded.{c}" for c in _COLS if c not in _KEY)
    conflict = ", ".join(_KEY)
    conn = connect(sqlite_path)
    try:
        conn.execute(
            f"INSERT INTO decisions ({', '.join(_COLS)}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict}) DO UPDATE SET {update_set}, created_at=datetime('now')",
            vals,
        )
        conn.commit()
        did = conn.execute(
            "SELECT id FROM decisions WHERE upstream_run_id=? AND model_key=? AND sample_idx=?",
            (row["upstream_run_id"], row["model_key"], row["sample_idx"]),
        ).fetchone()["id"]
        return int(did)
    finally:
        conn.close()


def upsert_pnl_mark(sqlite_path: str, mark: dict[str, Any]) -> None:
    cols = [
        "decision_id", "position", "week_monday", "week_friday", "entry_open", "exit_close",
        "gross_return_pct", "signed_return_pct", "bench_key", "bench_entry_open",
        "bench_exit_close", "bench_return_pct", "alpha_pct", "status",
    ]
    vals = [mark.get(c) for c in cols]
    placeholders = ", ".join("?" for _ in cols)
    update_set = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "decision_id")
    conn = connect(sqlite_path)
    try:
        conn.execute(
            f"INSERT INTO pnl_marks ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(decision_id) DO UPDATE SET {update_set}, computed_at=datetime('now')",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def find_decisions(
    sqlite_path: str, *, ticker: str, as_of: str, model_key: str
) -> list[dict[str, Any]]:
    """Wszystkie próbki decyzji dla (ticker, as_of, model_key)."""
    conn = connect(sqlite_path)
    try:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE ticker=? AND as_of_date=? AND model_key=? "
            "ORDER BY sample_idx",
            (ticker, as_of, model_key),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def decisions_without_marks(
    sqlite_path: str, *, model_key: str | None = None
) -> list[dict[str, Any]]:
    """Decyzje bez policzonej marki PnL (do pnl-sweep)."""
    conn = connect(sqlite_path)
    try:
        sql = (
            "SELECT d.* FROM decisions d LEFT JOIN pnl_marks m ON m.decision_id=d.id "
            "WHERE m.decision_id IS NULL"
        )
        params: tuple = ()
        if model_key:
            sql += " AND d.model_key=?"
            params = (model_key,)
        sql += " ORDER BY d.ticker, d.as_of_date, d.sample_idx"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def fetch_for_compare(sqlite_path: str, *, ticker: str | None = None) -> list[dict[str, Any]]:
    """Decyzje + ich marki PnL (jeden wiersz na próbkę)."""
    conn = connect(sqlite_path)
    try:
        sql = (
            "SELECT d.*, m.position, m.signed_return_pct, m.gross_return_pct, m.bench_return_pct, "
            "m.alpha_pct, m.status AS pnl_status, m.week_monday, m.week_friday "
            "FROM decisions d LEFT JOIN pnl_marks m ON m.decision_id = d.id"
        )
        params: tuple = ()
        if ticker:
            sql += " WHERE d.ticker=?"
            params = (ticker,)
        sql += " ORDER BY d.ticker, d.as_of_date, d.model_key, d.sample_idx"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def dumps(obj: Any) -> str | None:
    return json.dumps(obj, ensure_ascii=False) if obj is not None else None


# ── B1 adversarial sycophancy (syco_trials) ──────────────────────────────────

_SYCO_COLS = [
    "upstream_run_id", "ticker", "as_of_date", "model_key", "condition", "sample_idx",
    "src_decision_id", "director_stance_orig", "director_stance_shown",
    "stance", "confidence", "followed_shown", "followed_orig",
    "decision_json", "trader_user",
    "trd_input_tokens", "trd_output_tokens", "trd_duration_s",
]
_SYCO_KEY = ("upstream_run_id", "model_key", "condition", "sample_idx")


def upsert_syco_trial(sqlite_path: str, row: dict[str, Any]) -> int:
    vals = [row.get(c) for c in _SYCO_COLS]
    placeholders = ", ".join("?" for _ in _SYCO_COLS)
    update_set = ", ".join(f"{c}=excluded.{c}" for c in _SYCO_COLS if c not in _SYCO_KEY)
    conn = connect(sqlite_path)
    try:
        conn.execute(
            f"INSERT INTO syco_trials ({', '.join(_SYCO_COLS)}) VALUES ({placeholders}) "
            f"ON CONFLICT({', '.join(_SYCO_KEY)}) DO UPDATE SET {update_set}, "
            f"created_at=datetime('now')",
            vals,
        )
        conn.commit()
        r = conn.execute(
            "SELECT id FROM syco_trials WHERE upstream_run_id=? AND model_key=? "
            "AND condition=? AND sample_idx=?",
            (row["upstream_run_id"], row["model_key"], row["condition"], row["sample_idx"]),
        ).fetchone()
        return int(r["id"])
    finally:
        conn.close()


def syco_done_keys(sqlite_path: str, *, model_key: str) -> set[tuple[str, str, int]]:
    """(upstream_run_id, condition, sample_idx) już policzone — do idempotentnego wznowienia."""
    conn = connect(sqlite_path)
    try:
        return {
            (r["upstream_run_id"], r["condition"], int(r["sample_idx"]))
            for r in conn.execute(
                "SELECT upstream_run_id, condition, sample_idx FROM syco_trials "
                "WHERE model_key=? AND stance IS NOT NULL", (model_key,))
        }
    finally:
        conn.close()


def fetch_syco(sqlite_path: str) -> list[dict[str, Any]]:
    conn = connect(sqlite_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM syco_trials ORDER BY model_key, condition, ticker, as_of_date")]
    finally:
        conn.close()
