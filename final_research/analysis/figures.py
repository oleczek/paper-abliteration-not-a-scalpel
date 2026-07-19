"""Generuje figures/*.svg do paper/ z final.db (read-only, czysty SVG, zero-dep).
Paleta walidowana (dataviz): Gemma=niebieski #2a78d6, Qwen=aqua #1baf7a.
Każda seria etykietowana wprost (relief rule: aqua <3:1). Light surface.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from _common import (FAM_COLOR, PROMPT_HASH, load_decisions, load_pnl, mean, seg)
from capability_bootstrap import load as load_cap, boot_delta

OUT = Path(__file__).resolve().parents[1] / "paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

SURF, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
GEM, QWN = FAM_COLOR["Gemma"], FAM_COLOR["Qwen"]
FONT = 'font-family="system-ui,-apple-system,Segoe UI,sans-serif"'


def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;")
def T(x, y, s, sz=12, col=INK, anc="start", w=400):
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{sz}" fill="{col}" text-anchor="{anc}" font-weight="{w}" {FONT}>{esc(s)}</text>'
def L(x1, y1, x2, y2, col=GRID, w=1, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{col}" stroke-width="{w}"{d}/>'
def R(x, y, w, h, col, rx=0):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(0,w):.1f}" height="{max(0,h):.1f}" fill="{col}" rx="{rx}"/>'
def C(x, y, r, col, stroke=SURF, sw=2):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{col}" stroke="{stroke}" stroke-width="{sw}"/>'


def frame(w, h, title, sub, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'{R(0,0,w,h,SURF)}'
            f'{T(24,34,title,16,INK,"start",600)}'
            f'{T(24,54,sub,12,INK2)}'
            f'{body}'
            f'{T(24,h-12,f"final.db · 21,600 decyzji · prompt {PROMPT_HASH[:8]} · Fafuła, funduszai.pl",10,MUTED)}'
            f'</svg>')


# ---------- FIG 1: forest plot delt z CI ----------
def fig1():
    cap = load_cap()
    metrics = [("bull", "Bull-rate (pp)", 100), ("conf", "Mean confidence", 1),
               ("conf_words", "Confession length (words)", 1), ("h1w", "Horizon = 1w (pp)", 100)]
    W, H = 1080, 470
    x0, x1 = 300, 760
    panels = []
    py = 90
    ph = 88
    for key, label, sc in metrics:
        vals = {}
        for fm in ("Gemma", "Qwen"):
            p, lo, hi = boot_delta(cap, fm, key)
            vals[fm] = (p*sc, lo*sc, hi*sc)
        lo_all = min(v[1] for v in vals.values()); hi_all = max(v[2] for v in vals.values())
        span = max(hi_all, 0) - min(lo_all, 0) or 1
        pad = span*0.18
        vmin, vmax = min(lo_all, 0)-pad, max(hi_all, 0)+pad
        def sx(v): return x0 + (v-vmin)/(vmax-vmin)*(x1-x0)
        s = [T(24, py+4, label, 13, INK, "start", 600)]
        # zero line
        s.append(L(sx(0), py-8, sx(0), py+ph-24, AXIS, 1, "3 3"))
        s.append(T(sx(0), py+ph-10, "0", 10, MUTED, "middle"))
        for i, fm in enumerate(("Gemma", "Qwen")):
            p, lo, hi = vals[fm]
            yy = py+18 + i*30
            col = FAM_COLOR[fm]
            s.append(L(sx(lo), yy, sx(hi), yy, col, 2))       # CI whisker
            s.append(L(sx(lo), yy-4, sx(lo), yy+4, col, 2))
            s.append(L(sx(hi), yy-4, sx(hi), yy+4, col, 2))
            s.append(C(sx(p), yy, 5, col))                     # point
            s.append(T(24, yy+4, fm, 12, col, "start", 600))
            unit = "pp" if sc == 100 else ""
            s.append(T(x1+2, yy+4, f"{p:+.2f}{unit} [{lo:+.2f},{hi:+.2f}]", 10, INK2, "start"))
        panels.append("".join(s))
        py += ph
    body = "".join(panels)
    (OUT/"fig1_forest_deltas.svg").write_text(frame(W, H,
        "Abliteration deltas (abl − base) with 95% bootstrap CIs",
        "Cluster bootstrap over 18 weeks. Bull-tilt & verbosity same sign both families; confidence reverses sign (non-overlapping).",
        body))
    print("  fig1_forest_deltas.svg")


# ---------- FIG 2: rozkłady confidence base vs abl per rodzina ----------
def fig2():
    d = load_decisions()
    edges = [0.40 + 0.05*i for i in range(11)]  # 0.40..0.90, 10 koszyków
    def binof(c):
        for i in range(10):
            if edges[i] <= c < edges[i+1]: return i
        return 9
    W, H = 900, 380
    pw = 380; px = [50, 490]; py, ph = 96, 200
    body = []
    for pi, fm in enumerate(("Gemma", "Qwen")):
        ox = px[pi]; col = FAM_COLOR[fm]
        dist = {}
        for am in ("base", "abl"):
            g = [r["conf"] for r in d if r["fam"] == fm and r["arm"] == am]
            h = [0]*10
            for c in g: h[binof(c)] += 1
            dist[am] = [100*x/len(g) for x in h]
        mx = max(max(dist["base"]), max(dist["abl"])) or 1
        body.append(T(ox, py-16, fm, 14, col, "start", 600))
        bw = pw/10
        for i in range(10):
            bx = ox + i*bw
            hb = dist["base"][i]/mx*ph; ha = dist["abl"][i]/mx*ph
            body.append(R(bx+bw*0.12, py+ph-hb, bw*0.36, hb, AXIS, 1))       # base = szary
            body.append(R(bx+bw*0.52, py+ph-ha, bw*0.36, ha, col, 1))        # abl = kolor rodziny
            if i % 2 == 0:
                body.append(T(bx+bw*0.5, py+ph+14, f"{edges[i]:.2f}", 9, MUTED, "middle"))
        body.append(L(ox, py+ph, ox+pw, py+ph, AXIS, 1))
        body.append(T(ox+pw/2, py+ph+30, "confidence", 10, MUTED, "middle"))
    ly = H-30
    body.append(R(50, ly-9, 12, 12, AXIS, 1)); body.append(T(68, ly+1, "base", 11, INK2))
    body.append(R(130, ly-9, 12, 12, INK2, 1)); body.append(T(148, ly+1, "abliterated (family hue) · % of decisions per 0.05 bin", 11, INK2))
    (OUT/"fig2_confidence_hist.svg").write_text(frame(W, H,
        "Confidence distribution: base vs abliterated",
        "Gemma abl grows a low-confidence tail; Qwen stays saturated high (no tail) — the confidence effect reverses sign.",
        "".join(body)))
    print("  fig2_confidence_hist.svg")


# ---------- FIG 3: bull-rate per tydzień, base vs abl ----------
def fig3():
    d = load_decisions(); pnl = load_pnl()
    weeks = sorted({r["week"] for r in d})
    bench = {}
    for w_ in weeks:
        bs = [p["bench"] for p in pnl if p["week"] == w_]
        bench[w_] = mean(bs) if bs else None
    W, H = 860, 430
    px, pw = 300, 520; ptop = 88; ph = 130
    body = []
    for pi, fm in enumerate(("Gemma", "Qwen")):
        oy = ptop + pi*(ph+40); col = FAM_COLOR[fm]
        body.append(T(24, oy+4, fm, 14, col, "start", 600))
        # reżim tło
        for i, w_ in enumerate(weeks):
            if bench.get(w_) is None: continue
            bx = px + i*(pw/(len(weeks)-1))
            tint = "#eef4ee" if bench[w_] > 0 else "#f7eeee"
            body.append(R(bx-(pw/(len(weeks)-1))/2, oy, pw/(len(weeks)-1), ph, tint))
        body.append(L(px, oy+ph, px+pw, oy+ph, AXIS, 1))
        body.append(L(px, oy, px, oy+ph, AXIS, 1))
        for val in (0, 50, 100):
            yy = oy+ph-val/100*ph
            body.append(L(px, yy, px+pw, yy, GRID, 1))
            body.append(T(px-6, yy+3, f"{val}", 9, MUTED, "end"))
        for am, dash in (("base", "4 3"), ("abl", "")):
            pts = []
            for i, w_ in enumerate(weeks):
                g = [r["bull"] for r in d if r["fam"] == fm and r["arm"] == am and r["week"] == w_]
                bx = px + i*(pw/(len(weeks)-1)); by = oy+ph-100*mean(g)/100*ph
                pts.append((bx, by))
            path = " ".join(f"{'M' if j==0 else 'L'}{x:.1f} {y:.1f}" for j,(x,y) in enumerate(pts))
            opac = "1" if am == "abl" else "0.55"
            body.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="2" stroke-opacity="{opac}"'
                        + (f' stroke-dasharray="{dash}"' if dash else "") + '/>')
        body.append(T(px+pw+8, oy+14, "abl —", 11, col, "start", 600))
        body.append(T(px+pw+8, oy+30, "base --", 11, col, "start"))
    body.append(T(px, 76, "18 weeks (2026-03-07 … 07-04) · green band = up-week, red band = down-week", 10, MUTED, "start"))
    (OUT/"fig3_bullrate_weeks.svg").write_text(frame(W, H,
        "Bull-rate per week: base vs abliterated",
        "Abliterated is more bullish, amplified in up-weeks; both floor in the bearish early weeks.",
        "".join(body)))
    print("  fig3_bullrate_weeks.svg")


# ---------- FIG 4: rozkład horyzontu (stacked) ----------
def fig4():
    d = load_decisions()
    ramp = {"1w": "#256abf", "2w": "#6da7ec", "4w": "#cde2fb"}
    W, H = 700, 360; px, py, ph = 60, 90, 190; bw = 90; gap = 60
    body = []
    cohorts = [("Gemma", "base"), ("Gemma", "abl"), ("Qwen", "base"), ("Qwen", "abl")]
    for i, (fm, am) in enumerate(cohorts):
        g = [r["horizon"] for r in d if r["fam"] == fm and r["arm"] == am]
        bx = px + i*(bw+gap); yy = py
        for h_ in ("1w", "2w", "4w"):
            frac = sum(1 for x in g if x == h_)/len(g)
            hh = frac*ph
            if hh > 0.5:
                body.append(R(bx, yy+2, bw, hh-2, ramp[h_], 1))  # 2px spacer
                if frac > 0.06:
                    body.append(T(bx+bw/2, yy+hh/2+4, f"{h_} {100*frac:.0f}%", 10, "#ffffff" if h_=="1w" else INK, "middle", 600))
            yy += hh
        body.append(T(bx+bw/2, py+ph+16, fm, 11, FAM_COLOR[fm], "middle", 600))
        body.append(T(bx+bw/2, py+ph+30, am, 11, INK2, "middle"))
    (OUT/"fig4_horizon_bars.svg").write_text(frame(W, H,
        "Time-horizon distribution per cohort",
        "Gemma collapses to 1w in both arms; Qwen-abliterated opens the horizon (base ~95% 1w vs abl ~55%).",
        "".join(body)))
    print("  fig4_horizon_bars.svg")


# ---------- FIG 5: regime split PnL ----------
def fig5():
    pnl = load_pnl()
    # reżim tygodnia per segment
    wk = defaultdict(list)
    for p in pnl: wk[(p["seg"], p["week"])].append(p["bench"])
    regime = {k: ("UP" if mean(v) > 0 else "DOWN") for k, v in wk.items()}
    W, H = 860, 380; body = []
    groups = [("Gemma", "WIG20"), ("Gemma", "mWIG40"), ("Qwen", "WIG20"), ("Qwen", "mWIG40")]
    gx = 80; gw = 170; py, ph = 110, 150; mid = py+ph/2
    maxv = 1.0
    for i, (fm, sg) in enumerate(groups):
        ox = gx + i*(gw+20)
        body.append(T(ox+gw/2, py-22, fm, 12, FAM_COLOR[fm], "middle", 600))
        body.append(T(ox+gw/2, py-8, sg, 11, INK2, "middle"))
        for j, reg in enumerate(("UP", "DOWN")):
            for k, am in enumerate(("base", "abl")):
                vs = [p["signed"]*100 for p in pnl if p["fam"] == fm and p["seg"] == sg
                      and p["arm"] == am and regime.get((sg, p["week"])) == reg]
                v = mean(vs) if vs else 0
                bx = ox + j*(gw/2) + k*20 + 12
                hh = abs(v)/maxv*(ph/2)
                col = FAM_COLOR[fm] if am == "abl" else AXIS
                yy = mid-hh if v >= 0 else mid
                body.append(R(bx, yy, 16, hh, col, 1))
            body.append(T(ox + j*(gw/2)+gw/4, py+ph+14, reg, 9, MUTED, "middle"))
    body.append(L(gx-10, mid, gx+4*(gw+20), mid, AXIS, 1))
    body.append(T(gx-16, mid+3, "0", 9, MUTED, "end"))
    body.append(R(gx, H-32, 12, 12, AXIS, 1)); body.append(T(gx+18, H-22, "base", 11, INK2))
    body.append(R(gx+90, H-32, 12, 12, INK2, 1)); body.append(T(gx+108, H-22, "abl (family hue) · signed return %/dec", 11, INK2))
    (OUT/"fig5_regime_pnl.svg").write_text(frame(W, H,
        "PnL by regime — the edge is beta, not skill",
        "Gemma-abl advantage flips sign with regime (wins up, loses down). Qwen abl≈base (base already regime-follower).",
        "".join(body)))
    print("  fig5_regime_pnl.svg")


if __name__ == "__main__":
    print("figury →")
    fig1(); fig2(); fig3(); fig4(); fig5()
