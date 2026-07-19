"""Warstwa decyzyjna na gemmie: Dyrektor Researchu (manager) → Szef Funduszu (trader).

Wiernie odwzorowuje składanie zmiennych z `gielda_agents/pipeline/orchestrator.py`
(linie ~298-601, ścieżka reuse-upstream / tryb binarny „kierunkowy"), ale:
- upstream (analitycy + debata) pochodzi z bazy (loader), nie z żywego runu,
- model to nasz GemmaClient (wstrzyknięty do Manager/TraderAgent),
- wynik trafia do naszego SQLite, baza gielda-agents nietknięta.

Dyscyplina v1 (lekka): manager — tylko retry na ucięty JSON tail (brak synthesis_stance);
trader — 3× retry na niepoprawny/ucięty JSON. Pełne walidatory (reversal/tally/veto)
są DeepSeek-tuned i mogą częściej odpalać re-think na słabszym modelu — dokładamy je
później za flagą, gdy poznamy jakość outputu gemmy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from gielda_agents.debate.loop import run_manager_synthesis
from gielda_agents.debate.state import DebateState
from gielda_agents.personas.base import Persona
from gielda_agents.personas.manager import ManagerAgent
from gielda_agents.personas.researcher import extract_recommendation, render_confessions
from gielda_agents.personas.scenes import pick_framing_lens
from gielda_agents.personas.trader import Decision, TraderAgent
from gielda_agents.prompts.loader import PromptLoader
from gielda_agents.render.messages import STANCE_PL

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.llm.gemma_client import GemmaClient
from gielda_uncensored.upstream.loader import Upstream, load_upstream
from gielda_uncensored.upstream.loader import build_render_vars as context_to_render_vars

# Nakaz skrócenia, gdy JSON tail Dyrektora się uciął (odpowiednik _TRUNCATION_CORRECTION).
_TRUNCATION_CORRECTION = (
    "POPRZEDNIA WERSJA MIAŁA UCIĘTY BLOK JSON NA KOŃCU. Skróć dokument o ~30%, "
    "zostaw wszystkie sekcje, ale ZWIĘŹLE — i KONIECZNIE zamknij pełny blok ```json ...``` "
    "z polem synthesis_stance na końcu."
)


def _mk_persona(*, key: str, role: str, prompt_path: str, wire_model: str,
                temperature: float, response_json: bool) -> Persona:
    return Persona(
        key=key, display_name=key, role=role, prompt_path=prompt_path,
        model=wire_model, temperature=temperature, max_tokens=3200,
        response_format_json=response_json,
    )


def _trader_ok(st: dict[str, Any]) -> bool:
    return bool(st) and "stance" in st and not st.get("_invalid")


async def run_decision(
    settings: Settings,
    *,
    run_id: str,
    model_key_label: str,
    wire_model: str,
    reasoning: bool,
    sample_idx: int = 0,
    persist: bool = True,
    sampling: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Odpal manager+trader na gemmie dla danego upstreamu. Zwraca dict wiersza decyzji
    (i zapisuje do SQLite, gdy persist=True).

    sampling: opcjonalne nadpisania {temperature, top_p, top_k} dla OBU person
    (oś temperatury — falsyfikacja collapse'ów; domyślnie None = temp person 0.8/0.6)."""
    up: Upstream = await load_upstream(settings.agents_dsn, run_id)

    # --- render_vars (baza) + wymagane pola trybu binarnego (mirror orchestrator) ---
    render_vars: dict[str, Any] = context_to_render_vars(up.ctx)
    render_vars["strategy_mandate"] = None       # kierunkowy: bez mandatu działu
    render_vars["binary_directional"] = True      # decision_mode == binary
    render_vars["previous_decisions"] = []        # continuity OFF w v1
    render_vars["current_position"] = None
    render_vars["previous_scoreboard"] = None

    # --- klient + persony + agenci ---
    smp = sampling or {}
    gemma = GemmaClient(
        base_url=settings.gb10_base_url, api_key=settings.gb10_api_key,
        model_key=wire_model, reasoning=reasoning,
        temperature_override=smp.get("temperature"),
        top_p=smp.get("top_p"), top_k=smp.get("top_k"),
    )
    mgr_persona = _mk_persona(
        key="research_manager", role="manager",
        prompt_path="manager/research_manager.md", wire_model=wire_model,
        temperature=0.8, response_json=False,
    )
    trd_persona = _mk_persona(
        key="trader", role="trader", prompt_path="trader/trader.md",
        wire_model=wire_model, temperature=0.6, response_json=True,
    )
    loader = PromptLoader(Path(settings.agents_prompts_dir))
    manager_agent = ManagerAgent(
        persona=mgr_persona, prompt=loader.load(mgr_persona.prompt_path), llm=gemma
    )
    trader_agent = TraderAgent(
        persona=trd_persona, prompt=loader.load(trd_persona.prompt_path), llm=gemma
    )

    # --- Dyrektor Researchu (manager synthesis) na zcache'owanych turach ---
    state = DebateState(ticker=up.ticker, rounds_planned=1)
    state.turns = up.turns
    state.rounds_run = 1
    state = await run_manager_synthesis(
        state, manager=manager_agent, analyst_results=up.analysts,
        base_render_vars=render_vars,
    )
    for _ in range(2):  # dolecz ucięty JSON tail
        mr = state.manager_results[-1]
        if (mr.structured or {}).get("synthesis_stance"):
            break
        state.manager_results.pop()
        state = await run_manager_synthesis(
            state, manager=manager_agent, analyst_results=up.analysts,
            base_render_vars=render_vars, correction=_TRUNCATION_CORRECTION,
        )

    mgr = state.manager_results[-1]
    mgr_md = mgr.output_md
    mgr_s = mgr.structured or {}
    director_stance = mgr_s.get("synthesis_stance")
    director_strategy = mgr_s.get("strategy")

    # --- Szef Funduszu (trader) — zmienne jak w orchestrator 533-552 ---
    ctx_dict = up.ctx.issuer.context if isinstance(up.ctx.issuer.context, dict) else {}
    headlines = [
        it.get("title")
        for it in ((render_vars.get("top_feed") or {}).get("items") or [])
        if it.get("title")
    ][:8]
    trader_vars: dict[str, Any] = {
        **render_vars,
        "manager_recommendation": extract_recommendation(mgr_md),
        "manager_strategy": director_strategy,
        "manager_strategy_rationale": mgr_s.get("strategy_rationale", ""),
        "manager_stance": director_stance,
        "manager_stance_pl": STANCE_PL.get(director_stance, director_stance),
        "silenced_risks": render_confessions(state.history_turns()),
        "business_drivers": list(ctx_dict.get("drivers") or [])[:5],
        "headlines": headlines,
        "regime_line": render_vars.get("regime_line", ""),
        "calendar_line": render_vars.get("calendar_line", ""),
        "framing_lens": pick_framing_lens(up.ticker, up.as_of),
        "veto_correction": "",
    }
    trader_seq = len(up.analysts) + 1 + len(state.turns) + len(state.manager_results)

    trader_result = None
    structured: dict[str, Any] = {}
    for _ in range(3):
        trader_result = await trader_agent.run(render_vars=trader_vars, sequence=trader_seq)
        structured = trader_result.structured or {}
        if _trader_ok(structured):
            break
    if not _trader_ok(structured):
        raise RuntimeError(f"trader zwrócił niepoprawną decyzję po 3 próbach: {structured!r}")

    decision = Decision(**{
        k: v for k, v in structured.items()
        if k not in {"_invalid", "_raw", "_errors", "_debug"}
    })

    mgr_dbg = (mgr_s.get("_debug") or {})
    trd_dbg = (structured.get("_debug") or {})
    row: dict[str, Any] = {
        "upstream_run_id": up.run_id,
        "ticker": up.ticker,
        "as_of_date": up.as_of.isoformat(),
        "model_key": model_key_label,
        "sample_idx": sample_idx,
        "wire_model": wire_model,
        "variant": up.variant,
        "manager_system": mgr_dbg.get("system"),
        "manager_user": mgr_dbg.get("user"),
        "trader_system": trd_dbg.get("system"),
        "trader_user": trd_dbg.get("user"),
        "manager_output_md": mgr_md,
        "manager_structured": store.dumps({k: v for k, v in mgr_s.items() if k != "_debug"}),
        "director_stance": director_stance,
        "director_strategy": director_strategy,
        "stance": decision.stance,
        "confidence": decision.confidence,
        "time_horizon": decision.time_horizon,
        "investment_style": decision.investment_style,
        "thesis_one_liner": decision.thesis_one_liner,
        "key_risks": store.dumps(decision.key_risks),
        "decision_json": store.dumps(decision.model_dump()),
        "mgr_input_tokens": mgr.llm.input_tokens,
        "mgr_output_tokens": mgr.llm.output_tokens,
        "mgr_duration_s": mgr.llm.duration_s,
        "trd_input_tokens": trader_result.llm.input_tokens,
        "trd_output_tokens": trader_result.llm.output_tokens,
        "trd_duration_s": trader_result.llm.duration_s,
        "cost_usd": 0.0,
        "reasoning_used": 1 if reasoning else 0,
        "source": "gemma",
    }

    if persist:
        row["_id"] = store.upsert_decision(settings.sqlite_path, row)
    return row
