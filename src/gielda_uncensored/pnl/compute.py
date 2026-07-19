"""Czyste liczenie PnL pozycji tygodniowej.

Model (uproszczenie wg wymagań): fundusz trejduje co sobotę, pozycję trzyma tydzień.
Wejście = OPEN w poniedziałek, wyjście = CLOSE w piątek tygodnia PO dacie decyzji
(as_of = sobota). Long/short/flat wg stance. Benchmark = WIG20 (long).

Funkcja jest czysta (bez I/O) → deterministyczna i łatwa do testów/backfillu.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Any

# stance → pozycja kierunkowa
_LONG = {"strong_bull", "bull", "lean_bull"}
_SHORT = {"strong_bear", "bear", "lean_bear"}


def stance_to_position(stance: str | None) -> int:
    if stance in _LONG:
        return 1
    if stance in _SHORT:
        return -1
    return 0  # neutral / nieznane → flat


def next_monday(as_of: date) -> date:
    """Pierwszy poniedziałek ŚCIŚLE po `as_of` (as_of to zwykle sobota)."""
    days = (7 - as_of.weekday()) % 7  # weekday: pon=0 … nd=6
    if days == 0:
        days = 7  # gdy as_of samo jest poniedziałkiem → następny tydzień
    return as_of + timedelta(days=days)


def _bar_date(point: dict) -> date:
    return date.fromisoformat(str(point["ts"])[:10])


def _entry_open(points: list[dict], monday: date, friday: date) -> tuple[date, float] | None:
    in_week = sorted(
        (p for p in points if monday <= _bar_date(p) <= friday and p.get("open") is not None),
        key=_bar_date,
    )
    if not in_week:
        return None
    first = in_week[0]
    return _bar_date(first), float(first["open"])


def _exit_close(points: list[dict], monday: date, friday: date) -> tuple[date, float] | None:
    in_week = sorted(
        (p for p in points if monday <= _bar_date(p) <= friday and p.get("close") is not None),
        key=_bar_date,
    )
    if not in_week:
        return None
    last = in_week[-1]
    return _bar_date(last), float(last["close"])


@dataclass
class WeekMark:
    position: int
    week_monday: str
    week_friday: str
    entry_open: float | None
    exit_close: float | None
    gross_return_pct: float | None
    signed_return_pct: float | None
    bench_key: str
    bench_entry_open: float | None
    bench_exit_close: float | None
    bench_return_pct: float | None
    alpha_pct: float | None
    status: str  # closed | no_price

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def compute_week_pnl(
    points_ticker: list[dict],
    points_bench: list[dict],
    *,
    as_of: date,
    position: int,
    bench_key: str = "wig20",
) -> WeekMark:
    monday = next_monday(as_of)
    friday = monday + timedelta(days=4)

    entry = _entry_open(points_ticker, monday, friday)
    exit_ = _exit_close(points_ticker, monday, friday)
    b_entry = _entry_open(points_bench, monday, friday)
    b_exit = _exit_close(points_bench, monday, friday)

    if entry is None or exit_ is None or entry[1] == 0:
        return WeekMark(
            position=position, week_monday=monday.isoformat(), week_friday=friday.isoformat(),
            entry_open=entry[1] if entry else None,
            exit_close=exit_[1] if exit_ else None,
            gross_return_pct=None, signed_return_pct=None, bench_key=bench_key,
            bench_entry_open=b_entry[1] if b_entry else None,
            bench_exit_close=b_exit[1] if b_exit else None,
            bench_return_pct=None, alpha_pct=None, status="no_price",
        )

    gross = exit_[1] / entry[1] - 1.0
    signed = position * gross
    bench_ret = None
    if b_entry and b_exit and b_entry[1]:
        bench_ret = b_exit[1] / b_entry[1] - 1.0
    alpha = (signed - bench_ret) if bench_ret is not None else None

    return WeekMark(
        position=position, week_monday=monday.isoformat(), week_friday=friday.isoformat(),
        entry_open=entry[1], exit_close=exit_[1],
        gross_return_pct=gross, signed_return_pct=signed, bench_key=bench_key,
        bench_entry_open=b_entry[1] if b_entry else None,
        bench_exit_close=b_exit[1] if b_exit else None,
        bench_return_pct=bench_ret, alpha_pct=alpha, status="closed",
    )
