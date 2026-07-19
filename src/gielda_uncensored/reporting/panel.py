"""Panel sygnałów: wspólna struktura (tydzień × spółka) z konsensusem OBU modeli naraz.

To jest warstwa pośrednia, na której działają wszystkie strategie (single-model i hybrydowe):
- `gross` / `bench` są własnością (spółka, tydzień) — te same niezależnie od modelu (te same ceny).
- każdy model wnosi swój sygnał konsensusu (p_bull, siła zgody, śr. confidence) z N próbek.

Dzięki temu strategia hybrydowa (AGREE / DISAGREE / MEGA-ensemble) ma po prostu obie strony
tej samej komórki obok siebie, zamiast łączyć osobne przebiegi po kluczu.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store

CENS = "gb10-gemma4"
UNC = "gb10-gemma4-uncensored"
BASELINE = "deepseek-v4-pro-baseline"


def _pos(stance: str) -> int:
    """stance → kierunek pozycji. Wariant kierunkowy: bull=+1, wszystko inne=-1."""
    return 1 if "bull" in (stance or "") else -1


@dataclass
class Signal:
    """Konsensus jednego modelu na jednej komórce (spółka, tydzień)."""
    n: int
    p_bull: float           # udział byczych próbek
    conviction: float       # |2*p_bull - 1| ∈ [0,1] — siła zgody
    mean_conf: float        # średni self-reported confidence

    @property
    def direction(self) -> int:
        """Kierunek konsensusu: +1 long, -1 short, 0 remis 50/50."""
        if self.p_bull > 0.5:
            return 1
        if self.p_bull < 0.5:
            return -1
        return 0


@dataclass
class Cell:
    """Jedna komórka rynkowa: spółka × tydzień, z sygnałami modeli i realnym zwrotem."""
    ticker: str
    week_monday: str
    as_of: str
    gross: float                 # zwrot spółki (long) w tygodniu: exit/entry-1
    bench: float | None          # zwrot WIG20 (long) w tym tygodniu
    signals: dict[str, Signal]   # model_key -> Signal

    def sig(self, model: str) -> Signal | None:
        return self.signals.get(model)


def build_panel(settings: Settings, *, ticker: str | None = None,
                min_samples: int = 1) -> list[Cell]:
    """Zbuduj panel komórek z SQLite. Bierze tylko próbki z policzonym PnL (status=closed).

    min_samples: pomiń komórki modelu z mniejszą liczbą próbek (do rzetelnego konsensusu).
    Zwraca listę komórek posortowaną po (week_monday, ticker).
    """
    rows = store.fetch_for_compare(settings.sqlite_path, ticker=ticker)
    # grupuj po (upstream_run_id) — to unikalnie identyfikuje (ticker, tydzień)
    by_cell: dict[str, dict] = defaultdict(lambda: {
        "ticker": None, "week": None, "as_of": None, "gross": None, "bench": None,
        "stances": defaultdict(list), "confs": defaultdict(list),
    })
    for r in rows:
        if r.get("pnl_status") != "closed":
            continue
        g = r.get("gross_return_pct")
        if not isinstance(g, (int, float)):
            continue
        c = by_cell[r["upstream_run_id"]]
        c["ticker"] = r["ticker"]
        c["week"] = r["week_monday"]
        c["as_of"] = r["as_of_date"]
        c["gross"] = g
        if isinstance(r.get("bench_return_pct"), (int, float)):
            c["bench"] = r["bench_return_pct"]
        mk = r["model_key"]
        c["stances"][mk].append(r["stance"])
        if isinstance(r.get("confidence"), (int, float)):
            c["confs"][mk].append(r["confidence"])

    out: list[Cell] = []
    for c in by_cell.values():
        if c["ticker"] is None:
            continue
        signals: dict[str, Signal] = {}
        for mk, stances in c["stances"].items():
            if len(stances) < min_samples:
                continue
            p_bull = sum(1 for s in stances if "bull" in (s or "")) / len(stances)
            confs = c["confs"].get(mk) or []
            signals[mk] = Signal(
                n=len(stances), p_bull=p_bull,
                conviction=abs(2 * p_bull - 1),
                mean_conf=(sum(confs) / len(confs)) if confs else float("nan"),
            )
        if not signals:
            continue
        out.append(Cell(
            ticker=c["ticker"], week_monday=c["week"], as_of=c["as_of"],
            gross=c["gross"], bench=c["bench"], signals=signals,
        ))
    out.sort(key=lambda x: (x.week_monday, x.ticker))
    return out


def weeks_of(cells: Iterable[Cell]) -> list[str]:
    return sorted({c.week_monday for c in cells})


def by_week(cells: Iterable[Cell]) -> dict[str, list[Cell]]:
    d: dict[str, list[Cell]] = defaultdict(list)
    for c in cells:
        d[c.week_monday].append(c)
    return dict(sorted(d.items()))
