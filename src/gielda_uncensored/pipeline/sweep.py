"""run-sweep: przelot po całym uniwersum (WIG20+mWIG40) × dostępne tygodnie × próbki.

Współbieżność ograniczona semaforem (vLLM batchuje, ale nie zalewamy go). sample_idx
liczone z góry per run (bez wyścigów), więc równoległe zapisy trafiają w rozłączne klucze.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.pipeline.decide import run_decision
from gielda_uncensored.pipeline.decide_v2 import run_decision_v2
from gielda_uncensored.pnl.runner import price_rows
from gielda_uncensored.upstream.select import UpstreamRunRef, list_runs, universe_tickers


@dataclass
class SweepResult:
    total: int = 0
    ok: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    decision_ids: list[int] = field(default_factory=list)


def _noop(_: str) -> None:
    pass


def plan_samples(base: int, samples: int, samples_target: int | None) -> int:
    """Ile próbek dołożyć dla komórki z `base` istniejącymi.
    target=N → uzupełnij do N (restart-safe); bez targetu → dołóż `samples`."""
    if samples_target is not None:
        return max(0, samples_target - base)
    return samples


async def run_sweep(
    settings: Settings,
    *,
    model_key: str,
    wire_model: str,
    reasoning: bool,
    tickers: list[str] | None,
    date_from: date | None,
    date_to: date | None,
    variant: str = "kierunkowy",
    samples: int = 1,
    samples_target: int | None = None,
    concurrency: int = 3,
    with_pnl: bool = True,
    limit: int | None = None,
    verify: str | None = None,
    sampling: dict[str, Any] | None = None,
    flow: str = "v1",
    echo: Callable[[str], None] = _noop,
) -> SweepResult:
    # Guard: zanim zapiszemy tysiące wierszy pod etykietą, potwierdź że NA MASZYNIE jest
    # ten model (served-model-name identyczny dla obu → tylko refusal-rate rozróżnia).
    if verify:
        from gielda_uncensored import model_probe
        pr = await model_probe.probe_model(
            settings.gb10_base_url, settings.gb10_api_key, settings.gb10_model
        )
        rr = pr.refusal_rate
        ok = (rr <= 0.34) if verify == "uncensored" else \
             (rr >= 0.66) if verify == "censored" else True
        echo(f"[verify={verify}] refusal-rate={rr*100:.0f}% ({pr.n_refused}/{pr.n_probes})")
        if not ok:
            raise RuntimeError(
                f"WERYFIKACJA NIE PRZESZŁA: oczekiwano '{verify}', a refusal-rate={rr*100:.0f}% "
                f"({pr.verdict}). Abort — nie zapisuję pod etykietą '{model_key}'. "
                f"Podmień model na maszynie albo popraw --model/--verify."
            )

    if tickers is None:  # ALL → uniwersum z issuers (fallback: wszystkie w bazie)
        tickers = universe_tickers() or None

    runs = await list_runs(
        settings.agents_dsn, tickers=tickers, date_from=date_from,
        date_to=date_to, variant=variant, limit=limit,
    )
    echo(f"znaleziono {len(runs)} runów upstream (variant={variant})")

    # (run_ref, sample_idx) — indeksy liczone z góry, bez wyścigów.
    # samples_target = tryb „UZUPEŁNIJ DO N" (restart-safe: skończone komórki pomijane);
    # samples = tryb „DOŁÓŻ N" (każde wywołanie dokłada — do pomiaru zmienności).
    jobs: list[tuple[UpstreamRunRef, int]] = []
    for r in runs:
        base = store.next_sample_idx(
            settings.sqlite_path, upstream_run_id=r.run_id, model_key=model_key
        )
        n_add = plan_samples(base, samples, samples_target)
        for i in range(n_add):
            jobs.append((r, base + i))

    res = SweepResult(total=len(jobs))
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def _one(ref: UpstreamRunRef, sidx: int) -> None:
        nonlocal done
        async with sem:
            try:
                decide_fn = run_decision_v2 if flow == "v2" else run_decision
                row = await decide_fn(
                    settings, run_id=ref.run_id, model_key_label=model_key,
                    wire_model=wire_model, reasoning=reasoning, sample_idx=sidx,
                    sampling=sampling,
                )
                async with lock:
                    res.ok += 1
                    if row.get("_id"):
                        res.decision_ids.append(row["_id"])
                    done += 1
                    echo(f"[{done}/{res.total}] {ref.ticker} {ref.as_of_date} s{sidx} → {row['stance']}")
            except Exception as e:  # noqa: BLE001 — sweep ma dokończyć resztę
                async with lock:
                    res.failed += 1
                    res.errors.append(f"{ref.ticker} {ref.as_of_date} s{sidx}: {e}")
                    done += 1
                    echo(f"[{done}/{res.total}] {ref.ticker} {ref.as_of_date} s{sidx} → BŁĄD: {e}")

    await asyncio.gather(*(_one(r, s) for r, s in jobs))

    if with_pnl:
        echo("liczę PnL dla nowych decyzji...")
        rows = store.decisions_without_marks(settings.sqlite_path, model_key=model_key)
        n = await price_rows(settings, rows)
        echo(f"policzono {n} marek PnL")

    return res
