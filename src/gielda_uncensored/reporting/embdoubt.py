"""Semantyczny doubt-score na embeddingach (qwen3-embedding @ haxor/ollama).

Odpowiedź na zarzut spec-miningu leksykonu: zamiast listy słów — OŚ KIERUNKOWA
w przestrzeni embeddingów. Kotwice to parafrazy różniące się WYŁĄCZNIE kierunkiem
(„boję się, że wzrośnie" vs „boję się, że spadnie"), więc różnica ich centroidów
izoluje kierunek lęku od reszty semantyki gatunku confession.

doubt_emb(decyzja) = cos(confession, centroid lęku PRZECIWNEGO do stance)
                   − cos(confession, centroid lęku WŁASNEGO kierunku)

Różnica podobieństw kontroluje „bazowe" podobieństwo do gatunku lękowego.
Dalej identyczny pipeline jak leksykon: średnia per komórka → split po dolnej
medianie tygodnia → backtest LOW vs HIGH → CI. Embeddingi cache'owane w SQLite
(data/confession_emb.db) — ponowny bieg nic nie liczy.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sqlite3
from array import array
from pathlib import Path

import httpx

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.reporting import confession as C
from gielda_uncensored.reporting import portfolio as P
from gielda_uncensored.reporting import stats as S
from gielda_uncensored.reporting.panel import CENS, UNC, build_panel

MODELS = {"CENSORED": CENS, "UNCENSORED": UNC}

EMB_URL = os.environ.get("EMB_URL", "http://localhost:11434/api/embed")  # ollama, bez auth
EMB_MODEL = "qwen3-embedding:0.6b"
CACHE_DB = "data/confession_emb.db"

# Kotwice: pary lustrzane — różnią się tylko kierunkiem ruchu.
ANCHORS_FEAR_UP = [
    "Boję się, że kurs mocno wzrośnie.",
    "Obawiam się, że cena wystrzeli w górę.",
    "Mam obawę, że akcje czeka silne odbicie i rajd.",
    "Po cichu boję się, że notowania jednak urosną.",
]
ANCHORS_FEAR_DOWN = [
    "Boję się, że kurs mocno spadnie.",
    "Obawiam się, że cena runie w dół.",
    "Mam obawę, że akcje czeka silna przecena i wyprzedaż.",
    "Po cichu boję się, że notowania jednak spadną.",
]


# ── cache / embedding ─────────────────────────────────────────────────────────

def _cache(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS emb ("
                 "decision_id INTEGER PRIMARY KEY, model TEXT NOT NULL, vec BLOB NOT NULL)")
    return conn


async def _embed_batch(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    r = await client.post(EMB_URL, json={"model": EMB_MODEL, "input": texts}, timeout=120.0)
    r.raise_for_status()
    return r.json()["embeddings"]


async def embed_decisions(rows: list[dict], cache_path: str = CACHE_DB,
                          batch: int = 64, echo=print) -> dict[int, list[float]]:
    """Embeddingi confession per decision_id, z cache. rows = fetch_for_compare."""
    todo: dict[int, str] = {}
    for r in rows:
        dj = json.loads(r["decision_json"]) if r.get("decision_json") else {}
        conf = dj.get("confession")
        if conf and r.get("stance"):
            todo[r["id"]] = conf
    conn = _cache(cache_path)
    try:
        have = {row[0] for row in conn.execute("SELECT decision_id FROM emb WHERE model=?",
                                               (EMB_MODEL,))}
        missing = [(i, t) for i, t in todo.items() if i not in have]
        if missing:
            echo(f"embedduję {len(missing)} confession (cache: {len(have)})…")
            async with httpx.AsyncClient() as client:
                for k in range(0, len(missing), batch):
                    chunk = missing[k:k + batch]
                    embs = await _embed_batch(client, [t for _, t in chunk])
                    conn.executemany(
                        "INSERT OR REPLACE INTO emb (decision_id, model, vec) VALUES (?,?,?)",
                        [(i, EMB_MODEL, array("f", e).tobytes()) for (i, _), e in zip(chunk, embs)],
                    )
                    conn.commit()
                    if (k // batch) % 20 == 0:
                        echo(f"  {min(k + batch, len(missing))}/{len(missing)}")
        out: dict[int, list[float]] = {}
        for did, blob in conn.execute("SELECT decision_id, vec FROM emb WHERE model=?",
                                      (EMB_MODEL,)):
            if did in todo:
                v = array("f")
                v.frombytes(blob)
                out[did] = list(v)
        return out
    finally:
        conn.close()


async def embed_texts(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient() as client:
        return await _embed_batch(client, texts)


# ── geometria ─────────────────────────────────────────────────────────────────

def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def centroid(vecs: list[list[float]]) -> list[float]:
    dim = len(vecs[0])
    c = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
    return _norm(c)


def doubt_emb(vec: list[float], stance: str,
              c_up: list[float], c_down: list[float]) -> float | None:
    """cos do centroidu lęku przeciwnego − cos do centroidu lęku własnego kierunku."""
    direction = 1 if "bull" in stance else -1 if "bear" in stance else 0
    if direction == 0:
        return None
    v = _norm(vec)
    s_up, s_down = _dot(v, c_up), _dot(v, c_down)
    return (s_down - s_up) if direction > 0 else (s_up - s_down)


# ── analiza ───────────────────────────────────────────────────────────────────

def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = S.mean(a), S.mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    va = sum((x - ma) ** 2 for x in a) / n
    vb = sum((y - mb) ** 2 for y in b) / n
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else float("nan")


def _spearman(a: list[float], b: list[float]) -> float:
    from gielda_uncensored.reporting.xsec import _fractional_ranks
    return _pearson(_fractional_ranks(list(a)), _fractional_ranks(list(b)))


async def compute(settings: Settings, *, min_samples: int = 5, echo=print) -> str:
    rows = store.fetch_for_compare(settings.sqlite_path)
    embs = await embed_decisions(rows, echo=echo)
    anchor_vecs = await embed_texts(ANCHORS_FEAR_UP + ANCHORS_FEAR_DOWN)
    c_up = centroid(anchor_vecs[:len(ANCHORS_FEAR_UP)])
    c_down = centroid(anchor_vecs[len(ANCHORS_FEAR_UP):])

    # doubt per decyzja (emb + leksykon do korelacji) i mapa per komórka
    from collections import defaultdict
    acc: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    d_emb_all, d_lex_all = [], []
    for r in rows:
        v = embs.get(r["id"])
        if v is None:
            continue
        dj = json.loads(r["decision_json"]) if r.get("decision_json") else {}
        de = doubt_emb(v, r["stance"], c_up, c_down)
        if de is None:
            continue
        acc[(r["ticker"], r["as_of_date"], r["model_key"])].append(de)
        dl = C.doubt_score(r["stance"], dj.get("confession"))
        if dl is not None:
            d_emb_all.append(de)
            d_lex_all.append(dl)
    emb_map = {k: S.mean(v) for k, v in acc.items()}

    cells = build_panel(settings, min_samples=min_samples)
    L = ["SEMANTYCZNY DOUBT NA EMBEDDINGACH (qwen3-embedding:0.6b @ haxor)",
         f"decyzji z embeddingiem: {len(d_emb_all)}   kotwice: {len(ANCHORS_FEAR_UP)}+{len(ANCHORS_FEAR_DOWN)} lustrzane pary",
         "",
         f"Walidacja vs leksykon (per decyzja): Pearson r={_pearson(d_emb_all, d_lex_all):+.3f}  "
         f"Spearman ρ={_spearman(d_emb_all, d_lex_all):+.3f}",
         "",
         f"{'model':<11} {'wariant':<11} {'ret/tydz':>9} {'total':>7} {'Sharpe':>6}"]
    for label, model in MODELS.items():
        pts_low = P.backtest(cells, C.strat_fade(model, keep="low", doubt=emb_map))
        pts_high = P.backtest(cells, C.strat_fade(model, keep="high", doubt=emb_map))
        for variant, pts in (("LOW-doubt", pts_low), ("HIGH-doubt", pts_high)):
            m = P.metrics(variant, pts)
            L.append(f"{label:<11} {variant:<11} {S.mean([p.ret for p in pts])*100:>+8.2f}% "
                     f"{m.total_return*100:>+6.1f}% {m.sharpe:>6.2f}")
        d = [lo.ret - hi.ret for lo, hi in zip(pts_low, pts_high)]
        lo_ci, _, hi_ci = S.block_bootstrap_ci(d, S.mean)
        k = max(1, round(len(d) * 0.7))
        L.append(f"{label:<11} LOW−HIGH = {S.mean(d)*100:+.2f}%/t  CI95% [{lo_ci*100:+.2f}, {hi_ci*100:+.2f}]  "
                 f"IS {S.mean(d[:k])*100:+.2f} / OOS {S.mean(d[k:])*100:+.2f}")
    L += ["",
          "Czytanie: jeśli LOW−HIGH na osi semantycznej ~zgadza się z leksykonem (znak, rząd",
          "wielkości) → efekt nie jest artefaktem listy słów, tylko własnością treści confession."]
    return "\n".join(L)
