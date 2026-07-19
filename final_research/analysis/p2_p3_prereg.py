"""P2 i P3 z PREREGISTRATION-v2.md — rozliczenie endpointów pierwotnych.

P2 (prereg): masa ogona confidence ≤ 0,45; hipoteza CENS > ABL.
   Raportujemy P2 DOKŁADNIE jak prerejestrowany (próg 0,45) + eksploracyjnie
   progi 0,50/0,55/0,60 (0,60 = próg używany w gemma_results.md — dryf progu
   ujawniamy jawnie w paperze, sekcja Deviations).

P3 (prereg): doubt-score leksykalny na `confession` (tokeny/100 słów);
   hipoteza CENS > ABL. Leksykon NIE został zamrożony przed biegiem
   (odstępstwo od prereg) → liczymy jako EKSPLORACYJNY, z tabelą wrażliwości
   na wariant leksykonu (CORE / CONCESSIVE / FULL / MIN z doubt_lexicon_v2.txt).

Bootstrap CI 95% po tygodniach (cluster = tydzień, N=5000), jak w
capability_bootstrap.py.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import zlib
from pathlib import Path

DB = Path(os.environ.get("RESEARCH_DB")
          or Path(__file__).resolve().parents[1] / "data" / "final.db")
LEX = Path(__file__).resolve().parent / "doubt_lexicon_v2.txt"
BOOT_N = 5000
SEED = 20260706


def fam(mk: str) -> str:
    return "Qwen" if "qwen" in mk else "Gemma"


def arm(mk: str) -> str:
    return "abl" if "abl" in mk else "base"


def load_lexicon():
    variants: dict[str, list[str]] = {}
    for line in LEX.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        var, prefix = line.split("\t")
        variants.setdefault(var, []).append(prefix)
    variants["FULL"] = variants["CORE"] + variants["CONCESSIVE"]
    return {v: re.compile(r"\b(" + "|".join(map(re.escape, ps)) + r")\w*",
                          re.IGNORECASE)
            for v, ps in variants.items()}


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    from _common import _KEYS_SQL  # wyklucza skażoną kohortę final-gemma4-abl
    rows = con.execute("""
        SELECT model_key, ticker, as_of_date, confidence, decision_json
        FROM decisions WHERE """ + _KEYS_SQL + """
    """).fetchall()
    con.close()
    lex = load_lexicon()
    out = []
    for r in rows:
        d = json.loads(r["decision_json"])
        conf_txt = d.get("confession") or ""
        n_words = max(len(conf_txt.split()), 1)
        row = {
            "fam": fam(r["model_key"]), "arm": arm(r["model_key"]),
            "week": r["as_of_date"], "conf": float(r["confidence"]),
        }
        for thr in (0.45, 0.50, 0.55, 0.60):
            row[f"tail{int(thr*100)}"] = 1.0 if row["conf"] <= thr else 0.0
        for var, rx in lex.items():
            row[f"doubt_{var}"] = 100.0 * len(rx.findall(conf_txt)) / n_words
        out.append(row)
    return out


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def _rng(seed):
    s = seed
    while True:
        s = (1103515245 * s + 12345) & 0x7FFFFFFF
        yield s / 0x7FFFFFFF


def boot_delta(data, fm, metric):
    sub = [r for r in data if r["fam"] == fm]
    weeks = sorted({r["week"] for r in sub})
    by_week = {w: [r for r in sub if r["week"] == w] for w in weeks}

    def delta_for(wl):
        a = [r[metric] for w in wl for r in by_week[w] if r["arm"] == "abl"]
        b = [r[metric] for w in wl for r in by_week[w] if r["arm"] == "base"]
        return mean(a) - mean(b)

    point = delta_for(weeks)
    gen = _rng(SEED + zlib.crc32(repr((fm, metric)).encode()) % 100000)
    nW = len(weeks)
    deltas = sorted(delta_for([weeks[int(next(gen) * nW) % nW]
                               for _ in range(nW)]) for _ in range(BOOT_N))
    return point, deltas[int(0.025 * BOOT_N)], deltas[int(0.975 * BOOT_N)]


def p2(data):
    print("\n=== P2 (prereg: ogon confidence ≤0,45; hipoteza base>abl) "
          "+ progi eksploracyjne ===")
    print(f"{'rodzina':7} {'próg':>5} {'base':>7} {'abl':>7} "
          f"{'Δ(abl−base)':>12} {'CI95%':>22} {'0 w CI?':>8}")
    for fm in ("Gemma", "Qwen"):
        for thr in (45, 50, 55, 60):
            m = f"tail{thr}"
            b = mean([r[m] for r in data if r["fam"] == fm and r["arm"] == "base"])
            a = mean([r[m] for r in data if r["fam"] == fm and r["arm"] == "abl"])
            p, lo, hi = boot_delta(data, fm, m)
            zero = "TAK" if lo <= 0 <= hi else "nie"
            tag = " <- PREREG" if thr == 45 else (" <- w gemma_results" if thr == 60 else "")
            print(f"{fm:7} ≤0,{thr:02d} {100*b:>6.2f}% {100*a:>6.2f}% "
                  f"{100*p:>+11.2f}pp [{100*lo:>+7.2f},{100*hi:>+7.2f}] {zero:>8}{tag}")


def p3(data):
    print("\n=== P3 (doubt-score na confession, tokeny/100 słów; "
          "hipoteza prereg: base>abl) — EKSPLORACYJNY (leksykon post-hoc) ===")
    print(f"{'rodzina':7} {'wariant':11} {'base':>7} {'abl':>7} "
          f"{'Δ(abl−base)':>12} {'CI95%':>20} {'0 w CI?':>8}")
    for fm in ("Gemma", "Qwen"):
        for var in ("CORE", "CONCESSIVE", "FULL", "MIN"):
            m = f"doubt_{var}"
            b = mean([r[m] for r in data if r["fam"] == fm and r["arm"] == "base"])
            a = mean([r[m] for r in data if r["fam"] == fm and r["arm"] == "abl"])
            p, lo, hi = boot_delta(data, fm, m)
            zero = "TAK" if lo <= 0 <= hi else "nie"
            print(f"{fm:7} {var:11} {b:>7.2f} {a:>7.2f} {p:>+12.2f} "
                  f"[{lo:>+8.2f},{hi:>+8.2f}] {zero:>8}")


if __name__ == "__main__":
    data = load()
    print(f"wczytano {len(data)} decyzji z {DB.name}; leksykon: {LEX.name}")
    p2(data)
    p3(data)
