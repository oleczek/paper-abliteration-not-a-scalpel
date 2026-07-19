"""Regime-conditional split: tygodnie WIG20-up vs WIG20-down.

Centralny confound-killer papera: jeśli przewaga uncensored istnieje TYLKO w up-weeks
(a w down-weeks bull-tilt szkodzi), to edge = beta (przechylenie bycze), nie skill.
Wszystko liczone z istniejącego panelu — zero nowych biegów.

Metryki per (model × reżim):
- bull-rate konsensusu (udział komórek z kierunkiem long),
- hit-rate per komórka (kierunek == znak gross),
- średni tygodniowy zwrot/alfa portfela `strat_model` (te same wagi co w raporcie),
- diff-in-diff: (alfa_unc − alfa_cens) w up-weeks vs down-weeks, z bootstrap CI.
"""
from __future__ import annotations

from dataclasses import dataclass

from gielda_uncensored.config import Settings
from gielda_uncensored.reporting import portfolio as P
from gielda_uncensored.reporting import stats as S
from gielda_uncensored.reporting.panel import CENS, UNC, Cell, build_panel, by_week

MODELS = {"CENSORED": CENS, "UNCENSORED": UNC}


def split_weeks(cells: list[Cell]) -> tuple[list[str], list[str]]:
    """Tygodnie z dodatnim / ujemnym zwrotem WIG20 (bench identyczny w obrębie tygodnia)."""
    up, down = [], []
    for week, wcells in by_week(cells).items():
        bench = next((c.bench for c in wcells if c.bench is not None), None)
        if bench is None:
            continue
        (up if bench > 0 else down).append(week)
    return up, down


@dataclass
class RegimeRow:
    model: str
    regime: str            # up | down
    weeks: int
    cells: int
    bull_rate: float       # udział komórek z konsensusem long
    hit_rate: float        # kierunek == znak gross
    avg_week_ret: float    # średni tygodniowy zwrot portfela strat_model
    avg_week_alpha: float  # średni tygodniowy (ret − bench)
    avg_bench: float


def _weekly_series(cells: list[Cell], model: str) -> dict[str, tuple[float, float]]:
    """week → (ret, bench) portfela strat_model (wagi jak w raporcie, Σ|w|=1)."""
    pts = P.backtest(cells, P.strat_model(model))
    return {p.week: (p.ret, p.bench) for p in pts}


def _cell_stats(wcells: list[Cell], model: str) -> tuple[int, int, int, int]:
    """(n_cells, n_bull, n_hit, n_scored) dla komórek z sygnałem modelu."""
    n = bull = hit = scored = 0
    for c in wcells:
        s = c.sig(model)
        if s is None or s.direction == 0:
            continue
        n += 1
        if s.direction > 0:
            bull += 1
        if c.gross != 0:
            scored += 1
            if (c.gross > 0) == (s.direction > 0):
                hit += 1
    return n, bull, hit, scored


def regime_rows(cells: list[Cell]) -> list[RegimeRow]:
    up, down = split_weeks(cells)
    weeks_map = by_week(cells)
    out: list[RegimeRow] = []
    for label, model in MODELS.items():
        weekly = _weekly_series(cells, model)
        for regime, wks in (("up", up), ("down", down)):
            n = bull = hit = scored = 0
            rets, alphas, benchs = [], [], []
            for w in wks:
                cn, cb, ch, cs = _cell_stats(weeks_map[w], model)
                n, bull, hit, scored = n + cn, bull + cb, hit + ch, scored + cs
                if w in weekly:
                    r, b = weekly[w]
                    rets.append(r)
                    alphas.append(r - b)
                    benchs.append(b)
            out.append(RegimeRow(
                model=label, regime=regime, weeks=len(wks), cells=n,
                bull_rate=bull / n if n else float("nan"),
                hit_rate=hit / scored if scored else float("nan"),
                avg_week_ret=S.mean(rets), avg_week_alpha=S.mean(alphas),
                avg_bench=S.mean(benchs),
            ))
    return out


def diff_in_diff(cells: list[Cell]) -> dict:
    """(alfa_unc − alfa_cens) per tydzień; różnica średnich up − down z bootstrap CI.

    Dodatnia i istotna → przewaga uncensored żyje w up-weeks = beta, nie skill.
    """
    up, down = split_weeks(cells)
    wu, wc = _weekly_series(cells, UNC), _weekly_series(cells, CENS)
    d = {w: (wu[w][0] - wu[w][1]) - (wc[w][0] - wc[w][1])
         for w in wu.keys() & wc.keys()}
    d_up = [d[w] for w in up if w in d]
    d_down = [d[w] for w in down if w in d]
    lo, med, hi = S.diff_ci_two_groups(d_up, d_down)
    return {
        "edge_up": S.mean(d_up), "edge_down": S.mean(d_down),
        "did": S.mean(d_up) - S.mean(d_down), "ci_lo": lo, "ci_med": med, "ci_hi": hi,
        "n_up": len(d_up), "n_down": len(d_down),
    }


def render_regime(settings: Settings, *, min_samples: int = 5) -> str:
    cells = build_panel(settings, min_samples=min_samples)
    up, down = split_weeks(cells)
    L = [
        "REGIME-CONDITIONAL SPLIT (WIG20 up-weeks vs down-weeks)",
        f"tygodnie: up={len(up)} down={len(down)}   komórki panelu: {len(cells)}",
        "",
        f"{'model':<11} {'reżim':<5} {'tyg':>3} {'komórek':>7} {'bull%':>6} {'hit%':>6} "
        f"{'ret/tydz':>9} {'alfa/tydz':>9} {'bench/tydz':>10}",
    ]
    for r in regime_rows(cells):
        L.append(
            f"{r.model:<11} {r.regime:<5} {r.weeks:>3} {r.cells:>7} "
            f"{r.bull_rate*100:>5.0f}% {r.hit_rate*100:>5.0f}% "
            f"{r.avg_week_ret*100:>+8.2f}% {r.avg_week_alpha*100:>+8.2f}% {r.avg_bench*100:>+9.2f}%"
        )
    dd = diff_in_diff(cells)
    L += [
        "",
        "DIFF-IN-DIFF (przewaga UNC nad CENS w alfie tygodniowej):",
        f"  up-weeks:   {dd['edge_up']*100:+.2f}%/tydz (n={dd['n_up']})",
        f"  down-weeks: {dd['edge_down']*100:+.2f}%/tydz (n={dd['n_down']})",
        f"  DiD (up−down): {dd['did']*100:+.2f}pp  CI95% [{dd['ci_lo']*100:+.2f}, {dd['ci_hi']*100:+.2f}]",
        "",
        "Interpretacja: DiD > 0 = przewaga uncensored skoncentrowana w up-weeks → beta",
        "(bull-tilt), nie skill. CI z bootstrapu iid po tygodniach (reżimy nieciągłe).",
    ]
    return "\n".join(L)
