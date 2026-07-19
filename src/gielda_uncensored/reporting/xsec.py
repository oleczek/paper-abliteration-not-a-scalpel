"""Cross-sectional rank: long top / short bottom po p_bull w przekroju tygodnia.

Market-neutral z konstrukcji — odejmuje średnią byczość tygodnia, więc automatycznie
kasuje confound bull-tiltu i mierzy WZGLĘDNY sygnał (czy model wie, KTÓRA spółka
lepsza od której, a nie czy rynek urośnie). Jedyny znany sposób, w jaki taki model
bywa użyteczny mimo zerowego per-name skillu kierunkowego.

Dwie konstrukcje wag (obie Σ|w|=1 po normalizacji w silniku):
- rank:      w ∝ (ranga frakcyjna p_bull − średnia ranga); remisy = średnia ranga,
             używa wszystkich nazw, brak arbitralnego progu.
- quintile:  long top 20% / short bottom 20%; remisy na granicy dostają wagę ułamkową
             (sloty dzielone równo między nazwy o granicznym p_bull).

Degeneracja (wszystkie p_bull równe) → pusta książka → tydzień poza rynkiem (ret 0).
PnL liczony na surowym `gross` (NIE na kolumnie alpha_pct, która karze shorty vs
długi benchmark) — dla portfela dolarowo-neutralnego benchmarkiem jest 0.
"""
from __future__ import annotations

from dataclasses import dataclass

from gielda_uncensored.config import Settings
from gielda_uncensored.reporting import portfolio as P
from gielda_uncensored.reporting import stats as S
from gielda_uncensored.reporting.panel import CENS, UNC, Cell, build_panel, weeks_of

MODELS = {"CENSORED": CENS, "UNCENSORED": UNC}


# ── konstrukcje wag ───────────────────────────────────────────────────────────

