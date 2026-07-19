"""Wspólne: dostęp read-only do final.db + segmentacja WIG20/mWIG40 + metryki.
Używane przez tables.py, figures.py, export_public_snapshot.py."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

# Domyślnie prywatny final.db (autor); publiczna reprodukcja: RESEARCH_DB=.../public_snapshot.db
DB = Path(os.environ.get("RESEARCH_DB")
          or Path(__file__).resolve().parents[1] / "data" / "final.db")
PROMPT_HASH = "305718d8ebc369e4b5252383102da2ea7004f43eb2f3b9dd2d83f1f82011ac87"

# Kohorty wchodzące do analiz. UWAGA: `final-gemma4-abl` (oryginalny bieg abl)
# jest WYKLUCZONY — skażony szablonem huihui (system prompt jako repr listy,
# PROVENANCE.md 2026-07-06); zastąpiony pełnym re-runem `final-gemma4-abl-clean`
# (wagi huihui + szablon google). Stare wiersze zostają w bazie jako dowód.
MODEL_KEYS = ("final-gemma4-cens", "final-gemma4-abl-clean",
              "final-qwen-cens", "final-qwen-abl")
_KEYS_SQL = "model_key IN (%s)" % ",".join(f"'{k}'" for k in MODEL_KEYS)

WIG20 = {"PKN","PKO","PEO","PZU","PGE","KGH","CDR","ALE","DNP","LPP",
         "MBK","EBP","KRU","KTY","PCO","ALR","MDV","BDX","TPE","ZAB"}

FAM_COLOR = {"Gemma": "#2a78d6", "Qwen": "#1baf7a"}  # walidowane (dataviz)


def fam(mk: str) -> str:
    return "Qwen" if "qwen" in mk else "Gemma"


def arm(mk: str) -> str:
    return "abl" if "abl" in mk else "base"


def seg(ticker: str) -> str:
    return "WIG20" if ticker in WIG20 else "mWIG40"


def connect():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def load_decisions():
    con = connect()
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT model_key, ticker, as_of_date, sample_idx, stance, confidence,
               time_horizon, thesis_one_liner, decision_json, mgr_output_tokens
        FROM decisions WHERE """ + _KEYS_SQL + """
    """).fetchall()
    con.close()
    out = []
    for r in rows:
        d = json.loads(r["decision_json"])
        conf_txt = d.get("confession") or ""
        out.append({
            "fam": fam(r["model_key"]), "arm": arm(r["model_key"]),
            "seg": seg(r["ticker"]), "week": r["as_of_date"], "ticker": r["ticker"],
            "bull": 1.0 if r["stance"] == "bull" else 0.0,
            "conf": float(r["confidence"]),
            "p_wrong": float(d.get("p_wrong")) if d.get("p_wrong") is not None else None,
            "h1w": 1.0 if r["time_horizon"] == "1w" else 0.0,
            "horizon": r["time_horizon"],
            "conf_words": len(conf_txt.split()),
        })
    return out


def load_pnl():
    con = connect()
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT d.model_key, d.ticker, d.as_of_date,
               p.signed_return_pct, p.bench_return_pct, p.status
        FROM decisions d JOIN pnl_marks p ON p.decision_id = d.id
        WHERE p.status='closed' AND d.""" + _KEYS_SQL + """
    """).fetchall()
    con.close()
    return [{"fam": fam(r["model_key"]), "arm": arm(r["model_key"]),
             "seg": seg(r["ticker"]), "week": r["as_of_date"],
             "signed": r["signed_return_pct"], "bench": r["bench_return_pct"]}
            for r in rows]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")
