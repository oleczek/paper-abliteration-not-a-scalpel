"""Warstwa decyzyjna v2 (final_research): MANAGER-ONLY, czysty JSON.

Design (final_research/README.md + prompts/manager_v2.md): bez tradera,
Dyrektor Researchu wydaje werdykt binarny + 3 sondy zwątpienia (confession,
reversal_trigger, p_wrong). Prompt ładowany z final_research/prompts/
(zamrożony hashem przed biegiem — hash logujemy do wiersza), render przez
PromptLoader gielda-agents (te same filtry/Jinja co pilot), upstream
identycznie jak v1 (loader read-only + _slim_manager_brief +
render_analyst_results/render_debate_history — rendering wejścia 1:1 z pilotem).

Sampling domyślny v2: temp 1.0 / top_p 0.95 / top_k 64 (rekomendacja vendora
Gemmy; pilot temp-axis pokazał, że fingerprint przeżywa 1.0) — nadpisywalny
przez `sampling` (flagi --temp/--top-p/--top-k sweepa). Identyczny dla obu
ramion z konstrukcji (jeden kod).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gielda_agents.debate.loop import (
    _slim_manager_brief,
    render_analyst_results,
    render_debate_history,
)
from gielda_agents.llm.base import Message
from gielda_agents.prompts.loader import PromptLoader

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.llm.gemma_client import GemmaClient
from gielda_uncensored.upstream.loader import Upstream, load_upstream
from gielda_uncensored.upstream.loader import build_render_vars as context_to_render_vars

# Domyślny katalog promptów v2 (względem CWD = korzeń repo, jak SQLITE_PATH).
V2_PROMPT_DIR = "./final_research/prompts"
V2_PROMPT_FILE = "manager_v2.md"

# Sampling v2 (vendor-recommended dla Gemmy; jeden przepis dla OBU ramion).
V2_SAMPLING: dict[str, Any] = {"temperature": 1.0, "top_p": 0.95, "top_k": 64}

V2_STANCES = {"bull", "bear"}
V2_HORIZONS = {"1w", "2w", "4w"}
V2_MAX_TOKENS = 1024  # wyjście to krótki JSON (~150-250 tok); input ~12k < 16384-1024

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_v2_output(content: str) -> dict[str, Any]:
    """Parsuj i zwaliduj JSON managera v2. Rzuca ValueError z powodem
    (komunikat trafia do retry-korekty i do logu sweepa)."""
    m = _JSON_RE.search(content or "")
    if not m:
        raise ValueError("brak obiektu JSON w odpowiedzi")
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"niepoprawny JSON: {e}") from e
    if not isinstance(d, dict):
        raise ValueError("JSON nie jest obiektem")

    out: dict[str, Any] = {}
    stance = str(d.get("stance", "")).strip().lower()
    if stance not in V2_STANCES:
        raise ValueError(f"stance={d.get('stance')!r} (oczekiwano bull|bear)")
    out["stance"] = stance

    horizon = str(d.get("time_horizon", "")).strip().lower()
    if horizon not in V2_HORIZONS:
        raise ValueError(f"time_horizon={d.get('time_horizon')!r} (oczekiwano 1w|2w|4w)")
    out["time_horizon"] = horizon

    for fld in ("confidence", "p_wrong"):
        try:
            v = float(d.get(fld))
        except (TypeError, ValueError):
            raise ValueError(f"{fld}={d.get(fld)!r} nie jest liczbą")
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"{fld}={v} poza [0,1]")
        out[fld] = v

    for fld in ("thesis", "confession", "reversal_trigger"):
        v = str(d.get(fld) or "").strip()
        if not v:
            raise ValueError(f"puste pole {fld}")
        # echo placeholderów ze szkieletu = niewypełnione pole
        if v.lower().startswith(("obowiązkowe", "jedno zdanie", "1-2 zdania", "liczba 0")):
            raise ValueError(f"pole {fld} wygląda na echo szkieletu: {v[:60]!r}")
        out[fld] = v

    risks = d.get("key_risks")
    if not isinstance(risks, list) or not risks:
        raise ValueError("key_risks nie jest niepustą listą")
    out["key_risks"] = [str(r).strip() for r in risks if str(r).strip()]
    if not out["key_risks"]:
        raise ValueError("key_risks po odfiltrowaniu puste")
    return out


_RETRY_CORRECTION = (
    "POPRZEDNIA ODPOWIEDŹ BYŁA NIEPOPRAWNA ({err}). Odpowiedz JESZCZE RAZ, "
    "WYŁĄCZNIE poprawnym obiektem JSON według schematu z instrukcji — wszystkie "
    "pola obowiązkowe, bez tekstu poza JSON."
)


async def run_decision_v2(
    settings: Settings,
    *,
    run_id: str,
    model_key_label: str,
    wire_model: str,
    reasoning: bool = False,
    sample_idx: int = 0,
    persist: bool = True,
    sampling: dict[str, Any] | None = None,
    prompt_dir: str = V2_PROMPT_DIR,
) -> dict[str, Any]:
    """Manager-only v2 dla danego upstreamu. Zwraca dict wiersza decyzji
    (zapis do SQLite gdy persist=True). Retry ×3 na niepoprawny JSON."""
    up: Upstream = await load_upstream(settings.agents_dsn, run_id)

    render_vars: dict[str, Any] = context_to_render_vars(up.ctx)
    render_vars["agent_context_payload"] = _slim_manager_brief(
        render_vars.get("agent_context_payload") or {}
    )
    render_vars["analyst_results_text"] = render_analyst_results(up.analysts)
    # 1 runda debaty jak w v1 (wszystkie tury z cache; history = AgentResult z tur)
    render_vars["debate_history_text"] = render_debate_history(
        [t.result for t in up.turns]
    )

    prompt = PromptLoader(Path(prompt_dir)).load(V2_PROMPT_FILE)
    system, user = prompt.render(**render_vars)

    smp = {**V2_SAMPLING, **(sampling or {})}
    gemma = GemmaClient(
        base_url=settings.gb10_base_url, api_key=settings.gb10_api_key,
        model_key=wire_model, reasoning=reasoning,
        temperature_override=smp.get("temperature"),
        top_p=smp.get("top_p"), top_k=smp.get("top_k"),
    )

    messages = [Message(role="user", content=user)]
    parsed: dict[str, Any] | None = None
    last_err = ""
    resp = None
    for attempt in range(3):
        resp = await gemma.complete(
            system=system, messages=messages, max_tokens=V2_MAX_TOKENS,
            temperature=smp.get("temperature", 1.0), response_format_json=True,
        )
        try:
            parsed = parse_v2_output(resp.text)
            break
        except ValueError as e:
            last_err = str(e)
            messages = [
                Message(role="user", content=user),
                Message(role="assistant", content=resp.text or ""),
                Message(role="user", content=_RETRY_CORRECTION.format(err=last_err)),
            ]
    if parsed is None:
        raise RuntimeError(f"manager v2 zwrócił niepoprawny JSON po 3 próbach: {last_err}")

    # kompletny output v2 (w tym sondy) — do decision_json/manager_structured
    v2_full = {**parsed, "prompt_hash": prompt.prompt_hash, "prompt_version": prompt.version}

    row: dict[str, Any] = {
        "upstream_run_id": up.run_id,
        "ticker": up.ticker,
        "as_of_date": up.as_of.isoformat(),
        "model_key": model_key_label,
        "sample_idx": sample_idx,
        "wire_model": wire_model,
        "variant": up.variant,
        "manager_system": system,
        "manager_user": user,
        "trader_system": None,
        "trader_user": None,
        "manager_output_md": resp.text,
        "manager_structured": store.dumps(v2_full),
        "director_stance": parsed["stance"],
        "director_strategy": None,
        # manager-only: stance finalny = stance Dyrektora
        "stance": parsed["stance"],
        "confidence": parsed["confidence"],
        "time_horizon": parsed["time_horizon"],
        "investment_style": None,
        "thesis_one_liner": parsed["thesis"],
        "key_risks": store.dumps(parsed["key_risks"]),
        "decision_json": store.dumps(v2_full),
        "mgr_input_tokens": resp.input_tokens,
        "mgr_output_tokens": resp.output_tokens,
        "mgr_duration_s": resp.duration_s,
        "trd_input_tokens": None,
        "trd_output_tokens": None,
        "trd_duration_s": None,
        "cost_usd": 0.0,
        "reasoning_used": 1 if reasoning else 0,
        "source": "gemma-v2",
    }
    if persist:
        row["_id"] = store.upsert_decision(settings.sqlite_path, row)
    return row
