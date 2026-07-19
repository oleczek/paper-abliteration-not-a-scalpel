"""Prymitywy wykresów w czystym SVG — zero zależności, zero CDN, wszystko inline w HTML.

Każda funkcja zwraca string <svg>. Skala liniowa, oś Y auto z ładnym paddingiem. Kolory i
typografia spójne (paleta w THEME). Wykresy są responsywne (viewBox + width=100%).
"""
from __future__ import annotations

import html
import math
from dataclasses import dataclass

THEME = {
    "bg": "#0f1115",
    "grid": "#242833",
    "axis": "#565c6b",
    "text": "#c9d1d9",
    "muted": "#7d8590",
    "cens": "#e0574a",     # czerwony — censored
    "unc": "#4aa3e0",      # niebieski — uncensored
    "hybrid": "#c77dff",   # fiolet — hybrydy
    "bench": "#8b949e",    # szary — WIG20
    "good": "#3fb950",
    "bad": "#e0574a",
    "neutral": "#d29922",
}

_PALETTE = ["#4aa3e0", "#e0574a", "#c77dff", "#3fb950", "#d29922", "#56d4c4",
            "#f0883e", "#a5a5f5", "#db61a2", "#8b949e"]


def color_for(label: str, idx: int) -> str:
    l = label.upper()
    if l.startswith("CENSORED"):
        return THEME["cens"]
    if l.startswith("UNCENSORED"):
        return THEME["unc"]
    if l.startswith("HYBRID") or l.startswith("MEGA") or l.startswith("DISAGREE") or l.startswith("DEBIAS"):
        return THEME["hybrid"]
    if "WIG20" in l or "BENCH" in l or "ALWAYS-LONG" in l:
        return THEME["bench"]
    return _PALETTE[idx % len(_PALETTE)]


def _nice_bounds(lo: float, hi: float) -> tuple[float, float]:
    if lo == hi:
        return lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    return lo - pad, hi + pad


@dataclass
class Series:
    label: str
    ys: list[float]
    color: str
    dashed: bool = False
    width: float = 2.0


