"""GemmaClient — OpenAI-compatible klient dla self-hosted gemma4 na gb10.

Implementuje protokół `gielda_agents.llm.base.LLMClient`, więc wchodzi wprost do
`ManagerAgent`/`TraderAgent` (BaseAgent przyjmuje dowolny LLMClient) — bez ruszania
`gielda_agents/llm/factory.py` (który jest zablokowany na prefiks `deepseek-`).

Różnice względem DeepSeekClient (świadome):
- NIE wysyła `extra_body={"thinking": ...}` ani `reasoning_effort` — to rozszerzenia
  DeepSeeka; waniliowy serwer OpenAI-compatible (vLLM/llama.cpp/ollama) mógłby na nich
  zwrócić 400.
- Toleruje brak pól `usage.*` (self-hosted czasem ich nie zwraca) → 0.
- `cost_usd=0.0` (model własny, brak cennika).
- Opcjonalny `reasoning` (domyślnie OFF) — gdy user włączy reasoning w gemmie; kształt
  parametru zależy od serwera, więc trzymamy go za flagą.
- `response_format_json` jest soft-fail: gdy serwer odrzuci `response_format` (400),
  ponawiamy BEZ niego (trader i tak zwykle emituje czysty JSON, a parser tradera jest
  tolerancyjny).
"""
from __future__ import annotations

import time
from typing import Any

import openai
from openai import AsyncOpenAI

from gielda_agents.llm.base import LLMResponse, Message
from gielda_agents.llm.retry import EmptyContentError, llm_retrying


class GemmaClient:
    """Async LLM client dla gemma4/gemma4-uncensored na gb10 (OpenAI-compatible)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_key: str,
        reasoning: bool = False,
        reasoning_effort: str = "medium",
        timeout: float = 300.0,
        temperature_override: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
    ) -> None:
        # model_key = nazwa modelu wysyłana na serwer (pole `model`). Etykieta do
        # porównań (gb10-gemma4 vs gb10-gemma4-uncensored) żyje osobno w SQLite.
        self.model_key = model_key
        self._reasoning = reasoning
        self._reasoning_effort = reasoning_effort
        # Oś temperatury/samplingu (falsyfikacja „excision of doubt"): nadpisania
        # per-klient — persony podają swoje temp (0.8/0.6 DeepSeek-tuned), a klient
        # może wymusić np. rekomendację Gemmy (temp 1.0, top_p 0.95, top_k 64).
        self._temperature_override = temperature_override
        self._top_p = top_p
        self._top_k = top_k  # brak w OpenAI API → extra_body (vLLM rozumie)
        # max_retries=0 → jedynym retrierem jest tenacity (`llm_retrying`) z mądrym
        # predykatem; timeout twardy, żeby martwe połączenie nie wisiało w nieskończoność.
        self._client = AsyncOpenAI(
            api_key=api_key or "sk-none",
            base_url=base_url,
            max_retries=0,
            timeout=timeout,
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 4096,
        temperature: float = 1.0,
        response_format_json: bool = False,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
    ) -> LLMResponse:
        oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        oai_messages.extend(m.model_dump() for m in messages)

        base_kwargs: dict[str, Any] = {
            "model": self.model_key,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": (self._temperature_override
                            if self._temperature_override is not None else temperature),
        }
        if self._top_p is not None:
            base_kwargs["top_p"] = self._top_p
        if frequency_penalty != 0.0:
            base_kwargs["frequency_penalty"] = frequency_penalty
        if presence_penalty != 0.0:
            base_kwargs["presence_penalty"] = presence_penalty
        # NIE ustawiamy extra_body={"thinking":...} ani reasoning_effort (deepseek-only).
        extra_body: dict[str, Any] = {}
        if self._top_k is not None:
            extra_body["top_k"] = self._top_k
        if self._reasoning:
            # opt-in; serwer-zależne — jeśli gb10 odrzuci, wyłącz GB10_REASONING.
            extra_body["reasoning_effort"] = self._reasoning_effort
        if extra_body:
            base_kwargs["extra_body"] = extra_body

        async for attempt in llm_retrying():
            with attempt:
                kwargs = dict(base_kwargs)
                if response_format_json:
                    kwargs["response_format"] = {"type": "json_object"}
                t0 = time.perf_counter()
                try:
                    resp = await self._client.chat.completions.create(**kwargs)
                except openai.BadRequestError:
                    # soft-fail: serwer nie zna response_format → ponów bez niego
                    if not response_format_json:
                        raise
                    resp = await self._client.chat.completions.create(**base_kwargs)
                duration = time.perf_counter() - t0

                if not resp.choices:
                    raise EmptyContentError("gemma returned no choices")
                choice = resp.choices[0]
                text = (choice.message.content or "").strip()
                if not text:
                    raise EmptyContentError("gemma returned empty content")

                usage = resp.usage
                input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", 0) or 0

                return LLMResponse(
                    text=text,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=0,  # self-hosted: brak rozszerzenia cache
                    cost_usd=0.0,          # model własny, brak cennika
                    model_key=self.model_key,
                    duration_s=duration,
                    reasoning_content=getattr(choice.message, "reasoning_content", None),
                    raw_finish_reason=choice.finish_reason,
                )

        raise RuntimeError("unreachable")  # pragma: no cover
