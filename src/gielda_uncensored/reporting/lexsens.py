"""Analiza wrażliwości leksykonu confession-fade.

Zarzut do odparcia: „wynik jest artefaktem doboru listy słów" (spec-mining). Testy:
1. CZĘSTOŚCI — które tokeny w ogóle strzelają; czy jeden dominuje licznik.
2. LEAVE-ONE-OUT — usuń każdy token z osobna, przelicz efekt LOW−HIGH; worst-case.
3. LOSOWE PODZBIORY — zatrzymaj 75% / 50% tokenów (200 losowań), rozkład efektu:
   % losowań z efektem > 0 i z CI95% w całości > 0.
4. WARIANT NORMALIZACJI — surowe zliczenia zamiast per-100-słów.

Efekt = średnia tygodniowa różnica zwrotów LOW−HIGH doubt (jak w `confession`),
liczona osobno dla obu modeli. Leksykon stabilny = wnioski nie zależą od
pojedynczych tokenów ani od połowy listy.
"""
from __future__ import annotations

import random

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.reporting import confession as C
from gielda_uncensored.reporting import portfolio as P
from gielda_uncensored.reporting import stats as S
from gielda_uncensored.reporting.panel import CENS, UNC, Cell, build_panel

MODELS = {"CENSORED": CENS, "UNCENSORED": UNC}


def _effect(cells: list[Cell], model: str,
            doubt: dict[tuple[str, str, str], float]) -> list[float]:
    """Seria tygodniowa LOW−HIGH dla danej mapy doubt."""
    pts_low = P.backtest(cells, C.strat_fade(model, keep="low", doubt=doubt))
    pts_high = P.backtest(cells, C.strat_fade(model, keep="high", doubt=doubt))
    return [lo.ret - hi.ret for lo, hi in zip(pts_low, pts_high)]


def token_hits(records, tokens: list[str], *, side: str) -> dict[str, int]:
    """Ile razy token strzela jako KONTR-lęk (u decyzji przeciwnego kierunku)."""
    hits = {t: 0 for t in tokens}
    want_stance = "bear" if side == "up" else "bull"  # up-tokeny liczą się u bearów
    for _, _, _, stance, conf in records:
        if want_stance not in stance:
            continue
        t = conf.lower()
        for tok in tokens:
            hits[tok] += t.count(tok)
    return hits


