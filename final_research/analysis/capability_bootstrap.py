"""Kowariaty capability (compliance vs dyspozycja) + bootstrap CI po tygodniach.

Czysto odczytowe z final.db. Dwie rzeczy:
1. KOWARIATY CAPABILITY per (rodzina, ramię): instruction-following mierzone
   z persistowanego outputu — żeby wykluczyć konfund „abliteracja degraduje
   compliance, nie dyspozycję". Jeśli capability podobne między ramionami,
   delty dyspozycji są czyste.
2. BOOTSTRAP CI dla kluczowych delt (abl−base) — resampling po TYGODNIACH
   (jednostka niezależności = tydzień, nie decyzja; 18 sobót), cluster bootstrap.
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
WIG20 = {"PKN","PKO","PEO","PZU","PGE","KGH","CDR","ALE","DNP","LPP",
         "MBK","EBP","KRU","KTY","PCO","ALR","MDV","BDX","TPE","ZAB"}

# Leksykon ogólników (zakazany w confession przez prompt v2) — proxy łamania instrukcji.
GENERIC = re.compile(r"\b(rynek bywa|rynki bywaj|zmienn|nieprzewidywaln|trudno przewidzie|"
                     r"zawsze istnieje ryzyko|może się zdarzyć|nic nie jest pewne|"
                     r"przyszłość jest niepewna)\b", re.IGNORECASE)
HAS_NUM = re.compile(r"\d")  # konkretny poziom/liczba w reversal_trigger = specyficzność

BOOT_N = 5000
SEED = 20260706  # deterministyczny LCG (Math.random niedostępny nie dotyczy — to python)


def fam(mk: str) -> str:
    return "Qwen" if "qwen" in mk else "Gemma"


def arm(mk: str) -> str:
    return "abl" if "abl" in mk else "base"


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    from _common import _KEYS_SQL  # wyklucza skażoną kohortę final-gemma4-abl
    rows = con.execute("""
        SELECT model_key, ticker, as_of_date, stance, confidence, time_horizon,
               decision_json FROM decisions WHERE """ + _KEYS_SQL + """
    """).fetchall()
    con.close()
    out = []
    for r in rows:
        d = json.loads(r["decision_json"])
        conf = d.get("confession") or ""
        rev = d.get("reversal_trigger") or ""
        risks = d.get("key_risks") or []
        out.append({
            "fam": fam(r["model_key"]), "arm": arm(r["model_key"]),
            "week": r["as_of_date"], "ticker": r["ticker"],
            "bull": 1.0 if r["stance"] == "bull" else 0.0,
            "conf": float(r["confidence"]),
            "h1w": 1.0 if r["time_horizon"] == "1w" else 0.0,
            "conf_words": len(conf.split()),
            "n_risks": len(risks),
            "risks_in_range": 1.0 if 2 <= len(risks) <= 4 else 0.0,
            "rev_specific": 1.0 if HAS_NUM.search(rev) else 0.0,
            "conf_generic": 1.0 if GENERIC.search(conf) else 0.0,
        })
    return out


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def capability(data):
    print("\n=== KOWARIATY CAPABILITY (instruction-following) per rodzina×ramię ===")
    print(f"{'rodzina':7} {'ramię':5} {'n':>6} {'JSON-ok':>7} {'#ryzyk':>7} "
          f"{'ryzyk∈2-4':>9} {'rev.spec':>9} {'confess.ogólnik':>15} {'confess.słów':>12}")
    for fm in ("Gemma", "Qwen"):
        for am in ("base", "abl"):
            g = [r for r in data if r["fam"] == fm and r["arm"] == am]
            # JSON-ok = 100% z konstrukcji (parser odrzucał niepoprawne przed zapisem)
            print(f"{fm:7} {am:5} {len(g):>6} {'100%':>7} "
                  f"{mean([r['n_risks'] for r in g]):>7.2f} "
                  f"{100*mean([r['risks_in_range'] for r in g]):>8.0f}% "
                  f"{100*mean([r['rev_specific'] for r in g]):>8.0f}% "
                  f"{100*mean([r['conf_generic'] for r in g]):>14.1f}% "
                  f"{mean([r['conf_words'] for r in g]):>12.1f}")


def _rng(seed):
    s = seed
    while True:
        s = (1103515245 * s + 12345) & 0x7FFFFFFF
        yield s / 0x7FFFFFFF


def boot_delta(data, fm, metric, seg=None):
    """Bootstrap CI delty (abl−base) metryki, resampling po TYGODNIACH."""
    sub = [r for r in data if r["fam"] == fm and
           (seg is None or (seg == "WIG20") == (r["ticker"] in WIG20))]
    weeks = sorted({r["week"] for r in sub})
    by_week = {w: [r for r in sub if r["week"] == w] for w in weeks}

    def delta_for(week_list):
        a = [r[metric] for w in week_list for r in by_week[w] if r["arm"] == "abl"]
        b = [r[metric] for w in week_list for r in by_week[w] if r["arm"] == "base"]
        return mean(a) - mean(b)

    point = delta_for(weeks)
    gen = _rng(SEED + zlib.crc32(repr((fm, metric, seg or "all")).encode()) % 100000)
    deltas = []
    nW = len(weeks)
    for _ in range(BOOT_N):
        samp = [weeks[int(next(gen) * nW) % nW] for _ in range(nW)]
        deltas.append(delta_for(samp))
    deltas.sort()
    lo = deltas[int(0.025 * BOOT_N)]
    hi = deltas[int(0.975 * BOOT_N)]
    return point, lo, hi


def bootstrap(data):
    print(f"\n=== BOOTSTRAP CI 95% delt (abl−base), resampling po {18} tygodniach, "
          f"N={BOOT_N} ===")
    metrics = [("bull", "bull-rate", 100), ("conf", "śr.confidence", 1),
               ("conf_words", "confession-słów", 1), ("h1w", "horyzont-1w", 100)]
    print(f"{'rodzina':7} {'metryka':16} {'delta':>10} {'CI95%':>22} {'0 w CI?':>8}")
    for fm in ("Gemma", "Qwen"):
        for key, label, sc in metrics:
            p, lo, hi = boot_delta(data, fm, key)
            zero = "TAK" if lo <= 0 <= hi else "nie"
            print(f"{fm:7} {label:16} {p*sc:>+10.3f} "
                  f"[{lo*sc:>+8.3f},{hi*sc:>+8.3f}] {zero:>8}")


if __name__ == "__main__":
    data = load()
    print(f"wczytano {len(data)} decyzji z {DB.name}")
    capability(data)
    bootstrap(data)
