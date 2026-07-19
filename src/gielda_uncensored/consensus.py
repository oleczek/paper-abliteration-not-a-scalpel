"""Konsensus między biegami (self-ensembling): agreguj N próbek jednej komórki
w jedną decyzję większościową i sprawdź, czy odszumienie wydobywa sygnał.

Kluczowe pytania:
- czy konsensus-hit-rate > single-sample-hit-rate? (jest ukryty sygnał?)
- czy edge rośnie, gdy filtrujemy do komórek WYSOKIEJ ZGODY? (model ma rację tylko gdy
  jest ze sobą zgodny?)
Działa na istniejących próbkach — zero nowych biegów.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store


def _cells_for_model(rows: list[dict]) -> dict[str, dict]:
    """Grupuj wycenione próbki po komórce (upstream_run_id). Zwraca per-komórka:
    stances, gross (wsp. dla komórki), bench."""
    by: dict[str, dict] = defaultdict(lambda: {"stances": [], "gross": None, "bench": None,
                                               "ticker": None, "as_of": None})
    for r in rows:
        if r.get("pnl_status") != "closed":
            continue
        cell = by[r["upstream_run_id"]]
        cell["stances"].append(r["stance"])
        cell["ticker"] = r["ticker"]
        cell["as_of"] = r["as_of_date"]
        # gross/bench są takie same dla wszystkich próbek komórki (ta sama spółka/tydzień)
        if isinstance(r.get("gross_return_pct"), (int, float)):
            cell["gross"] = r["gross_return_pct"]
        if isinstance(r.get("bench_return_pct"), (int, float)):
            cell["bench"] = r["bench_return_pct"]
    return by


def consensus_metrics(rows: list[dict], *, conviction_thr: float = 0.6) -> dict[str, Any]:
    cells = _cells_for_model(rows)
    single_hits, single_alpha = [], []
    cons_hits, cons_alpha, strengths = [], [], []
    hc_hits, hc_alpha = [], []  # high-conviction
    ties = 0
    for c in cells.values():
        st = c["stances"]
        gross, bench = c["gross"], c["bench"]
        if gross is None or not st:
            continue
        p_bull = sum(1 for s in st if s == "bull") / len(st)
        strength = abs(2 * p_bull - 1)
        strengths.append(strength)
        # single-sample (każda próbka osobno)
        for s in st:
            pos = 1 if s == "bull" else -1
            sg = pos * gross
            single_hits.append(sg > 0)
            if bench is not None:
                single_alpha.append(sg - bench)
        # konsensus (większość)
        if p_bull == 0.5:
            ties += 1
            continue
        pos = 1 if p_bull > 0.5 else -1
        sg = pos * gross
        cons_hits.append(sg > 0)
        a = (sg - bench) if bench is not None else None
        if a is not None:
            cons_alpha.append(a)
        if strength >= conviction_thr:
            hc_hits.append(sg > 0)
            if a is not None:
                hc_alpha.append(a)

    def rate(xs):
        return sum(1 for x in xs if x) / len(xs) if xs else None

    def mean(xs):
        return statistics.fmean(xs) if xs else None

    return {
        "n_cells": len([c for c in cells.values() if c["gross"] is not None]),
        "avg_samples": mean([len(c["stances"]) for c in cells.values() if c["stances"]]),
        "ties": ties,
        "single_hit": rate(single_hits),
        "single_alpha": mean(single_alpha),
        "cons_hit": rate(cons_hits),
        "cons_alpha": mean(cons_alpha),
        "mean_strength": mean(strengths),
        "unanimous_rate": rate([s == 1.0 for s in strengths]),
        "hc_thr": conviction_thr,
        "hc_n": len(hc_hits),
        "hc_hit": rate(hc_hits),
        "hc_alpha": mean(hc_alpha),
    }


def _pct(v: Any) -> str:
    return f"{v * 100:+.2f}%" if isinstance(v, (int, float)) else "—"


def _rt(v: Any) -> str:
    return f"{v * 100:.0f}%" if isinstance(v, (int, float)) else "—"


def render_consensus(settings: Settings, *, ticker: str | None = None, conviction_thr: float = 0.6) -> str:
    rows = store.fetch_for_compare(settings.sqlite_path, ticker=ticker)
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_model[r["model_key"]].append(r)
    lines = ["", "KONSENSUS MIĘDZY BIEGAMI (self-ensembling)" + (f"  ticker={ticker}" if ticker else "")]
    for mk in sorted(by_model):
        m = consensus_metrics(by_model[mk], conviction_thr=conviction_thr)
        if not m["n_cells"]:
            continue
        lines.append(f"\n▸ {mk}  ({m['n_cells']} komórek, śr. {m['avg_samples']:.1f} próbek/kom., ties={m['ties']})")
        lines.append(f"    single-sample : hit {_rt(m['single_hit'])}   alpha {_pct(m['single_alpha'])}")
        lines.append(f"    KONSENSUS     : hit {_rt(m['cons_hit'])}   alpha {_pct(m['cons_alpha'])}   "
                     f"(Δhit {_signed_pp(m['cons_hit'], m['single_hit'])})")
        lines.append(f"    zgoda: śr. siła {_rt(m['mean_strength'])}, jednogłośnych {_rt(m['unanimous_rate'])}")
        lines.append(f"    wysoka konwikcja (siła≥{m['hc_thr']:.1f}): {m['hc_n']} kom. → "
                     f"hit {_rt(m['hc_hit'])}   alpha {_pct(m['hc_alpha'])}")
    return "\n".join(lines) if len(lines) > 2 else "(brak wycenionych komórek — najpierw `pnl`/`pnl-sweep`)"


def _signed_pp(a: Any, b: Any) -> str:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return f"{(a - b) * 100:+.0f}pp"
    return "—"
