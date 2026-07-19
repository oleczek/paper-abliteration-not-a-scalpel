"""Silnik backtestu portfelowego + strategie (single-model i hybrydowe).

Model portfela: co tydzień strategia patrzy na panel komórek (spółka×tydzień z sygnałami modeli)
i emituje KSIĄŻKĘ — mapę ticker → waga (dodatnia=long, ujemna=short). Zwrot tygodnia portfela =
Σ waga_i * gross_i. Łączymy tygodnie multiplikatywnie → equity curve. Benchmark = WIG20 long.

Ważenie: equal-weight po aktywnych nazwach, znormalizowane tak, że Σ|waga| = 1 (pełne
zaangażowanie, brutto 100%). To czyni strategie porównywalnymi bez ukrytej dźwigni.

Strategia = funkcja(list[Cell]) -> dict[ticker, waga_znormalizowana_lub_surowa]. Silnik sam
normalizuje Σ|waga|=1 (chyba że pusto → tydzień poza rynkiem, zwrot 0).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from gielda_uncensored.reporting.panel import CENS, UNC, Cell, by_week

Book = dict[str, float]
Strategy = Callable[[list[Cell]], Book]


# ── strategie single-model ────────────────────────────────────────────────────

def _consensus_book(cells: list[Cell], model: str, *, conviction_weight: bool,
                    conviction_min: float = 0.0, long_only: bool = False) -> Book:
    book: Book = {}
    for c in cells:
        s = c.sig(model)
        if s is None or s.direction == 0:
            continue
        if s.conviction < conviction_min:
            continue
        if long_only and s.direction < 0:
            continue
        w = s.conviction if conviction_weight else 1.0
        book[c.ticker] = s.direction * w
    return book


def strat_model(model: str, *, conviction_weight: bool = False,
                conviction_min: float = 0.0, long_only: bool = False) -> Strategy:
    def f(cells: list[Cell]) -> Book:
        return _consensus_book(cells, model, conviction_weight=conviction_weight,
                               conviction_min=conviction_min, long_only=long_only)
    return f


# ── strategie hybrydowe (dwa modele razem) ────────────────────────────────────

def strat_agree(*, conviction_weight: bool = False) -> Strategy:
    """Trejduj TYLKO gdy oba modele zgadzają się co do kierunku. Selektywność > pokrycie."""
    def f(cells: list[Cell]) -> Book:
        book: Book = {}
        for c in cells:
            a, b = c.sig(CENS), c.sig(UNC)
            if a is None or b is None:
                continue
            if a.direction == 0 or a.direction != b.direction:
                continue
            w = (a.conviction + b.conviction) / 2 if conviction_weight else 1.0
            book[c.ticker] = a.direction * w
        return book
    return f


def strat_disagree(side: str) -> Strategy:
    """Trejduj tylko komórki SPORNE, biorąc kierunek wskazanego modelu (side='cens'|'unc').
    Test 'kto ma rację, gdy się kłócą'."""
    model = CENS if side == "cens" else UNC
    def f(cells: list[Cell]) -> Book:
        book: Book = {}
        for c in cells:
            a, b = c.sig(CENS), c.sig(UNC)
            if a is None or b is None or a.direction == 0 or b.direction == 0:
                continue
            if a.direction == b.direction:
                continue  # zgoda → pomiń
            s = c.sig(model)
            book[c.ticker] = float(s.direction)
        return book
    return f


def strat_mega_ensemble(*, conviction_weight: bool = False) -> Strategy:
    """Pula próbek OBU modeli traktowana jak jeden większy komitet (ważona liczbą próbek).
    p_bull_łączne = (n_c*p_c + n_u*p_u)/(n_c+n_u)."""
    def f(cells: list[Cell]) -> Book:
        book: Book = {}
        for c in cells:
            a, b = c.sig(CENS), c.sig(UNC)
            parts = [(s.n, s.p_bull) for s in (a, b) if s is not None]
            if not parts:
                continue
            ntot = sum(n for n, _ in parts)
            p = sum(n * pb for n, pb in parts) / ntot
            direction = 1 if p > 0.5 else -1 if p < 0.5 else 0
            if direction == 0:
                continue
            w = abs(2 * p - 1) if conviction_weight else 1.0
            book[c.ticker] = direction * w
        return book
    return f


def strat_debias(model: str) -> Strategy:
    """Neutralizacja biasu kierunkowego: co tydzień odejmij od głosów medianę p_bull tego modelu,
    kierunek = znak (p_bull - mediana_tygodnia). Usuwa stałe przechylenie (np. +13pp bull uncensored)."""
    def f(cells: list[Cell]) -> Book:
        vals = [(c, c.sig(model)) for c in cells if c.sig(model) is not None]
        if not vals:
            return {}
        ps = sorted(s.p_bull for _, s in vals)
        m = ps[len(ps) // 2]
        book: Book = {}
        for c, s in vals:
            d = s.p_bull - m
            if abs(d) < 1e-9:
                continue
            book[c.ticker] = 1.0 if d > 0 else -1.0
        return book
    return f


# ── benchmark „głupich" strategii (sanity / kontrola) ─────────────────────────

def strat_always_long() -> Strategy:
    """Kup wszystko equal-weight (proxy szerokiego rynku równoważonego)."""
    def f(cells: list[Cell]) -> Book:
        return {c.ticker: 1.0 for c in cells}
    return f


# ── silnik ────────────────────────────────────────────────────────────────────

@dataclass
class WeeklyPoint:
    week: str
    ret: float           # zwrot portfela w tygodniu
    bench: float         # zwrot WIG20 (long)
    n_long: int
    n_short: int
    gross_exposure: float
    book: Book = field(default_factory=dict)  # znormalizowane wagi tego tygodnia
    equity: float = 1.0  # kumulatywnie (wypełniane po przebiegu)
    bench_equity: float = 1.0


def _normalize(book: Book) -> Book:
    tot = sum(abs(w) for w in book.values())
    if tot <= 0:
        return {}
    return {k: w / tot for k, w in book.items()}


def backtest(cells: list[Cell], strategy: Strategy) -> list[WeeklyPoint]:
    """Uruchom strategię tydzień po tygodniu, zbuduj equity curve. Zwrot tygodnia = Σ w_i*gross_i."""
    pts: list[WeeklyPoint] = []
    for week, wcells in by_week(cells).items():
        raw = strategy(wcells)
        book = _normalize(raw)
        gross_by_t = {c.ticker: c.gross for c in wcells}
        bench_vals = [c.bench for c in wcells if c.bench is not None]
        bench = sum(bench_vals) / len(bench_vals) if bench_vals else 0.0
        ret = sum(w * gross_by_t.get(t, 0.0) for t, w in book.items())
        n_long = sum(1 for w in raw.values() if w > 0)
        n_short = sum(1 for w in raw.values() if w < 0)
        pts.append(WeeklyPoint(
            week=week, ret=ret, bench=bench, n_long=n_long, n_short=n_short,
            gross_exposure=sum(abs(w) for w in book.values()), book=book,
        ))
    # equity kumulatywne
    eq = bq = 1.0
    for p in pts:
        eq *= (1 + p.ret)
        bq *= (1 + p.bench)
        p.equity = eq
        p.bench_equity = bq
    return pts


# ── metryki ──────────────────────────────────────────────────────────────────

PERIODS_PER_YEAR = 52.0


@dataclass
class Metrics:
    name: str
    weeks: int
    total_return: float
    ann_return: float
    ann_vol: float
    sharpe: float
    max_drawdown: float
    win_weeks: float          # udział tygodni z dodatnim zwrotem
    avg_week: float
    total_bench: float
    alpha_total: float        # total_return - total_bench
    avg_alpha_week: float
    avg_n_long: float
    avg_n_short: float
    turnover: float = 0.0     # śr. |Δwaga| między tygodniami (wypełniane osobno)
    extra: dict = field(default_factory=dict)


def _max_drawdown(equity: list[float]) -> float:
    peak = -math.inf
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, e / peak - 1)
    return mdd


def metrics(name: str, pts: list[WeeklyPoint]) -> Metrics:
    if not pts:
        return Metrics(name, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    rets = [p.ret for p in pts]
    benchs = [p.bench for p in pts]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n if n > 1 else 0.0
    vol = math.sqrt(var)
    ann_ret = (pts[-1].equity) ** (PERIODS_PER_YEAR / n) - 1 if pts[-1].equity > 0 else -1.0
    ann_vol = vol * math.sqrt(PERIODS_PER_YEAR)
    sharpe = (mean / vol * math.sqrt(PERIODS_PER_YEAR)) if vol > 1e-12 else 0.0
    alphas = [r - b for r, b in zip(rets, benchs)]
    return Metrics(
        name=name, weeks=n,
        total_return=pts[-1].equity - 1,
        ann_return=ann_ret, ann_vol=ann_vol, sharpe=sharpe,
        max_drawdown=_max_drawdown([p.equity for p in pts]),
        win_weeks=sum(1 for r in rets if r > 0) / n,
        avg_week=mean,
        total_bench=pts[-1].bench_equity - 1,
        alpha_total=(pts[-1].equity - 1) - (pts[-1].bench_equity - 1),
        avg_alpha_week=sum(alphas) / n,
        avg_n_long=sum(p.n_long for p in pts) / n,
        avg_n_short=sum(p.n_short for p in pts) / n,
    )


# ── rejestr strategii do raportu ──────────────────────────────────────────────

def turnover(pts: list[WeeklyPoint]) -> float:
    """Średni tygodniowy obrót: 0.5·Σ|w_t − w_{t−1}| po unii tickerów. 1.0 = pełna wymiana książki."""
    if len(pts) < 2:
        return 0.0
    tot = 0.0
    for prev, cur in zip(pts, pts[1:]):
        keys = set(prev.book) | set(cur.book)
        tot += 0.5 * sum(abs(cur.book.get(k, 0.0) - prev.book.get(k, 0.0)) for k in keys)
    return tot / (len(pts) - 1)


def sleeve_returns(cells: list[Cell], model: str) -> tuple[float, float]:
    """Rozłóż zwrot modelu na RĘKAW LONG i RĘKAW SHORT (skumulowane), by oddzielić
    'skill kierunkowy' od bety rynku. Każdy rękaw equal-weight, znormalizowany osobno."""
    long_eq = short_eq = 1.0
    for week, wcells in by_week(cells).items():
        longs = [(c.ticker, c.gross) for c in wcells
                 if (s := c.sig(model)) is not None and s.direction > 0]
        shorts = [(c.ticker, c.gross) for c in wcells
                  if (s := c.sig(model)) is not None and s.direction < 0]
        if longs:
            long_eq *= (1 + sum(g for _, g in longs) / len(longs))
        if shorts:
            short_eq *= (1 + sum(-g for _, g in shorts) / len(shorts))
    return long_eq - 1, short_eq - 1


def default_strategies() -> dict[str, Strategy]:
    """Zestaw strategii liczonych w raporcie. Klucz = etykieta na wykresach."""
    return {
        "CENSORED cons": strat_model(CENS),
        "UNCENSORED cons": strat_model(UNC),
        "CENSORED conv-wt": strat_model(CENS, conviction_weight=True),
        "UNCENSORED conv-wt": strat_model(UNC, conviction_weight=True),
        "HYBRID agree": strat_agree(),
        "HYBRID agree conv-wt": strat_agree(conviction_weight=True),
        "MEGA-ensemble": strat_mega_ensemble(),
        "DISAGREE→cens": strat_disagree("cens"),
        "DISAGREE→unc": strat_disagree("unc"),
        "DEBIAS unc": strat_debias(UNC),
        "ALWAYS-LONG (EW)": strat_always_long(),
    }
