"""Metryki dyspozycji decyzyjnej — do wyłowienia różnic censored vs uncensored.

Filozofia: abliteracja usuwa „kierunek odmowy", ale hipoteza dotyczy DYSPOZYCJI
(bycia miłym/hedżującego/optymistycznego). Jeśli te proxy się ruszą między modelami
na IDENTYCZNYM upstreamie → mamy mechanizm; jeśli nie → abliteracja jest za wąska na tę
hipotezę (i PnL też się nie ruszy). Liczymy hojnie — dużo metryk, pogrupowanych po hipotezach.

Metryki liczone per model (agregat po wszystkich decyzjach) + sekcja PAROWA na wspólnych
komórkach (upstream_run_id) — bezpośredni kontrast dwóch modeli.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from typing import Any, Callable

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store

# Polskie tokeny hedżujące (miękkość/niepewność) — liczone w ceo_comment+tezie.
HEDGE_TOKENS = [
    "może", "możliwe", "prawdopodobn", "niepewn", "trudno powiedzieć", "zależy",
    "ostrożn", "aczkolwiek", "wydaje się", "raczej", "chyba", "wątpl", "niejasn",
    "z jednej strony", "z drugiej strony", "umiarkowan", "trudno", "nie wiadomo",
    "sugeruje", "wskazywać może", "potencjaln",
]
STRONG_CONF = 0.70
HEDGED_CONF = 0.55


def _dj(r: dict) -> dict:
    return json.loads(r["decision_json"]) if r.get("decision_json") else {}


def _ms(r: dict) -> dict:
    return json.loads(r["manager_structured"]) if r.get("manager_structured") else {}


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.fmean(xs) if xs else None


def _std(xs: list[float]) -> float | None:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.pstdev(xs) if len(xs) > 1 else (0.0 if xs else None)


def _rate(flags: list[bool]) -> float | None:
    flags = [f for f in flags if f is not None]
    return sum(1 for f in flags if f) / len(flags) if flags else None


def _entropy(p: float) -> float:
    if p <= 0 or p >= 1:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def _hedge_score(text: str | None) -> float | None:
    if not text:
        return None
    t = text.lower()
    words = max(len(t.split()), 1)
    hits = sum(t.count(tok) for tok in HEDGE_TOKENS)
    return hits / words * 100.0


def compute_model_metrics(rows: list[dict]) -> dict[str, Any]:
    """Wszystkie metryki dyspozycji dla jednego modelu (lista jego decyzji)."""
    djs = [_dj(r) for r in rows]
    mss = [_ms(r) for r in rows]
    n = len(rows)
    stance = [r.get("stance") for r in rows]
    dir_stance = [r.get("director_stance") for r in rows]
    conf = [d.get("confidence") for d in djs]

    # per-cell (upstream_run_id) rozkład stance → flip/entropia
    by_cell: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_cell[r["upstream_run_id"]].append(r.get("stance"))
    multi = {k: v for k, v in by_cell.items() if len(v) >= 2}
    cell_pbull = [sum(1 for s in v if s == "bull") / len(v) for v in multi.values()]
    flip_rate = _rate([0 < p < 1 for p in cell_pbull]) if cell_pbull else None
    entropy_mean = _mean([_entropy(p) for p in cell_pbull]) if cell_pbull else None

    # ceny/PnL
    signed = [r.get("signed_return_pct") for r in rows if r.get("pnl_status") == "closed"]
    alpha = [r.get("alpha_pct") for r in rows if r.get("pnl_status") == "closed"]
    closed = [r for r in rows if r.get("pnl_status") == "closed"]
    hit = [(r.get("signed_return_pct") or 0) > 0 for r in closed
           if isinstance(r.get("signed_return_pct"), (int, float))]

    # kalibracja: hit-rate w koszu wysokiej vs niskiej pewności
    hi = [(r.get("signed_return_pct") or 0) > 0 for r in closed
          if isinstance(_dj(r).get("confidence"), (int, float)) and _dj(r)["confidence"] >= STRONG_CONF
          and isinstance(r.get("signed_return_pct"), (int, float))]
    lo = [(r.get("signed_return_pct") or 0) > 0 for r in closed
          if isinstance(_dj(r).get("confidence"), (int, float)) and _dj(r)["confidence"] < STRONG_CONF
          and isinstance(r.get("signed_return_pct"), (int, float))]
    calib_gap = (_rate(hi) - _rate(lo)) if (_rate(hi) is not None and _rate(lo) is not None) else None

    # confidence bull vs bear (asymetria = optymizm: mocniej commituje byka)
    conf_bull = _mean([d.get("confidence") for d in djs if d.get("stance") == "bull"])
    conf_bear = _mean([d.get("confidence") for d in djs if d.get("stance") == "bear"])

    def dist(vals: list) -> dict:
        c = Counter(v for v in vals if v is not None)
        tot = sum(c.values()) or 1
        return {k: round(100 * v / tot) for k, v in c.most_common()}

    alpha_mean = _mean(alpha)
    alpha_std = _std(alpha)

    # --- long vs short (kierunek pozycji = stance) ---
    def _side(s: str | None) -> str | None:
        if s in ("strong_bull", "bull", "lean_bull"):
            return "long"
        if s in ("strong_bear", "bear", "lean_bear"):
            return "short"
        return None

    longs = [r for r in closed if _side(r.get("stance")) == "long"
             and isinstance(r.get("signed_return_pct"), (int, float))]
    shorts = [r for r in closed if _side(r.get("stance")) == "short"
              and isinstance(r.get("signed_return_pct"), (int, float))]

    return {
        "n_decisions": n,
        "n_cells": len(by_cell),
        # --- H1: bias byczy / „grzeczny optymizm" ---
        "bull_rate": _rate([s == "bull" for s in stance]),
        "director_bull_rate": _rate([s == "bull" for s in dir_stance]),
        "net_bias": (_rate([s == "bull" for s in stance]) - _rate([s == "bear" for s in stance]))
                    if n else None,
        "veto_rate": _rate([r.get("stance") != r.get("director_stance") for r in rows
                            if r.get("director_stance")]),
        # --- H2: zdecydowanie / hedżowanie ---
        "conf_mean": _mean(conf),
        "conf_std": _std(conf),
        "conf_strong_rate": _rate([c >= STRONG_CONF for c in conf if isinstance(c, (int, float))]),
        "conf_hedged_rate": _rate([c <= HEDGED_CONF for c in conf if isinstance(c, (int, float))]),
        "conf_bull": conf_bull,
        "conf_bear": conf_bear,
        "conf_asymmetry": (conf_bull - conf_bear) if (conf_bull and conf_bear) else None,
        "flip_rate": flip_rate,
        "decision_entropy": entropy_mean,
        # --- H3: ostrożność / język ---
        "key_risks_mean": _mean([len(d.get("key_risks") or []) for d in djs]),
        "thesis_len": _mean([len(d.get("thesis_one_liner") or "") for d in djs]),
        "ceo_len": _mean([len(d.get("ceo_comment") or "") for d in djs]),
        "confession_len": _mean([len(d.get("confession") or "") for d in djs]),
        "confession_rate": _rate([bool((d.get("confession") or "").strip()) for d in djs]),
        "hedge_score": _mean([_hedge_score(
            (d.get("ceo_comment") or "") + " " + (d.get("thesis_one_liner") or "")) for d in djs]),
        # --- H4: strategia / styl ---
        "contrarian_rate": _rate([r.get("director_strategy") == "contrarian" for r in rows
                                  if r.get("director_strategy")]),
        "style_dist": dist([d.get("investment_style") for d in djs]),
        "horizon_dist": dist([d.get("time_horizon") for d in djs]),
        "opp_trend_dist": dist([m.get("opposing_trend_strength") for m in mss]),
        # --- H5: skill ekonomiczny / kalibracja ---
        "hit_rate": _rate(hit),
        "signed_mean": _mean(signed),
        "alpha_mean": alpha_mean,
        "alpha_pos_rate": _rate([a > 0 for a in alpha if isinstance(a, (int, float))]),
        "alpha_sharpe": (alpha_mean / alpha_std) if (alpha_mean is not None and alpha_std) else None,
        "calib_gap": calib_gap,
        # --- H6: gadatliwość / wysiłek ---
        "mgr_out_tok": _mean([r.get("mgr_output_tokens") for r in rows]),
        "trd_out_tok": _mean([r.get("trd_output_tokens") for r in rows]),
        # --- LONG vs SHORT ---
        "long_n": len(longs),
        "long_hit": _rate([r["signed_return_pct"] > 0 for r in longs]),
        "long_signed": _mean([r["signed_return_pct"] for r in longs]),
        "long_alpha": _mean([r["alpha_pct"] for r in longs if isinstance(r.get("alpha_pct"), (int, float))]),
        "short_n": len(shorts),
        "short_hit": _rate([r["signed_return_pct"] > 0 for r in shorts]),
        "short_signed": _mean([r["signed_return_pct"] for r in shorts]),
        "short_alpha": _mean([r["alpha_pct"] for r in shorts if isinstance(r.get("alpha_pct"), (int, float))]),
    }


# Definicja wierszy tabeli: (klucz, etykieta, format, „kierunek" interpretacji dla hipotezy)
# kierunek: '↑bycie-miłym' = wyższa wartość zgodna z 'cenzurowany bardziej miły'
_ROWS: list[tuple[str, str, str]] = [
    ("__H1", "— H1: BIAS BYCZY / OPTYMIZM —", ""),
    ("bull_rate", "  % decyzji BULL", "pct"),
    ("director_bull_rate", "  % dyrektor BULL", "pct"),
    ("net_bias", "  net bias (bull-bear)", "pct_signed"),
    ("conf_asymmetry", "  asymetria conf (bull-bear)", "f3s"),
    ("veto_rate", "  % veto tradera na dyrektora", "pct"),
    ("__H2", "— H2: ZDECYDOWANIE / HEDŻOWANIE —", ""),
    ("conf_mean", "  śr. confidence", "f3"),
    ("conf_std", "  odch. confidence", "f3"),
    ("conf_strong_rate", "  % conf≥0.70 (mocne)", "pct"),
    ("conf_hedged_rate", "  % conf≤0.55 (miękkie)", "pct"),
    ("flip_rate", "  flip-rate (zmiana bull↔bear)", "pct"),
    ("decision_entropy", "  entropia decyzji /komórkę", "f3"),
    ("__H3", "— H3: OSTROŻNOŚĆ / JĘZYK —", ""),
    ("key_risks_mean", "  śr. liczba key_risks", "f1"),
    ("hedge_score", "  hedge-score /100 słów", "f2"),
    ("thesis_len", "  dł. tezy (znaki)", "f0"),
    ("ceo_len", "  dł. ceo_comment", "f0"),
    ("confession_len", "  dł. confession", "f0"),
    ("confession_rate", "  % z confession", "pct"),
    ("__H4", "— H4: STRATEGIA / STYL —", ""),
    ("contrarian_rate", "  % contrarian (dyrektor)", "pct"),
    ("__H5", "— H5: SKILL / KALIBRACJA —", ""),
    ("hit_rate", "  hit-rate (kierunek)", "pct"),
    ("signed_mean", "  śr. signed return", "pct_signed"),
    ("alpha_mean", "  śr. alpha", "pct_signed"),
    ("alpha_pos_rate", "  % decyzji +alpha", "pct"),
    ("alpha_sharpe", "  alpha 'sharpe'", "f2"),
    ("calib_gap", "  kalibracja (hit hi-lo conf)", "pct_signed"),
    ("__LS", "— LONG vs SHORT (hit/signed główne; alpha dla short ma niesprawiedliwy bench) —", ""),
    ("long_n", "  LONG: liczba", "f0"),
    ("long_hit", "  LONG: hit-rate", "pct"),
    ("long_signed", "  LONG: śr. signed", "pct_signed"),
    ("long_alpha", "  LONG: śr. alpha", "pct_signed"),
    ("short_n", "  SHORT: liczba", "f0"),
    ("short_hit", "  SHORT: hit-rate", "pct"),
    ("short_signed", "  SHORT: śr. signed", "pct_signed"),
    ("short_alpha", "  SHORT: śr. alpha", "pct_signed"),
    ("__H6", "— H6: GADATLIWOŚĆ —", ""),
    ("mgr_out_tok", "  śr. tokeny managera", "f0"),
    ("trd_out_tok", "  śr. tokeny tradera", "f0"),
]


def _fmt(val: Any, kind: str) -> str:
    if val is None:
        return "—"
    if kind == "pct":
        return f"{val * 100:.0f}%"
    if kind == "pct_signed":
        return f"{val * 100:+.2f}%"
    if kind == "f3":
        return f"{val:.3f}"
    if kind == "f3s":
        return f"{val:+.3f}"
    if kind == "f2":
        return f"{val:.2f}"
    if kind == "f1":
        return f"{val:.1f}"
    if kind == "f0":
        return f"{val:.0f}"
    return str(val)


def render_disposition(settings: Settings, *, ticker: str | None = None) -> str:
    rows = store.fetch_for_compare(settings.sqlite_path, ticker=ticker)
    if not rows:
        return "(brak decyzji)"
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_model[r["model_key"]].append(r)
    models = sorted(by_model)
    metrics = {mk: compute_model_metrics(rs) for mk, rs in by_model.items()}

    w = 30
    lines = ["", "DYSPOZYCJA DECYZYJNA — metryki per model" + (f" (ticker={ticker})" if ticker else "")]
    header = f"{'metryka':34}" + "".join(f"{m[:w]:>{w}}" for m in models)
    lines.append(header)
    lines.append("-" * len(header))
    for key, label, kind in _ROWS:
        if key.startswith("__"):
            lines.append(label)
            continue
        cells = "".join(f"{_fmt(metrics[m].get(key), kind):>{w}}" for m in models)
        lines.append(f"{label:34}{cells}")

    # rozkłady (osobno, bo wieloczłonowe)
    lines.append("— rozkłady —")
    for key, label in [("style_dist", "  styl inwest."), ("horizon_dist", "  horyzont"),
                       ("opp_trend_dist", "  siła przeciw-trendu")]:
        lines.append(f"{label:34}" + "".join(
            f"{(' '.join(f'{k}:{v}' for k, v in (metrics[m].get(key) or {}).items()) or '—')[:w]:>{w}}"
            for m in models))

    # sekcja parowa: kontrast na wspólnych komórkach
    if len(models) >= 2:
        lines.append("")
        lines.append("KONTRAST PAROWY (na wspólnych upstream_run_id)")
        for i in range(len(models)):
            for j in range(i + 1, len(models)):
                lines.append(_pairwise(by_model[models[i]], by_model[models[j]], models[i], models[j]))
    return "\n".join(lines)


def _mode_stance(stances: list[str]) -> str | None:
    c = Counter(s for s in stances if s)
    return c.most_common(1)[0][0] if c else None


def _pairwise(rows_a: list[dict], rows_b: list[dict], a: str, b: str) -> str:
    def cells(rows):
        d = defaultdict(list)
        for r in rows:
            d[r["upstream_run_id"]].append(r)
        return d
    ca, cb = cells(rows_a), cells(rows_b)
    shared = sorted(set(ca) & set(cb))
    if not shared:
        return f"  {a} vs {b}: brak wspólnych komórek"
    agree = 0
    alpha_diffs = []
    a_wins = 0
    for rid in shared:
        sa = _mode_stance([r["stance"] for r in ca[rid]])
        sb = _mode_stance([r["stance"] for r in cb[rid]])
        if sa == sb:
            agree += 1
        aa = _mean([r.get("alpha_pct") for r in ca[rid] if r.get("pnl_status") == "closed"])
        ab = _mean([r.get("alpha_pct") for r in cb[rid] if r.get("pnl_status") == "closed"])
        if aa is not None and ab is not None:
            alpha_diffs.append(aa - ab)
            if aa > ab:
                a_wins += 1
    agree_pct = 100 * agree / len(shared)
    md = _mean(alpha_diffs)
    md_s = f"{md * 100:+.2f}pp" if md is not None else "—"
    win_s = f"{a_wins}/{len(alpha_diffs)}" if alpha_diffs else "—"
    return (f"  {a} vs {b}: komórek={len(shared)}  zgodność stance={agree_pct:.0f}%  "
            f"śr. Δalpha({a}-{b})={md_s}  {a} lepszy w {win_s}")