def line_chart(x_labels: list[str], series: list[Series], *, width: int = 860,
               height: int = 380, y_fmt: str = "pct", title: str = "",
               zero_line: bool = True) -> str:
    """Wykres liniowy wielu serii. x_labels — etykiety osi X (tygodnie); series — linie."""
    ml, mr, mt, mb = 56, 130, 30 if title else 16, 40
    pw, ph = width - ml - mr, height - mt - mb
    all_y = [y for s in series for y in s.ys if y is not None and not math.isnan(y)]
    if not all_y:
        return f'<svg viewBox="0 0 {width} {height}" width="100%"></svg>'
    lo, hi = _nice_bounds(min(all_y), max(all_y))
    n = max(len(x_labels), 1)

    def px(i: int) -> float:
        return ml + (pw * i / max(n - 1, 1))

    def py(v: float) -> float:
        return mt + ph * (1 - (v - lo) / (hi - lo))

    def fmt(v: float) -> str:
        if y_fmt == "pct":
            return f"{v*100:.0f}%"
        if y_fmt == "x":
            return f"{v:.2f}×"
        return f"{v:.2f}"

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" font-family="ui-monospace,monospace" font-size="11">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{THEME["bg"]}" rx="8"/>')
    if title:
        parts.append(f'<text x="{ml}" y="18" fill="{THEME["text"]}" font-size="13" font-weight="700">{html.escape(title)}</text>')
    # gridlines + y labels
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = py(v)
        parts.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+pw}" y2="{y:.1f}" stroke="{THEME["grid"]}" stroke-width="1"/>')
        parts.append(f'<text x="{ml-8}" y="{y+3:.1f}" fill="{THEME["muted"]}" text-anchor="end">{fmt(v)}</text>')
    if zero_line and lo < 0 < hi:
        y0 = py(0)
        parts.append(f'<line x1="{ml}" y1="{y0:.1f}" x2="{ml+pw}" y2="{y0:.1f}" stroke="{THEME["axis"]}" stroke-width="1.2" stroke-dasharray="2,2"/>')
    # x labels (rzadziej gdy dużo)
    step = max(1, n // 8)
    for i in range(0, n, step):
        x = px(i)
        lab = x_labels[i][5:] if len(x_labels[i]) > 5 else x_labels[i]  # bez roku
        parts.append(f'<text x="{x:.1f}" y="{height-mb+16}" fill="{THEME["muted"]}" text-anchor="middle">{html.escape(lab)}</text>')
    # serie
    for s in series:
        pts = []
        for i, v in enumerate(s.ys):
            if v is None or math.isnan(v):
                continue
            pts.append(f"{px(i):.1f},{py(v):.1f}")
        if not pts:
            continue
        dash = 'stroke-dasharray="5,4"' if s.dashed else ""
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{s.color}" stroke-width="{s.width}" {dash} stroke-linejoin="round"/>')
    # legenda
    ly = mt + 4
    for s in series:
        parts.append(f'<rect x="{ml+pw+14}" y="{ly-8}" width="10" height="10" fill="{s.color}" rx="2"/>')
        parts.append(f'<text x="{ml+pw+28}" y="{ly+1}" fill="{THEME["text"]}">{html.escape(s.label)}</text>')
        ly += 18
    parts.append("</svg>")
    return "".join(parts)


def bar_chart(labels: list[str], values: list[float], *, colors: list[str] | None = None,
              width: int = 860, height: int = 360, y_fmt: str = "pct", title: str = "") -> str:
    """Poziomy bar chart (etykiety po lewej). Dobre do rankingu strategii / spółek."""
    mt = 30 if title else 12
    ml, mr, mb = 150, 60, 16
    row_h = max(18, (height - mt - mb) // max(len(values), 1))
    height = mt + mb + row_h * len(values)
    pw = width - ml - mr
    lo = min(0.0, min(values) if values else 0.0)
    hi = max(0.0, max(values) if values else 0.0)
    lo, hi = _nice_bounds(lo, hi)
    span = hi - lo or 1.0

    def px(v: float) -> float:
        return ml + pw * (v - lo) / span

    def fmt(v: float) -> str:
        return f"{v*100:+.1f}%" if y_fmt == "pct" else f"{v:+.2f}"

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" font-family="ui-monospace,monospace" font-size="11">']
    parts.append(f'<rect width="{width}" height="{height}" fill="{THEME["bg"]}" rx="8"/>')
    if title:
        parts.append(f'<text x="{ml}" y="18" fill="{THEME["text"]}" font-size="13" font-weight="700">{html.escape(title)}</text>')
    x0 = px(0)
    parts.append(f'<line x1="{x0:.1f}" y1="{mt}" x2="{x0:.1f}" y2="{height-mb}" stroke="{THEME["axis"]}" stroke-width="1"/>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = mt + i * row_h
        col = (colors[i] if colors else None) or (THEME["good"] if v >= 0 else THEME["bad"])
        x1 = px(v)
        bx = min(x0, x1)
        bw = abs(x1 - x0)
        parts.append(f'<rect x="{bx:.1f}" y="{y+3:.1f}" width="{bw:.1f}" height="{row_h-6}" fill="{col}" rx="2" opacity="0.85"/>')
        parts.append(f'<text x="{ml-8}" y="{y+row_h/2+3:.1f}" fill="{THEME["text"]}" text-anchor="end">{html.escape(lab)}</text>')
        tx = x1 + 4 if v >= 0 else x1 - 4
        anc = "start" if v >= 0 else "end"
        parts.append(f'<text x="{tx:.1f}" y="{y+row_h/2+3:.1f}" fill="{THEME["muted"]}" text-anchor="{anc}">{fmt(v)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def scatter(points: list[tuple[float, float, str, str]], *, width: int = 560, height: int = 440,
            x_label: str = "", y_label: str = "", title: str = "",
            x_ref: float | None = None, y_ref: float | None = None) -> str:
    """Punkty (x, y, etykieta, kolor). x_ref/y_ref rysują linie odniesienia (kwadranty)."""
    ml, mr, mt, mb = 56, 20, 30 if title else 14, 44
    pw, ph = width - ml - mr, height - mt - mb
    if not points:
        return f'<svg viewBox="0 0 {width} {height}" width="100%"></svg>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xlo, xhi = _nice_bounds(min(xs + ([x_ref] if x_ref is not None else [])),
                            max(xs + ([x_ref] if x_ref is not None else [])))
    ylo, yhi = _nice_bounds(min(ys + ([y_ref] if y_ref is not None else [])),
                            max(ys + ([y_ref] if y_ref is not None else [])))

    def px(v): return ml + pw * (v - xlo) / (xhi - xlo)
    def py(v): return mt + ph * (1 - (v - ylo) / (yhi - ylo))

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" font-family="ui-monospace,monospace" font-size="11">']
    parts.append(f'<rect width="{width}" height="{height}" fill="{THEME["bg"]}" rx="8"/>')
    if title:
        parts.append(f'<text x="{ml}" y="18" fill="{THEME["text"]}" font-size="13" font-weight="700">{html.escape(title)}</text>')
    # ref lines (kwadranty)
    if x_ref is not None:
        parts.append(f'<line x1="{px(x_ref):.1f}" y1="{mt}" x2="{px(x_ref):.1f}" y2="{mt+ph}" stroke="{THEME["axis"]}" stroke-dasharray="3,3"/>')
    if y_ref is not None:
        parts.append(f'<line x1="{ml}" y1="{py(y_ref):.1f}" x2="{ml+pw}" y2="{py(y_ref):.1f}" stroke="{THEME["axis"]}" stroke-dasharray="3,3"/>')
    for k in range(5):
        yv = ylo + (yhi - ylo) * k / 4
        parts.append(f'<text x="{ml-8}" y="{py(yv)+3:.1f}" fill="{THEME["muted"]}" text-anchor="end">{yv*100:.0f}%</text>')
        xv = xlo + (xhi - xlo) * k / 4
        parts.append(f'<text x="{px(xv):.1f}" y="{mt+ph+16}" fill="{THEME["muted"]}" text-anchor="middle">{xv:.2f}</text>')
    for x, y, lab, col in points:
        parts.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="5" fill="{col}" opacity="0.8"/>')
        if lab:
            parts.append(f'<text x="{px(x)+7:.1f}" y="{py(y)+3:.1f}" fill="{THEME["muted"]}" font-size="9">{html.escape(lab)}</text>')
    if x_label:
        parts.append(f'<text x="{ml+pw/2:.1f}" y="{height-6}" fill="{THEME["text"]}" text-anchor="middle">{html.escape(x_label)}</text>')
    if y_label:
        parts.append(f'<text x="14" y="{mt+ph/2:.1f}" fill="{THEME["text"]}" text-anchor="middle" transform="rotate(-90 14 {mt+ph/2:.1f})">{html.escape(y_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def heatmap(row_labels: list[str], col_labels: list[str], grid: list[list[float | None]],
            *, width: int = 860, title: str = "", vmax: float | None = None) -> str:
    """Heatmapa zwrotów (wiersze=spółki, kolumny=tygodnie). Zielony=+, czerwony=−."""
    mt = 30 if title else 12
    ml, mb = 70, 40
    cell = max(14, (width - ml - 12) // max(len(col_labels), 1))
    width = ml + 12 + cell * len(col_labels)
    height = mt + mb + cell * len(row_labels)
    flat = [abs(v) for row in grid for v in row if v is not None]
    vm = vmax or (max(flat) if flat else 0.05) or 0.05

    def col(v: float | None) -> str:
        if v is None:
            return THEME["grid"]
        t = max(-1, min(1, v / vm))
        if t >= 0:
            g = int(60 + 120 * t)
            return f"rgb(30,{g},50)"
        r = int(60 + 130 * (-t))
        return f"rgb({r},40,40)"

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" font-family="ui-monospace,monospace" font-size="10">']
    parts.append(f'<rect width="{width}" height="{height}" fill="{THEME["bg"]}" rx="8"/>')
    if title:
        parts.append(f'<text x="{ml}" y="18" fill="{THEME["text"]}" font-size="13" font-weight="700">{html.escape(title)}</text>')
    for r, rlab in enumerate(row_labels):
        y = mt + r * cell
        parts.append(f'<text x="{ml-6}" y="{y+cell/2+3:.1f}" fill="{THEME["muted"]}" text-anchor="end">{html.escape(rlab)}</text>')
        for cix in range(len(col_labels)):
            v = grid[r][cix] if cix < len(grid[r]) else None
            x = ml + cix * cell
            parts.append(f'<rect x="{x}" y="{y}" width="{cell-1}" height="{cell-1}" fill="{col(v)}" rx="1"/>')
    step = max(1, len(col_labels) // 10)
    for cix in range(0, len(col_labels), step):
        x = ml + cix * cell
        lab = col_labels[cix][5:]
        parts.append(f'<text x="{x+cell/2:.1f}" y="{height-mb+16}" fill="{THEME["muted"]}" text-anchor="middle" font-size="9">{html.escape(lab)}</text>')
    parts.append("</svg>")
    return "".join(parts)