def render_sensitivity(settings: Settings, *, min_samples: int = 5,
                       n_draws: int = 200, seed: int = 1337) -> str:
    rows = store.fetch_for_compare(settings.sqlite_path)
    records = C.extract_records(rows)
    cells = build_panel(settings, min_samples=min_samples)

    def effects_for(up: list[str], down: list[str]) -> dict[str, list[float]]:
        doubt = C.doubt_map_from_records(records, up=up, down=down)
        return {label: _effect(cells, model, doubt) for label, model in MODELS.items()}

    base = effects_for(C.UP_TOKENS, C.DOWN_TOKENS)
    L = ["ANALIZA WRAŻLIWOŚCI LEKSYKONU (confession-fade, efekt = LOW−HIGH %/tydz)", ""]
    for label, d in base.items():
        lo, _, hi = S.block_bootstrap_ci(d, S.mean)
        L.append(f"BASELINE {label:<11} {S.mean(d)*100:+.2f}%/t  CI95% [{lo*100:+.2f}, {hi*100:+.2f}]")

    # 1. częstości tokenów (kontr-kierunkowe strzały)
    L += ["", "1) CZĘSTOŚCI (strzały kontr-kierunkowe; dominacja jednego tokenu = ryzyko):"]
    for side, tokens in (("up", C.UP_TOKENS), ("down", C.DOWN_TOKENS)):
        hits = token_hits(records, tokens, side=side)
        tot = sum(hits.values()) or 1
        top = sorted(hits.items(), key=lambda kv: -kv[1])[:6]
        L.append(f"   {side.upper():<4} (Σ={tot}): " +
                 "  ".join(f"{t}={h} ({h/tot*100:.0f}%)" for t, h in top))

    # 2. leave-one-out (jeden przebieg na token, oba modele naraz)
    L += ["", "2) LEAVE-ONE-OUT (najgorszy efekt po usunięciu pojedynczego tokenu):"]
    loo: dict[str, list[tuple[str, float]]] = {label: [] for label in MODELS}
    for side, tokens in (("up", C.UP_TOKENS), ("down", C.DOWN_TOKENS)):
        for tok in tokens:
            up = [t for t in C.UP_TOKENS if t != tok]
            down = [t for t in C.DOWN_TOKENS if t != tok]
            eff = effects_for(up, down)
            for label in MODELS:
                loo[label].append((f"{side}:{tok}", S.mean(eff[label])))
    for label in MODELS:
        effs = [e for _, e in loo[label]]
        worst_tok, worst_e = min(loo[label], key=lambda kv: kv[1])
        L.append(f"   {label:<11} zakres [{min(effs)*100:+.2f}, {max(effs)*100:+.2f}]%/t  "
                 f"min po usunięciu '{worst_tok}' ({worst_e*100:+.2f})  "
                 f"({sum(1 for e in effs if e > 0)}/{len(effs)} > 0)")

    # 3. losowe podzbiory 75% i 50%
    rng = random.Random(seed)
    for keep_frac in (0.75, 0.50):
        L += ["", f"3) LOSOWE PODZBIORY {int(keep_frac*100)}% tokenów ({n_draws} losowań):"]
        means: dict[str, list[float]] = {label: [] for label in MODELS}
        ci_pos: dict[str, int] = {label: 0 for label in MODELS}
        for _ in range(n_draws):
            up = [t for t in C.UP_TOKENS if rng.random() < keep_frac] or list(C.UP_TOKENS)
            down = [t for t in C.DOWN_TOKENS if rng.random() < keep_frac] or list(C.DOWN_TOKENS)
            for label, d in effects_for(up, down).items():
                m = S.mean(d)
                means[label].append(m)
                lo, _, _ = S.block_bootstrap_ci(d, S.mean, n_boot=1000)
                ci_pos[label] += lo > 0
        for label in MODELS:
            ms = sorted(means[label])
            L.append(f"   {label:<11} mediana {ms[len(ms)//2]*100:+.2f}%/t  "
                     f"zakres [{ms[0]*100:+.2f}, {ms[-1]*100:+.2f}]  "
                     f">0: {sum(1 for m in ms if m > 0)/len(ms)*100:.0f}%  "
                     f"CI95%>0: {ci_pos[label]/n_draws*100:.0f}%")

    # 4. normalizacja: surowe zliczenia zamiast per-100-słów
    L += ["", "4) WARIANT NORMALIZACJI (surowe zliczenia tokenów, bez dzielenia przez długość):"]
    from collections import defaultdict
    acc = defaultdict(list)
    for ticker, as_of, model, stance, conf in records:
        direction = 1 if "bull" in stance else -1 if "bear" in stance else 0
        if direction == 0:
            continue
        t = conf.lower()
        opp = C._count(t, C.DOWN_TOKENS) if direction > 0 else C._count(t, C.UP_TOKENS)
        acc[(ticker, as_of, model)].append(float(opp))
    raw_map = {k: S.mean(v) for k, v in acc.items()}
    for label, model in MODELS.items():
        d = _effect(cells, model, raw_map)
        lo, _, hi = S.block_bootstrap_ci(d, S.mean)
        L.append(f"   {label:<11} {S.mean(d)*100:+.2f}%/t  CI95% [{lo*100:+.2f}, {hi*100:+.2f}]")

    L += [
        "",
        "Czytanie: wynik jest odporny, jeśli (a) żaden pojedynczy token nie zeruje efektu,",
        "(b) zdecydowana większość losowych podzbiorów daje efekt > 0, (c) zmiana normalizacji",
        "nie zmienia znaku. Wtedy zarzut spec-miningu leksykonu upada.",
    ]
    return "\n".join(L)
