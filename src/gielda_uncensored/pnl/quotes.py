"""Cienkie GET-y do quotes API (:8090) — READ-ONLY. Dzienne bary z open/close."""
from __future__ import annotations

from datetime import date

import httpx


async def fetch_history(base_url: str, ticker: str, date_from: date, date_to: date) -> list[dict]:
    """Dzienne bary spółki: points[].{ts, open, high, low, close, ...}."""
    url = f"{base_url.rstrip('/')}/history/{ticker}"
    params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "tf": "1d"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json().get("points", [])


async def fetch_macro_history(base_url: str, key: str, date_from: date, date_to: date) -> list[dict]:
    """Dzienne bary serii makro/indeksu (np. wig20): points[].{ts, open, close, ...}.
    UWAGA: ten endpoint używa parametrów `from`/`to` (nie `date_from`/`date_to`)."""
    url = f"{base_url.rstrip('/')}/macro/{key}/history"
    params = {"from": date_from.isoformat(), "to": date_to.isoformat(), "limit": 50}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json().get("points", [])
