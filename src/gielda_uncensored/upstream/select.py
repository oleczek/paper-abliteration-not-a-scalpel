"""Wybór istniejącego runu deepseeka jako źródła upstreamu (READ-ONLY)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import asyncpg


@dataclass(frozen=True)
class UpstreamRunRef:
    run_id: str
    ticker: str
    as_of_date: date
    variant: str
    status: str


async def _connect(dsn: str) -> asyncpg.Connection:
    # Wymuszamy tryb read-only na poziomie sesji — żaden write nie przejdzie.
    return await asyncpg.connect(dsn, server_settings={"default_transaction_read_only": "on"})


async def pick_run(
    dsn: str, *, ticker: str, as_of: date, variant: str = "kierunkowy"
) -> str | None:
    """Najświeższy `ok` run dla (ticker, as_of, variant). Zwraca run_id (str) lub None."""
    conn = await _connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id FROM runs "
            "WHERE ticker=$1 AND as_of_date=$2 AND variant=$3 AND status='ok' "
            "ORDER BY finished_at DESC NULLS LAST LIMIT 1",
            ticker, as_of, variant,
        )
        return str(row["id"]) if row else None
    finally:
        await conn.close()


async def list_runs(
    dsn: str,
    *,
    tickers: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    variant: str = "kierunkowy",
    limit: int | None = None,
) -> list[UpstreamRunRef]:
    """Wszystkie `ok` runy pasujące do filtrów — do run-sweep. tickers=None → wszystkie."""
    conds = ["status='ok'", "variant=$1"]
    params: list = [variant]
    if tickers:
        params.append(tickers)
        conds.append(f"ticker = ANY(${len(params)})")
    if date_from:
        params.append(date_from)
        conds.append(f"as_of_date >= ${len(params)}")
    if date_to:
        params.append(date_to)
        conds.append(f"as_of_date <= ${len(params)}")
    sql = (
        "SELECT id, ticker, as_of_date, variant, status FROM runs "
        f"WHERE {' AND '.join(conds)} ORDER BY as_of_date, ticker"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = await _connect(dsn)
    try:
        rows = await conn.fetch(sql, *params)
        return [
            UpstreamRunRef(
                run_id=str(r["id"]), ticker=r["ticker"], as_of_date=r["as_of_date"],
                variant=r["variant"], status=r["status"],
            )
            for r in rows
        ]
    finally:
        await conn.close()


def tickers_for_index(index: str) -> list[str]:
    """Tickery danego indeksu (WIG20 / mWIG40) z gielda_issuers. `by_index` zwraca
    RegistryEntry z polem `.issuer` (nie samego Issuera) → sięgamy `.issuer.ticker`."""
    from gielda_issuers import IssuerRegistry

    reg = IssuerRegistry.load("PL")
    seen: dict[str, None] = {}
    for e in reg.by_index(index):
        seen.setdefault(e.issuer.ticker, None)
    return list(seen)


def universe_tickers() -> list[str]:
    """WIG20 + mWIG40 (kanoniczne 60). Fallback: pusta lista → sweep bierze wszystkie z bazy."""
    try:
        return tickers_for_index("WIG20") + tickers_for_index("mWIG40")
    except Exception:
        return []


def resolve_tickers(spec: str | None) -> list[str] | None:
    """'ALL'/None → None (całe uniwersum w sweepie); 'WIG20'/'mWIG40' → indeks;
    inaczej lista po przecinku."""
    if not spec or spec.upper() == "ALL":
        return None
    up = spec.upper()
    if up in ("WIG20", "MWIG40"):
        return tickers_for_index("WIG20" if up == "WIG20" else "mWIG40")
    return [t.strip().upper() for t in spec.split(",") if t.strip()]


async def list_upstream(dsn: str, *, ticker: str) -> list[UpstreamRunRef]:
    conn = await _connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, ticker, as_of_date, variant, status FROM runs "
            "WHERE ticker=$1 AND status='ok' ORDER BY as_of_date",
            ticker,
        )
        return [
            UpstreamRunRef(
                run_id=str(r["id"]), ticker=r["ticker"], as_of_date=r["as_of_date"],
                variant=r["variant"], status=r["status"],
            )
            for r in rows
        ]
    finally:
        await conn.close()