def _fractional_ranks(vals: list[float]) -> list[float]:
    """Rangi 1..n ze średnią rangą dla remisów."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # rangi 1-indeksowane
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def rank_book(cells: list[Cell], model: str) -> P.Book:
    """w ∝ ranga(p_bull) − średnia ranga. Dolarowo-neutralne z konstrukcji (Σw=0)."""
    pairs = [(c.ticker, c.sig(model).p_bull) for c in cells if c.sig(model) is not None]
    if len(pairs) < 2:
        return {}
    ranks = _fractional_ranks([p for _, p in pairs])
    mean_rank = sum(ranks) / len(ranks)
    book = {t: r - mean_rank for (t, _), r in zip(pairs, ranks)}
    return {t: w for t, w in book.items() if abs(w) > 1e-12}


def quintile_book(cells: list[Cell], model: str, frac: float = 0.2) -> P.Book:
    """Long top-frac / short bottom-frac po p_bull; remisy na granicy → wagi ułamkowe."""
    pairs = [(c.ticker, c.sig(model).p_bull) for c in cells if c.sig(model) is not None]
    n = len(pairs)
    if n < 2:
        return {}
    k = max(1, round(frac * n))

    def side_weights(desc: bool) -> dict[str, float]:
        srt = sorted(pairs, key=lambda x: x[1], reverse=desc)
        boundary = srt[k - 1][1]
        if desc:
            better = [t for t, p in pairs if p > boundary]
        else:
            better = [t for t, p in pairs if p < boundary]
        at = [t for t, p in pairs if p == boundary]
        w: dict[str, float] = {t: 1.0 for t in better}
        share = (k - len(better)) / len(at) if at else 0.0
        for t in at:
            w[t] = share
        return {t: x for t, x in w.items() if x > 1e-12}

    longs = side_weights(desc=True)
    shorts = side_weights(desc=False)
    book: P.Book = {}
    for t, w in longs.items():
        book[t] = book.get(t, 0.0) + w
    for t, w in shorts.items():
        book[t] = book.get(t, 0.0) - w
    return {t: w for t, w in book.items() if abs(w) > 1e-12}


def strat_xsec(model: str, *, weighting: str = "rank") -> P.Strategy:
    builder = rank_book if weighting == "rank" else quintile_book
    def f(cells: list[Cell]) -> P.Book:
        return builder(cells, model)
    return f


# ── metryki / raport ──────────────────────────────────────────────────────────

@dataclass
class XsecResult:
    model: str
    weighting: str
    weeks: int
    active_weeks: int          # tygodnie z niepustą książką
    mean_week: float
    total: float
    sharpe: float
    win_weeks: float
    corr_bench: float          # sanity market-neutrality
    ci_lo: float
    ci_hi: float               # CI95% mean_week (block bootstrap)
    mean_is: float             # 70% pierwszych tygodni
    mean_oos: float            # 30% ostatnich
    avg_dispersion: float      # śr. cross-sectional σ(p_bull) — diagnostyka degeneracji


def _corr(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 2:
        return float("nan")
    ma, mb = S.mean(a), S.mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    va = sum((x - ma) ** 2 for x in a) / n
    vb = sum((y - mb) ** 2 for y in b) / n
    return cov / (va * vb) ** 0.5 if va > 0 and vb > 0 else float("nan")


def _dispersion(cells: list[Cell], model: str) -> float:
    ps = [c.sig(model).p_bull for c in cells if c.sig(model) is not None]
    if len(ps) < 2:
        return 0.0
    m = S.mean(ps)
    return (sum((p - m) ** 2 for p in ps) / len(ps)) ** 0.5


def xsec_result(cells: list[Cell], model_label: str, model: str,
                weighting: str) -> XsecResult:
    pts = P.backtest(cells, strat_xsec(model, weighting=weighting))
    rets = [p.ret for p in pts]
    benchs = [p.bench for p in pts]
    m = P.metrics(f"xsec-{weighting}", pts)
    lo, _, hi = S.block_bootstrap_ci(rets, S.mean)
    k = max(1, int(round(len(rets) * 0.7)))
    from gielda_uncensored.reporting.panel import by_week
    disp = [_dispersion(wc, model) for wc in by_week(cells).values()]
    return XsecResult(
        model=model_label, weighting=weighting, weeks=len(pts),
        active_weeks=sum(1 for p in pts if p.book),
        mean_week=S.mean(rets), total=m.total_return, sharpe=m.sharpe,
        win_weeks=m.win_weeks, corr_bench=_corr(rets, benchs),
        ci_lo=lo, ci_hi=hi,
        mean_is=S.mean(rets[:k]), mean_oos=S.mean(rets[k:]) if rets[k:] else float("nan"),
        avg_dispersion=S.mean(disp),
    )


def render_xsec(settings: Settings, *, min_samples: int = 5) -> str:
    cells = build_panel(settings, min_samples=min_samples)
    L = [
        "CROSS-SECTIONAL RANK (market-neutral: long góra / short dół przekroju p_bull)",
        f"tygodnie: {len(weeks_of(cells))}   komórki: {len(cells)}   benchmark = 0 (dolarowo-neutralny)",
        "",
        f"{'model':<11} {'wagi':<9} {'ret/tydz':>9} {'CI95%':>18} {'total':>7} "
        f"{'Sharpe':>6} {'win%':>5} {'corr(WIG20)':>11} {'IS':>7} {'OOS':>7} {'σ(p_bull)':>9}",
    ]
    for label, model in MODELS.items():
        for weighting in ("rank", "quintile"):
            r = xsec_result(cells, label, model, weighting)
            ci = f"[{r.ci_lo*100:+.2f},{r.ci_hi*100:+.2f}]"
            L.append(
                f"{r.model:<11} {r.weighting:<9} {r.mean_week*100:>+8.2f}% {ci:>18} "
                f"{r.total*100:>+6.1f}% {r.sharpe:>6.2f} {r.win_weeks*100:>4.0f}% "
                f"{r.corr_bench:>+10.2f} {r.mean_is*100:>+6.2f}% {r.mean_oos*100:>+6.2f}% "
                f"{r.avg_dispersion:>9.3f}"
            )
    L += [
        "",
        "Czytanie: ret/tydz z CI95% (block bootstrap po tygodniach) — CI zawierające 0 = brak",
        "dowodu sygnału względnego. corr(WIG20) ~0 potwierdza neutralność. σ(p_bull) niska",
        "(<0.2) = model daje wszystkim podobny sygnał → ranking jest szumem.",
    ]
    return "\n".join(L)
