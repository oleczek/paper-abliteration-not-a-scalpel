"""Eksport macierzy do zewnętrznych optymalizatorów portfela (cuFOLIO / cuDF / pandas).

Generuje „długie" i „szerokie" CSV, gotowe do wczytania bez sklejania z bazą:
- returns.csv     : week × ticker → gross_return (long) — realizacja rynku (target).
- bench.csv       : week → wig20_return.
- signal_<model>.csv : week × ticker → kierunek konsensusu (+1/-1/0).
- conv_<model>.csv   : week × ticker → siła zgody [0,1] (do sizingu).

Konwencja: pierwszy wiersz = nagłówek `week,<TICKER1>,<TICKER2>,...`; brak danej = puste pole.
Wszystko czysto z SQLite (SELECT), zero nowych biegów.
"""
from __future__ import annotations

import csv
import os

from gielda_uncensored.config import Settings
from gielda_uncensored.reporting.panel import CENS, UNC, Cell, build_panel, weeks_of


def _write_matrix(path: str, weeks: list[str], tickers: list[str],
                  val: dict[tuple[str, str], float], fmt: str = "{:.6f}") -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["week", *tickers])
        for wk in weeks:
            row = [wk]
            for t in tickers:
                v = val.get((wk, t))
                row.append("" if v is None else fmt.format(v))
            w.writerow(row)


def export_matrices(settings: Settings, out_dir: str, *, min_samples: int = 5) -> list[str]:
    cells = build_panel(settings, min_samples=min_samples)
    os.makedirs(out_dir, exist_ok=True)
    weeks = weeks_of(cells)
    tickers = sorted({c.ticker for c in cells})
    written: list[str] = []

    # returns + bench
    ret = {(c.week_monday, c.ticker): c.gross for c in cells}
    p = os.path.join(out_dir, "returns.csv")
    _write_matrix(p, weeks, tickers, ret); written.append(p)

    bench = {}
    for c in cells:
        if c.bench is not None:
            bench[c.week_monday] = c.bench
    pb = os.path.join(out_dir, "bench.csv")
    with open(pb, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f); wr.writerow(["week", "wig20_return"])
        for wk in weeks:
            wr.writerow([wk, "" if wk not in bench else f"{bench[wk]:.6f}"])
    written.append(pb)

    # sygnały + konwikcja per model
    for label, mk in (("cens", CENS), ("unc", UNC)):
        sig = {(c.week_monday, c.ticker): float((s := c.sig(mk)).direction)
               for c in cells if c.sig(mk) is not None}
        conv = {(c.week_monday, c.ticker): c.sig(mk).conviction
                for c in cells if c.sig(mk) is not None}
        ps = os.path.join(out_dir, f"signal_{label}.csv")
        pc = os.path.join(out_dir, f"conv_{label}.csv")
        _write_matrix(ps, weeks, tickers, sig, fmt="{:.0f}"); written.append(ps)
        _write_matrix(pc, weeks, tickers, conv, fmt="{:.4f}"); written.append(pc)

    return written
