"""Rekonstrukcja upstreamu (analitycy + debata + kontekst) z bazy gielda-agents.

READ-ONLY: tylko SELECT-y, połączenie w trybie read-only. Odtwarzamy dokładnie te
obiekty, których oczekuje `run_manager_synthesis` i budowa render_vars — jak
`gielda_agents/pipeline/replay_cache.py:_reconstruct`, ale kluczując po `run_id`
(zamiast po persona_id), więc nie zależymy od wersji promptów upstreamu.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import asyncpg

from gielda_agents.debate.state import DebateTurn
from gielda_agents.llm.base import LLMResponse
from gielda_agents.personas.base import AgentResult
from gielda_agents.pipeline.context import ContextBundle, context_to_render_vars


@dataclass
class Upstream:
    run_id: str
    ticker: str
    as_of: date
    variant: str
    ctx: ContextBundle
    analysts: list[AgentResult]
    director: AgentResult | None
    turns: list[DebateTurn]
    baseline_decision: dict[str, Any] | None  # decyzja deepseeka (do 3-way compare)


def _as_dict(v: Any) -> dict[str, Any]:
    """JSONB z asyncpg bywa str albo dict — znormalizuj do dict."""
    if v is None:
        return {}
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return {}
    return dict(v)


def _reconstruct(r: asyncpg.Record) -> AgentResult:
    return AgentResult(
        persona_key=r["key"],
        sequence=r["sequence"],
        debate_round=r["debate_round"],
        output_md=r["output_md"],
        structured=_as_dict(r["structured"]),
        llm=LLMResponse(
            text=r["output_md"],
            input_tokens=r["input_tokens"] or 0,
            output_tokens=r["output_tokens"] or 0,
            cache_read_tokens=r["cache_read_tokens"] or 0,
            cost_usd=0.0,            # już zapłacone — reuse
            model_key="(cached)",
            duration_s=0.0,
        ),
        started_at=r["started_at"],
        finished_at=r["finished_at"],
    )


async def load_upstream(dsn: str, run_id: str) -> Upstream:
    conn = await asyncpg.connect(
        dsn, server_settings={"default_transaction_read_only": "on"}
    )
    try:
        run = await conn.fetchrow(
            "SELECT id, ticker, as_of_date, variant FROM runs WHERE id=$1", run_id
        )
        if run is None:
            raise ValueError(f"run {run_id} nie istnieje")

        ci = await conn.fetchrow(
            "SELECT context_bundle FROM run_inputs WHERE run_id=$1", run_id
        )
        if ci is None:
            raise ValueError(f"run {run_id} nie ma run_inputs.context_bundle")
        ctx = ContextBundle.model_validate(_as_dict(ci["context_bundle"]))

        rows = await conn.fetch(
            "SELECT p.key, p.role, ao.sequence, ao.debate_round, ao.output_md, "
            "ao.structured, ao.input_tokens, ao.output_tokens, ao.cache_read_tokens, "
            "ao.started_at, ao.finished_at "
            "FROM agent_outputs ao JOIN personas p ON p.id = ao.persona_id "
            "WHERE ao.run_id=$1 ORDER BY ao.sequence",
            run_id,
        )

        analysts: list[AgentResult] = []
        director: AgentResult | None = None
        turns: list[DebateTurn] = []
        for r in rows:
            role = r["role"]
            if role == "analyst":
                analysts.append(_reconstruct(r))
            elif role == "director_intro":
                director = _reconstruct(r)
            elif role == "researcher":
                side = "bull" if r["key"].startswith("bull") else "bear"
                turns.append(DebateTurn(round=1, side=side, result=_reconstruct(r)))
            # role in {manager, trader} → pomijamy (odtwarzamy na gemmie)
        analysts.sort(key=lambda a: a.sequence)
        turns.sort(key=lambda t: t.result.sequence)

        if not analysts or director is None or not turns:
            raise ValueError(
                f"run {run_id}: niekompletny upstream "
                f"(analysts={len(analysts)}, director={director is not None}, turns={len(turns)})"
            )

        dec = await conn.fetchrow(
            "SELECT stance, confidence, time_horizon, investment_style, strategy, "
            "director_stance, thesis_one_liner, key_risks, extras "
            "FROM decisions WHERE run_id=$1",
            run_id,
        )
        baseline = dict(dec) if dec else None
        if baseline is not None:
            baseline["key_risks"] = _as_dict_list(baseline.get("key_risks"))
            baseline["extras"] = _as_dict(baseline.get("extras"))
            baseline["confidence"] = float(baseline["confidence"]) if baseline.get("confidence") is not None else None

        return Upstream(
            run_id=str(run["id"]),
            ticker=run["ticker"],
            as_of=run["as_of_date"],
            variant=run["variant"],
            ctx=ctx,
            analysts=analysts,
            director=director,
            turns=turns,
            baseline_decision=baseline,
        )
    finally:
        await conn.close()


def _as_dict_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            return list(json.loads(v))
        except json.JSONDecodeError:
            return []
    return list(v)


# eksport pomocniczy dla decide.py (nie musi importować z context)
build_render_vars = context_to_render_vars
