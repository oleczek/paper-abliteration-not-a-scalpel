"""Generuje fragmenty .md do paper/tables/ z final.db (read-only, deterministyczne)."""
from __future__ import annotations

from pathlib import Path

from _common import PROMPT_HASH, WIG20, load_decisions, load_pnl, mean

OUT = Path(__file__).resolve().parents[1] / "paper" / "tables"
OUT.mkdir(parents=True, exist_ok=True)
HDR = f"<!-- auto-gen z final.db · prompt-hash {PROMPT_HASH[:12]} · NIE edytować ręcznie -->\n\n"


def w(name, txt):
    (OUT / name).write_text(HDR + txt)
    print(f"  {name}")


def tab1_disposition(d):
    rows = []
    for fm in ("Gemma", "Qwen"):
        for sg in ("WIG20", "mWIG40"):
            for am in ("base", "abl"):
                g = [r for r in d if r["fam"] == fm and r["seg"] == sg and r["arm"] == am]
                rows.append((fm, sg, am, g))
    t = "| rodzina | segment | ramię | bull% | śr conf | ogon≤0,60 | horyzont 1w | confession (sł) | n |\n"
    t += "|---|---|---|---|---|---|---|---|---|\n"
    for fm, sg, am, g in rows:
        t += (f"| {fm} | {sg} | {am} | {100*mean([r['bull'] for r in g]):.1f} "
              f"| {mean([r['conf'] for r in g]):.3f} "
              f"| {100*mean([1.0 if r['conf']<=0.60 else 0.0 for r in g]):.0f}% "
              f"| {100*mean([r['h1w'] for r in g]):.0f}% "
              f"| {mean([r['conf_words'] for r in g]):.1f} | {len(g)} |\n")
    w("tab1_disposition.md", t)


def tab3_capability(d):
    import json, sqlite3
    from _common import DB, fam, arm, _KEYS_SQL  # whitelist: skażony final-gemma4-abl OUT
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    recs = []
    for r in con.execute(f"SELECT model_key, decision_json FROM decisions WHERE {_KEYS_SQL}"):
        dj = json.loads(r["decision_json"])
        rev = dj.get("reversal_trigger") or ""
        risks = dj.get("key_risks") or []
        recs.append((fam(r["model_key"]), arm(r["model_key"]), len(risks),
                     1.0 if 2 <= len(risks) <= 4 else 0.0,
                     1.0 if any(c.isdigit() for c in rev) else 0.0))
    con.close()
    t = "| rodzina | ramię | JSON | #ryzyk | ryzyk∈2-4 | rev. z liczbą |\n|---|---|---|---|---|---|\n"
    for fm in ("Gemma", "Qwen"):
        for am in ("base", "abl"):
            g = [x for x in recs if x[0] == fm and x[1] == am]
            t += (f"| {fm} | {am} | 100% | {mean([x[2] for x in g]):.2f} "
                  f"| {100*mean([x[3] for x in g]):.0f}% | {100*mean([x[4] for x in g]):.0f}% |\n")
    w("tab3_capability.md", t)


def tab4_perticker(d):
    t = "| rodzina | spółka | bull base→abl | Δ |\n|---|---|---|---|\n"
    for fm in ("Gemma", "Qwen"):
        shifts = []
        tickers = sorted({r["ticker"] for r in d})
        for tk in tickers:
            b = mean([r["bull"] for r in d if r["fam"] == fm and r["ticker"] == tk and r["arm"] == "base"])
            a = mean([r["bull"] for r in d if r["fam"] == fm and r["ticker"] == tk and r["arm"] == "abl"])
            shifts.append((tk, 100*b, 100*a, 100*(a-b)))
        shifts.sort(key=lambda x: -abs(x[3]))
        for tk, b, a, dd in shifts[:5]:
            t += f"| {fm} | {tk} | {b:.0f}→{a:.0f}% | {dd:+.0f}pp |\n"
    t += "\n*Top-5 spółek wg |Δ bull-rate| per rodzina. LEAD, nie wniosek (wielokrotne porównania → OOS/FDR).*\n"
    w("tab4_perticker.md", t)


if __name__ == "__main__":
    d = load_decisions()
    print(f"tabele z {len(d)} decyzji →")
    tab1_disposition(d)
    tab3_capability(d)
    tab4_perticker(d)
    print("(tab2_bootstrap generuje capability_bootstrap.py — już w gemma_results.md)")
