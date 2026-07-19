"""Zestawienie decyzji per model na wspólnym upstreamie + import baseline deepseeka.

To jest test hipotezy: te same wejścia (upstream_run_id), różne modele (model_key) →
różnica w stance/PnL/alfie.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.upstream.loader import load_upstream

BASELINE_MODEL_KEY = "deepseek-v4-pro-baseline"


async def import_baseline(settings: Settings, run_id: str) -> dict[str, Any] | None:
    """Skopiuj decyzję deepseeka z bazy gielda-agents (read-only) do naszego SQLite,
    żeby mieć 3-way compare (gemma / gemma-uncensored / deepseek) na tym samym upstreamie."""
    up = await load_upstream(settings.agents_dsn, run_id)
    b = up.baseline_decision
    if b is None:
        return None
    row: dict[str, Any] = {
        "upstream_run_id": up.run_id,
        "ticker": up.ticker,
        "as_of_date": up.as_of.isoformat(),
        "model_key": BASELINE_MODEL_KEY,
        "sample_idx": 0,  # baseline = 1 produkcyjna decyzja (bez resamplingu)
        "wire_model": "deepseek-v4-pro",
        "variant": up.variant,
        "manager_system": None, "manager_user": None,
        "trader_system": None, "trader_user": None,
        "manager_output_md": None,
        "manager_structured": None,
        "director_stance": b.get("director_stance"),
        "director_strategy": b.get("strategy"),
        "stance": b.get("stance"),
        "confidence": b.get("confidence"),
        "time_horizon": b.get("time_horizon"),
        "investment_style": b.get("investment_style"),
        "thesis_one_liner": b.get("thesis_one_liner"),
        "key_risks": store.dumps(b.get("key_risks")),
        "decision_json": store.dumps(b),
        "mgr_input_tokens": None, "mgr_output_tokens": None, "mgr_duration_s": None,
        "trd_input_tokens": None, "trd_output_tokens": None, "trd_duration_s": None,
        "cost_usd": 0.0, "reasoning_used": 0,
        "source": "baseline-import",
    }
    row["_id"] = store.upsert_decision(settings.sqlite_path, row)
    return row


def _fmt_pct(v: Any) -> str:
    return f"{v * 100:+.2f}%" if isinstance(v, (int, float)) else "—"


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def _std(xs: list[float]) -> float | None:
    return statistics.pstdev(xs) if len(xs) > 1 else (0.0 if xs else None)


def _aggregate_model(model_key: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Agreguj próbki jednego modelu: rozkład stance + statystyki PnL/alfy."""
    signed = [s["signed_return_pct"] for s in samples if isinstance(s.get("signed_return_pct"), (int, float))]
    alpha = [s["alpha_pct"] for s in samples if isinstance(s.get("alpha_pct"), (int, float))]
    conf = [s["confidence"] for s in samples if isinstance(s.get("confidence"), (int, float))]
    stances = Counter(s.get("stance") for s in samples)
    return {
        "model_key": model_key,
        "n": len(samples),
        "stance_dist": dict(stances),
        "stance_mode": stances.most_common(1)[0][0] if stances else None,
        "conf_mean": _mean(conf),
        "signed_mean": _mean(signed),
        "signed_std": _std(signed),
        "alpha_mean": _mean(alpha),
        "alpha_std": _std(alpha),
        "bench_return_pct": next((s.get("bench_return_pct") for s in samples), None),
        "n_priced": len(signed),
    }


def build_comparison(settings: Settings, *, ticker: str | None = None) -> list[dict[str, Any]]:
    """Grupuj po (ticker, as_of, upstream_run_id); w każdej grupie agreguj próbki per model."""
    rows = store.fetch_for_compare(settings.sqlite_path, ticker=ticker)
    groups: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        groups[(r["ticker"], r["as_of_date"], r["upstream_run_id"])][r["model_key"]].append(r)
    out = []
    for (tkr, as_of, run_id), by_model in sorted(groups.items()):
        models = [_aggregate_model(mk, s) for mk, s in sorted(by_model.items())]
        out.append({"ticker": tkr, "as_of": as_of, "upstream_run_id": run_id, "models": models})
    return out


def _fmt_dist(dist: dict[str, Any]) -> str:
    return " ".join(f"{k}:{v}" for k, v in dist.items() if k) or "—"


def render_comparison_text(groups: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for g in groups:
        lines.append(f"\n=== {g['ticker']}  as_of={g['as_of']}  upstream={g['upstream_run_id'][:8]} ===")
        lines.append(
            f"  {'model':30} {'n':>3} {'stance(mode)':14} "
            f"{'signed(μ)':>10} {'±σ':>7} {'alpha(μ)':>10} {'±σ':>7}  stance_dist"
        )
        for m in g["models"]:
            lines.append(
                f"  {m['model_key']:30} {m['n']:>3} {str(m['stance_mode']):14} "
                f"{_fmt_pct(m['signed_mean']):>10} {_fmt_pct(m['signed_std']):>7} "
                f"{_fmt_pct(m['alpha_mean']):>10} {_fmt_pct(m['alpha_std']):>7}  "
                f"{_fmt_dist(m['stance_dist'])}"
            )
    return "\n".join(lines) if lines else "(brak decyzji — najpierw uruchom `decide`/`pnl`)"
