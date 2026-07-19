"""Confession-vs-stance fade: rozbieżność deklarowanego kierunku z lękiem w `confession`.

Trader po decyzji pisze swobodny tekst „confession" (sekretna obawa). Gatunkowo to
zwykle „gram na X, ale boję się Y" — mierzymy więc NASILENIE lęku kontr-kierunkowego
(tokeny kierunku przeciwnego do własnego stance, na 100 słów), nie sam fakt jego
wystąpienia. Hipoteza: decyzje z silnym kontr-lękiem są gorsze → filtr/fade.
Prawdziwa niepewność siedzi w tekście, nie w liczbie (confidence zapadło do 0.6).

Wszystko z istniejących danych; doubt znany w momencie decyzji (sobota) → filtr
low-doubt nie ma lookahead (split po medianie tygodnia = cross-sectional, jak DEBIAS).
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.reporting import portfolio as P
from gielda_uncensored.reporting import stats as S
from gielda_uncensored.reporting.panel import CENS, UNC, Cell, build_panel, by_week

MODELS = {"CENSORED": CENS, "UNCENSORED": UNC}

# Tokeny kierunkowe PL (prefiksy, lowercase). Celowo konserwatywne — lepiej nie doliczyć
# niż liczyć fałszywe trafienia.
UP_TOKENS = [
    "wzrost", "wzrośn", "wzrosn", "urośn", "urosn", "rośnie", "wystrzel", "odbici",
    "odbije", "rajd", "w górę", "hoss", "zwyżk", "podskocz", "wybije", "wybici",
    "przebije", "drożej",
]
DOWN_TOKENS = [
    "spad", "spadn", "runie", "przecen", "wyprzeda", "w dół", "bess", "korekt",
    "zniżk", "osun", "tąpn", "zjazd", "nurkuj",
]


def _count(text: str, tokens: list[str]) -> int:
    return sum(text.count(tok) for tok in tokens)


def doubt_score(stance: str | None, confession: str | None, *,
                up: list[str] | None = None, down: list[str] | None = None) -> float | None:
    """Lęk kontr-kierunkowy na 100 słów confession. None gdy brak tekstu/kierunku.
    `up`/`down` pozwalają podmienić leksykon (analiza wrażliwości)."""
    if not confession or not stance:
        return None
    direction = 1 if "bull" in stance else -1 if "bear" in stance else 0
    if direction == 0:
        return None
    t = confession.lower()
    words = max(len(t.split()), 1)
    opp = _count(t, down if down is not None else DOWN_TOKENS) if direction > 0 \
        else _count(t, up if up is not None else UP_TOKENS)
    return opp / words * 100.0


def aligned_score(stance: str | None, confession: str | None) -> float | None:
    """Tokeny WŁASNEGO kierunku na 100 słów (kontrola: czy confession w ogóle mówi o kierunku)."""
    if not confession or not stance:
        return None
    direction = 1 if "bull" in stance else -1 if "bear" in stance else 0
    if direction == 0:
        return None
    t = confession.lower()
    words = max(len(t.split()), 1)
    own = _count(t, UP_TOKENS) if direction > 0 else _count(t, DOWN_TOKENS)
    return own / words * 100.0


# ── agregacja per komórka ─────────────────────────────────────────────────────

def extract_records(rows: list[dict]) -> list[tuple[str, str, str, str, str]]:
    """(ticker, as_of, model_key, stance, confession) — JSON parsowany RAZ
    (analiza wrażliwości przelicza doubt setki razy na tych samych tekstach)."""
    out = []
    for r in rows:
        dj = json.loads(r["decision_json"]) if r.get("decision_json") else {}
        # gemma trzyma confession na top-level; baseline deepseeka w extras
        conf = dj.get("confession") or (dj.get("extras") or {}).get("confession")
        if conf and r.get("stance"):
            out.append((r["ticker"], r["as_of_date"], r["model_key"], r["stance"], conf))
    return out


def doubt_map_from_records(records: list[tuple[str, str, str, str, str]], *,
                           up: list[str] | None = None,
                           down: list[str] | None = None) -> dict[tuple[str, str, str], float]:
    acc: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for ticker, as_of, model, stance, conf in records:
        d = doubt_score(stance, conf, up=up, down=down)
        if d is not None:
            acc[(ticker, as_of, model)].append(d)
    return {k: S.mean(v) for k, v in acc.items()}


def cell_doubt_map(rows: list[dict]) -> dict[tuple[str, str, str], float]:
    """(ticker, as_of, model_key) → średni doubt po próbkach komórki."""
    return doubt_map_from_records(extract_records(rows))


def _week_median_split(cells: list[Cell], model: str,
                       doubt: dict[tuple[str, str, str], float]) -> dict[str, bool]:
    """ticker → is_low_doubt w obrębie JEDNEGO tygodnia (mediana przekroju; remis → low)."""
    vals = [(c.ticker, doubt.get((c.ticker, c.as_of, model)))
            for c in cells if doubt.get((c.ticker, c.as_of, model)) is not None]
    if len(vals) < 2:
        return {t: True for t, _ in vals}
    ds = sorted(d for _, d in vals)
    med = ds[(len(ds) - 1) // 2]  # dolna mediana — przy parzystym n nie wciąga górnej połowy do LOW
    return {t: d <= med for t, d in vals}


def strat_fade(model: str, *, keep: str = "low",
               doubt: dict[tuple[str, str, str], float] | None = None) -> P.Strategy:
    """strat_model ograniczony do komórek low-doubt (keep='low') albo high-doubt ('high')."""
    assert doubt is not None
    def f(cells: list[Cell]) -> P.Book:
        split = _week_median_split(cells, model, doubt)
        book: P.Book = {}
        for c in cells:
            s = c.sig(model)
            if s is None or s.direction == 0 or c.ticker not in split:
                continue
            if split[c.ticker] != (keep == "low"):
                continue
            book[c.ticker] = float(s.direction)
        return book
    return f


# ── raport ────────────────────────────────────────────────────────────────────

@dataclass
class FadeRow:
    model: str
    variant: str          # FULL | LOW-doubt | HIGH-doubt
    mean_week: float
    total: float
    sharpe: float
    hit: float            # per-cell hit-rate komórek w książkach
    cells: int


def _percell_hit(cells: list[Cell], model: str,
                 doubt: dict[tuple[str, str, str], float], which: str | None) -> tuple[float, int]:
    hit = n = 0
    for week, wcells in by_week(cells).items():
        split = _week_median_split(wcells, model, doubt)
        for c in wcells:
            s = c.sig(model)
            if s is None or s.direction == 0 or c.gross == 0:
                continue
            if which is not None:
                if c.ticker not in split or split[c.ticker] != (which == "low"):
                    continue
            n += 1
            if (c.gross > 0) == (s.direction > 0):
                hit += 1
    return (hit / n if n else float("nan")), n


def render_confession(settings: Settings, *, min_samples: int = 5) -> str:
    rows = store.fetch_for_compare(settings.sqlite_path)
    doubt = cell_doubt_map(rows)
    cells = build_panel(settings, min_samples=min_samples)

    # statystyki tekstowe per model (per decyzja, nie komórka)
    L = ["CONFESSION-VS-STANCE FADE (lęk kontr-kierunkowy w tekście confession)", ""]
    for label, model in MODELS.items():
        ds, als = [], []
        for r in rows:
            if r["model_key"] != model:
                continue
            dj = json.loads(r["decision_json"]) if r.get("decision_json") else {}
            conf = dj.get("confession") or (dj.get("extras") or {}).get("confession")
            d = doubt_score(r.get("stance"), conf)
            a = aligned_score(r.get("stance"), conf)
            if d is not None:
                ds.append(d)
            if a is not None:
                als.append(a)
        any_rate = sum(1 for d in ds if d > 0) / len(ds) if ds else float("nan")
        L.append(f"{label:<11} doubt/100słów: μ={S.mean(ds):.2f}  %z kontr-lękiem: {any_rate*100:.0f}%  "
                 f"aligned/100słów: μ={S.mean(als):.2f}  (n={len(ds)} decyzji)")

    L += ["", f"{'model':<11} {'wariant':<11} {'ret/tydz':>9} {'total':>7} {'Sharpe':>6} "
              f"{'hit%':>5} {'komórek':>7}"]
    diffs_by_model: dict[str, tuple[float, float, float]] = {}
    for label, model in MODELS.items():
        pts_full = P.backtest(cells, P.strat_model(model))
        pts_low = P.backtest(cells, strat_fade(model, keep="low", doubt=doubt))
        pts_high = P.backtest(cells, strat_fade(model, keep="high", doubt=doubt))
        for variant, pts, which in (("FULL", pts_full, None), ("LOW-doubt", pts_low, "low"),
                                    ("HIGH-doubt", pts_high, "high")):
            m = P.metrics(variant, pts)
            h, n = _percell_hit(cells, model, doubt, which)
            L.append(f"{label:<11} {variant:<11} {S.mean([p.ret for p in pts])*100:>+8.2f}% "
                     f"{m.total_return*100:>+6.1f}% {m.sharpe:>6.2f} {h*100:>4.0f}% {n:>7}")
        d = [lo.ret - hi.ret for lo, hi in zip(pts_low, pts_high)]
        ci_lo, _, ci_hi = S.block_bootstrap_ci(d, S.mean)
        diffs_by_model[label] = (S.mean(d), ci_lo, ci_hi)

    L.append("")
    for label, (d_mean, lo, hi) in diffs_by_model.items():
        L.append(f"{label}: LOW−HIGH doubt = {d_mean*100:+.2f}%/tydz  CI95% [{lo*100:+.2f}, {hi*100:+.2f}] "
                 f"(block bootstrap; 0 w CI = brak dowodu, że kontr-lęk coś przewiduje)")
    L += [
        "",
        "Czytanie: jeśli LOW-doubt > HIGH-doubt (CI poza 0) → tekst confession niesie",
        "kalibrację, której nie ma w liczbie confidence → fade jako filtr + wynik do papera.",
    ]
    return "\n".join(L)
