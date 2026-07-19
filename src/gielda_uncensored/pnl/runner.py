"""Wycena PnL: pobierz ceny tygodnia (raz na ticker/tydzień) i policz marki dla próbek."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.pnl import quotes
from gielda_uncensored.pnl.compute import compute_week_pnl, next_monday, stance_to_position


async def _fetch_week_prices(settings: Settings, ticker: str, as_of_d: date):
    monday = next_monday(as_of_d)
    friday = monday + timedelta(days=4)
    lo, hi = monday - timedelta(days=3), friday + timedelta(days=3)  # bufor na święta
    points_t = await quotes.fetch_history(settings.quotes_base_url, ticker, lo, hi)
    points_b = await quotes.fetch_macro_history(settings.quotes_base_url, "wig20", lo, hi)
    return points_t, points_b


def _mark_row(dec: dict[str, Any], points_t, points_b, as_of_d: date) -> dict[str, Any]:
    position = stance_to_position(dec["stance"])
    mark = compute_week_pnl(points_t, points_b, as_of=as_of_d, position=position)
    row = mark.as_row()
    row["decision_id"] = dec["id"]
    return row


async def price_decisions(
    settings: Settings, *, ticker: str, as_of: str, model_key: str
) -> list[dict[str, Any]]:
    """Wyceń WSZYSTKIE próbki decyzji dla (ticker, as_of, model_key). Ceny pobierane raz."""
    decs = store.find_decisions(settings.sqlite_path, ticker=ticker, as_of=as_of, model_key=model_key)
    if not decs:
        raise ValueError(
            f"brak decyzji w SQLite dla ticker={ticker} as_of={as_of} model={model_key} (najpierw `decide`)"
        )
    as_of_d = date.fromisoformat(as_of)
    points_t, points_b = await _fetch_week_prices(settings, ticker, as_of_d)
    out = []
    for dec in decs:
        row = _mark_row(dec, points_t, points_b, as_of_d)
        store.upsert_pnl_mark(settings.sqlite_path, row)
        out.append(row)
    return out


async def price_rows(settings: Settings, rows: list[dict[str, Any]]) -> int:
    """Wyceń dowolny zbiór wierszy decyzji (pnl-sweep). Grupuje po (ticker, as_of) →
    ceny pobierane raz na tydzień. Zwraca liczbę policzonych marek."""
    by_week: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_week[(r["ticker"], r["as_of_date"])].append(r)
    n = 0
    for (ticker, as_of), decs in by_week.items():
        as_of_d = date.fromisoformat(as_of)
        try:
            points_t, points_b = await _fetch_week_prices(settings, ticker, as_of_d)
        except Exception:
            points_t, points_b = [], []
        for dec in decs:
            store.upsert_pnl_mark(settings.sqlite_path, _mark_row(dec, points_t, points_b, as_of_d))
            n += 1
    return n
