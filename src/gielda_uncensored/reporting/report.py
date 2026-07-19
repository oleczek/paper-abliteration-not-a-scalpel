"""Asembler raportu HTML — self-contained (inline SVG + CSS), zero zależności zewnętrznych.

Sekcje (v2, 2026-07-03):
  0. TL;DR z auto-werdyktem (skill vs beta rynku)
  1. Equity curves wszystkich strategii vs WIG20 (+3-way z deepseekiem w tabeli)
  2. Ranking strategii + tabela metryk
  3. „Skill czy beta?" — rękaw long vs short, debias
  4. Walk-forward OOS
  5. Regime-conditional split (up/down-weeks) + diff-in-diff z CI  ← confound-killer
  6. Cross-sectional rank (market-neutral) z CI                    ← null sygnału względnego
  7. Confession-fade (LOW vs HIGH doubt) + wrażliwość/replikacja   ← sygnał suggestive
  8. Deep-dive na sporach modeli (DISAGREE)
  9. Heatmapa zwrotów per spółka×tydzień
 10. Dyspozycja: bull-bias / flip / ogon confidence / collapse'y (3 modele)
 11. Zastrzeżenia (uczciwość metodologiczna)
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass

from gielda_uncensored.config import Settings
from gielda_uncensored.db import store
from gielda_uncensored.reporting import charts, portfolio as P
from gielda_uncensored.reporting import confession as CF
from gielda_uncensored.reporting import regime as RG
from gielda_uncensored.reporting import stats as ST
from gielda_uncensored.reporting import xsec as XS
from gielda_uncensored.reporting.panel import (BASELINE, CENS, UNC, Cell, build_panel,
                                               by_week, weeks_of)

T = charts.THEME


def _pct(v: float, sign: bool = True) -> str:
    return f"{v*100:+.1f}%" if sign else f"{v*100:.1f}%"


def _oos_split(cells: list[Cell], frac: float = 0.7) -> tuple[list[str], list[str]]:
    wks = weeks_of(cells)
    k = max(1, int(round(len(wks) * frac)))
    return wks[:k], wks[k:]


@dataclass
class StratRow:
    name: str
    m: P.Metrics
    turn: float


def _all_metrics(cells: list[Cell]) -> list[StratRow]:
    out = []
    for name, strat in P.default_strategies().items():
        pts = P.backtest(cells, strat)
        out.append(StratRow(name, P.metrics(name, pts), P.turnover(pts)))
    return out


def _metrics_table(rows: list[StratRow], bench_total: float) -> str:
    head = ("<tr><th>strategia</th><th>total</th><th>ann</th><th>Sharpe</th>"
            "<th>maxDD</th><th>win-wk</th><th>α vs WIG20</th><th>turnover</th><th>n L/S</th></tr>")
    body = []
    for r in sorted(rows, key=lambda x: x.m.total_return, reverse=True):
        m = r.m
        acol = T["good"] if m.alpha_total > 0 else T["bad"]
        scol = T["good"] if m.sharpe > 0 else T["bad"]
        body.append(
            f"<tr><td style='text-align:left'>{html.escape(m.name)}</td>"
            f"<td>{_pct(m.total_return)}</td><td>{_pct(m.ann_return)}</td>"
            f"<td style='color:{scol}'>{m.sharpe:.2f}</td>"
            f"<td>{_pct(m.max_drawdown)}</td><td>{m.win_weeks*100:.0f}%</td>"
            f"<td style='color:{acol};font-weight:700'>{_pct(m.alpha_total)}</td>"
            f"<td>{m.turnover*100:.0f}%</td>"
            f"<td>{m.avg_n_long:.1f}/{m.avg_n_short:.1f}</td></tr>"
        )
    return (f"<table class='mt'>{head}{''.join(body)}</table>"
            f"<p class='cap'>WIG20 buy&amp;hold w tym oknie: <b>{_pct(bench_total)}</b>. "
            f"α = total − WIG20. Wszystkie strategie znormalizowane do Σ|waga|=1 (brutto 100%).</p>")


def _equity_chart(cells: list[Cell]) -> str:
    wks = weeks_of(cells)
    xlabels = [""] + wks  # punkt startowy = 1.0 przed pierwszym tygodniem
    series = []
    pick = ["UNCENSORED cons", "CENSORED cons", "HYBRID agree", "MEGA-ensemble",
            "DISAGREE→unc", "ALWAYS-LONG (EW)"]
    strategies = P.default_strategies()
    bench_pts = None
    for i, name in enumerate(pick):
        pts = P.backtest(cells, strategies[name])
        bench_pts = pts
        ys = [0.0] + [p.equity - 1 for p in pts]
        series.append(charts.Series(name, ys, charts.color_for(name, i),
                                    width=2.4 if name.startswith(("UNC", "DISAGREE")) else 1.8))
    if bench_pts:
        ys = [0.0] + [p.bench_equity - 1 for p in bench_pts]
        series.append(charts.Series("WIG20 b&h", ys, T["bench"], dashed=True, width=2.0))
    return charts.line_chart(xlabels, series, title="Equity curve — zwrot skumulowany vs WIG20",
                             y_fmt="pct", height=420)


def _ranking_bar(rows: list[StratRow]) -> str:
    srt = sorted(rows, key=lambda x: x.m.alpha_total)
    labels = [r.m.name for r in srt]
    vals = [r.m.alpha_total for r in srt]
    cols = [charts.color_for(r.m.name, i) for i, r in enumerate(srt)]
    return charts.bar_chart(labels, vals, colors=cols, title="Alpha vs WIG20 wg strategii",
                            y_fmt="pct")


def _skill_vs_beta(cells: list[Cell]) -> str:
    cl, cs = P.sleeve_returns(cells, CENS)
    ul, us = P.sleeve_returns(cells, UNC)
    labels = ["CENS long-sleeve", "CENS short-sleeve", "UNC long-sleeve", "UNC short-sleeve"]
    vals = [cl, cs, ul, us]
    cols = [T["cens"], T["cens"], T["unc"], T["unc"]]
    bar = charts.bar_chart(labels, vals, colors=cols, height=200,
                           title="Rękaw LONG vs SHORT (skumulowany zwrot rękawa)")
    note = (
        "<p>Rękaw <b>long</b> zarabia w hossie u obu modeli — to <b>beta</b>, nie skill. "
        "Prawdziwy test kierunkowego skillu to rękaw <b>short</b>: czy shortowane spółki faktycznie "
        "spadały (dodatni zwrot rękawa short = trafne shorty). Ujemny rękaw short = model shortował "
        "spółki, które rosły — czyli szkodził.</p>")
    return bar + note


def _walkforward(cells: list[Cell]) -> str:
    in_wks, oos_wks = _oos_split(cells, 0.7)
    in_set, oos_set = set(in_wks), set(oos_wks)
    in_cells = [c for c in cells if c.week_monday in in_set]
    oos_cells = [c for c in cells if c.week_monday in oos_set]
    strategies = P.default_strategies()
    head = ("<tr><th>strategia</th><th>IN α</th><th>IN Sharpe</th>"
            "<th>OOS α</th><th>OOS Sharpe</th><th>trzyma?</th></tr>")
    body = []
    rank = []
    for name, strat in strategies.items():
        mi = P.metrics(name, P.backtest(in_cells, strat))
        mo = P.metrics(name, P.backtest(oos_cells, strat))
        rank.append((name, mi, mo))
    for name, mi, mo in sorted(rank, key=lambda x: x[1].alpha_total, reverse=True):
        holds = mi.alpha_total > 0 and mo.alpha_total > 0
        degrades = mi.alpha_total > 0 and mo.alpha_total <= 0
        tag = ("<span style='color:%s'>✔ trzyma</span>" % T["good"]) if holds else \
              ("<span style='color:%s'>✘ pada OOS</span>" % T["bad"]) if degrades else \
              "<span style='color:%s'>—</span>" % T["muted"]
        ic = T["good"] if mi.alpha_total > 0 else T["bad"]
        oc = T["good"] if mo.alpha_total > 0 else T["bad"]
        body.append(
            f"<tr><td style='text-align:left'>{html.escape(name)}</td>"
            f"<td style='color:{ic}'>{_pct(mi.alpha_total)}</td><td>{mi.sharpe:.2f}</td>"
            f"<td style='color:{oc};font-weight:700'>{_pct(mo.alpha_total)}</td><td>{mo.sharpe:.2f}</td>"
            f"<td>{tag}</td></tr>")
    cap = (f"<p class='cap'>IN-SAMPLE: {in_wks[0][5:]}–{in_wks[-1][5:]} ({len(in_wks)} tyg). "
           f"OUT-OF-SAMPLE: {oos_wks[0][5:]}–{oos_wks[-1][5:]} ({len(oos_wks)} tyg). "
           f"Na 16 tygodniach OOS to zaledwie sygnał ostrzegawczy o przeuczeniu, nie dowód.</p>")
    return f"<table class='mt'>{head}{''.join(body)}</table>{cap}"


def _disagree_section(cells: list[Cell]) -> str:
    n_dis = n_unc_right = 0
    unc_ret = cens_ret = 0.0
    for c in cells:
        a, b = c.sig(CENS), c.sig(UNC)
        if a is None or b is None or a.direction == 0 or b.direction == 0:
            continue
        if a.direction == b.direction:
            continue
        n_dis += 1
        unc_ret += b.direction * c.gross
        cens_ret += a.direction * c.gross
        if (b.direction * c.gross) > 0:
            n_unc_right += 1
    if n_dis == 0:
        return "<p>Brak komórek spornych.</p>"
    return (
        f"<div class='grid2'>"
        f"<div class='stat'><div class='big'>{n_dis}</div><div class='lbl'>komórek spornych "
        f"(z {sum(1 for c in cells if c.sig(CENS) and c.sig(UNC))} wspólnych)</div></div>"
        f"<div class='stat'><div class='big' style='color:{T['unc']}'>{n_unc_right/n_dis*100:.0f}%</div>"
        f"<div class='lbl'>trafień, gdy idziemy za UNCENSORED</div></div>"
        f"<div class='stat'><div class='big'>{_pct(unc_ret/n_dis)}</div><div class='lbl'>śr. zwrot/komórkę "
        f"kierunek UNC</div></div>"
        f"<div class='stat'><div class='big'>{_pct(cens_ret/n_dis)}</div><div class='lbl'>śr. zwrot/komórkę "
        f"kierunek CENS</div></div></div>"
        f"<p>Na spornych komórkach uncensored ma rację częściej — ale to garść zakładów "
        f"(~{n_dis/max(len(weeks_of(cells)),1):.1f}/tydzień) i w większości oznacza „long tam, gdzie "
        f"censored chciał short”, co w hossie jest tożsame z bull-tiltem. <b>Zobacz kolumnę OOS wyżej "
        f"zanim uwierzysz.</b></p>")


def _heatmap_section(cells: list[Cell]) -> str:
    wks = weeks_of(cells)
    tickers = sorted({c.ticker for c in cells})
    idx = {(c.ticker, c.week_monday): c.gross for c in cells}
    grid = [[idx.get((t, w)) for w in wks] for t in tickers]
    return charts.heatmap(tickers, wks, grid,
                          title="Zwrot spółki (long) per tydzień — realizacja rynku", vmax=0.06)


def _disposition_section(cells: list[Cell]) -> str:
    def disp(model: str):
        bull = flip = n = ncell = 0
        conv_hit = [0, 0]
        for c in cells:
            s = c.sig(model)
            if s is None:
                continue
            ncell += 1
            bull += s.p_bull
            if 0 < s.p_bull < 1:
                flip += 1
            # konwikcja vs trafność kierunku
            if s.direction != 0:
                hit = (s.direction * c.gross) > 0
                if s.conviction >= 0.8:
                    conv_hit[1] += 1
                    conv_hit[0] += hit
        return dict(bull=bull / ncell, flip=flip / ncell,
                    conv=conv_hit[0] / conv_hit[1] if conv_hit[1] else float("nan"),
                    convn=conv_hit[1])
    dc, du = disp(CENS), disp(UNC)
    rows = [
        ("Bull-rate (śr. p_bull)", f"{dc['bull']*100:.0f}%", f"{du['bull']*100:.0f}%"),
        ("Flip-rate (komórki niejednogłośne)", f"{dc['flip']*100:.0f}%", f"{du['flip']*100:.0f}%"),
        (f"Trafność przy wysokiej konwikcji (≥0.8)",
         f"{dc['conv']*100:.0f}% (n{dc['convn']})", f"{du['conv']*100:.0f}% (n{du['convn']})"),
    ]
    body = "".join(
        f"<tr><td style='text-align:left'>{html.escape(a)}</td>"
        f"<td style='color:{T['cens']}'>{b}</td><td style='color:{T['unc']}'>{c}</td></tr>"
        for a, b, c in rows)
    return (f"<table class='mt'><tr><th>metryka</th><th style='color:{T['cens']}'>CENSORED</th>"
            f"<th style='color:{T['unc']}'>UNCENSORED</th></tr>{body}</table>")


def _regime_section(cells: list[Cell]) -> str:
    up, down = RG.split_weeks(cells)
    head = (f"<tr><th>model</th><th>reżim</th><th>tyg</th><th>bull%</th><th>hit%</th>"
            f"<th>ret/tydz</th><th>α/tydz</th></tr>")
    body = []
    for r in RG.regime_rows(cells):
        col = T["cens"] if r.model == "CENSORED" else T["unc"]
        ac = T["good"] if r.avg_week_alpha > 0 else T["bad"]
        body.append(
            f"<tr><td style='text-align:left;color:{col}'>{r.model}</td>"
            f"<td>{'▲ up' if r.regime == 'up' else '▼ down'}</td><td>{r.weeks}</td>"
            f"<td>{r.bull_rate*100:.0f}%</td><td>{r.hit_rate*100:.0f}%</td>"
            f"<td>{_pct(r.avg_week_ret)}</td><td style='color:{ac}'>{_pct(r.avg_week_alpha)}</td></tr>")
    dd = RG.diff_in_diff(cells)
    sig = dd["ci_lo"] > 0 or dd["ci_hi"] < 0
    verdict = ("<b>zero POZA przedziałem → różnica reżimów jest istotna: przewaga uncensored "
               "żyje w up-weeks = BETA, nie skill.</b>" if sig else
               "zero w przedziale — brak rozstrzygnięcia.")
    return (
        f"<table class='mt'>{head}{''.join(body)}</table>"
        f"<div class='grid2'>"
        f"<div class='stat'><div class='big'>{_pct(dd['edge_up'])}</div>"
        f"<div class='lbl'>przewaga UNC/tydz w UP-weeks (n={dd['n_up']})</div></div>"
        f"<div class='stat'><div class='big'>{_pct(dd['edge_down'])}</div>"
        f"<div class='lbl'>przewaga UNC/tydz w DOWN-weeks (n={dd['n_down']})</div></div>"
        f"<div class='stat'><div class='big'>{dd['did']*100:+.2f}pp</div>"
        f"<div class='lbl'>diff-in-diff (up−down)</div></div>"
        f"<div class='stat'><div class='big'>[{dd['ci_lo']*100:+.2f}, {dd['ci_hi']*100:+.2f}]</div>"
        f"<div class='lbl'>CI95% DiD (bootstrap po tygodniach)</div></div></div>"
        f"<p>{verdict} Bull-rate uncensored jest niemal stały między reżimami "
        f"(tilt bezwarunkowy — model nie czyta rynku, ma przesunięty prior), a hit-rate "
        f"odwraca się z reżimem: w up-weeks lepszy uncensored, w down-weeks censored.</p>")


def _xsec_section(cells: list[Cell]) -> str:
    head = (f"<tr><th>model</th><th>wagi</th><th>ret/tydz</th><th>CI95%</th><th>total</th>"
            f"<th>Sharpe</th><th>IS</th><th>OOS</th></tr>")
    body = []
    for label, model in (("CENSORED", CENS), ("UNCENSORED", UNC)):
        for weighting in ("rank", "quintile"):
            r = XS.xsec_result(cells, label, model, weighting)
            col = T["cens"] if label == "CENSORED" else T["unc"]
            sig = r.ci_lo > 0 or r.ci_hi < 0
            body.append(
                f"<tr><td style='text-align:left;color:{col}'>{label}</td><td>{weighting}</td>"
                f"<td>{_pct(r.mean_week)}</td>"
                f"<td style='font-weight:{700 if sig else 400}'>[{r.ci_lo*100:+.2f}, {r.ci_hi*100:+.2f}]</td>"
                f"<td>{_pct(r.total)}</td><td>{r.sharpe:.2f}</td>"
                f"<td>{_pct(r.mean_is)}</td><td>{_pct(r.mean_oos)}</td></tr>")
    return (
        f"<table class='mt'>{head}{''.join(body)}</table>"
        f"<p>Long góra / short dół przekroju p_bull — dolarowo-neutralne, więc bull-tilt "
        f"kasuje się z konstrukcji i zostaje czysty sygnał WZGLĘDNY (która spółka lepsza). "
        f"<b>Wszystkie CI zawierają zero → model nie umie rankować spółek</b> (dyspersja sygnału "
        f"zdrowa, σ(p_bull)≈0.35 — to nie degeneracja, po prostu ranking nie przewiduje). "
        f"Censored rankuje nieistotnie lepiej niż uncensored.</p>")


def _confession_section(settings: Settings, cells: list[Cell]) -> str:
    rows_db = store.fetch_for_compare(settings.sqlite_path)
    doubt = CF.cell_doubt_map(rows_db)
    head = (f"<tr><th>model</th><th>wariant</th><th>ret/tydz</th><th>total</th>"
            f"<th>Sharpe</th><th>hit%</th></tr>")
    body, cis = [], []
    for label, model in (("CENSORED", CENS), ("UNCENSORED", UNC)):
        col = T["cens"] if label == "CENSORED" else T["unc"]
        pts_low = P.backtest(cells, CF.strat_fade(model, keep="low", doubt=doubt))
        pts_high = P.backtest(cells, CF.strat_fade(model, keep="high", doubt=doubt))
        pts_full = P.backtest(cells, P.strat_model(model))
        for variant, pts, which in (("FULL", pts_full, None), ("LOW-doubt", pts_low, "low"),
                                    ("HIGH-doubt", pts_high, "high")):
            m = P.metrics(variant, pts)
            h, _ = CF._percell_hit(cells, model, doubt, which)
            hl = variant == "LOW-doubt"
            body.append(
                f"<tr><td style='text-align:left;color:{col}'>{label}</td>"
                f"<td style='font-weight:{700 if hl else 400}'>{variant}</td>"
                f"<td>{_pct(ST.mean([p.ret for p in pts]))}</td><td>{_pct(m.total_return)}</td>"
                f"<td>{m.sharpe:.2f}</td><td>{h*100:.0f}%</td></tr>")
        d = [a.ret - b.ret for a, b in zip(pts_low, pts_high)]
        lo, _, hi = ST.block_bootstrap_ci(d, ST.mean)
        k = max(1, round(len(d) * 0.7))
        cis.append((label, ST.mean(d), lo, hi, ST.mean(d[:k]), ST.mean(d[k:])))
    stat_cards = "".join(
        f"<div class='stat'><div class='big'>{m*100:+.2f}%</div>"
        f"<div class='lbl'>{lb}: LOW−HIGH /tydz · CI [{lo*100:+.2f}, {hi*100:+.2f}] · "
        f"IS {i*100:+.2f} / OOS {o*100:+.2f}</div></div>"
        for lb, m, lo, hi, i, o in cis)
    return (
        f"<p class='sub'>doubt = tokeny kierunku PRZECIWNEGO do własnej decyzji na 100 słów "
        f"confession (spowiedź jest wymuszona promptem — mierzymy, KTÓRY kontr-argument model "
        f"wybiera: kierunkowy czy eventowy). Split po dolnej medianie tygodnia — bez lookahead.</p>"
        f"<table class='mt'>{head}{''.join(body)}</table>"
        f"<div class='grid2' style='grid-template-columns:repeat(2,1fr)'>{stat_cards}</div>"
        f"<p><b>Status: suggestive, nie potwierdzone.</b> Wrażliwość leksykonu (2026-07-03): znak "
        f"odporny (leave-one-out 62/62 &gt; 0; 75%-podzbiory dodatnie w 88–94% losowań), istotność "
        f"krucha (CI&gt;0 tylko w 26–53% podzbiorów; przy surowej normalizacji censored trzyma "
        f"[+0.45,+1.97], uncensored traci). Replikacja semantyczna (qwen3-embedding, oś z kotwic "
        f"lustrzanych): censored kierunkowo spójny (+0.16 pełny tekst / +0.39 klauzula — zgodne "
        f"z tłumieniem przy r≈0.36 między instrumentami), uncensored znika. Replikacja cross-model "
        f"(deepseek, 1 confession/komórkę): +0.15 [−0.21,+0.96] — znak dobry, moc za mała. "
        f"<b>Rozstrzygnięcie: pre-rejestrowany świeży OOS (PREREGISTRATION.md, ocena ~2026-09).</b></p>")


def _conf_hist(rows_db: list[dict]) -> dict[str, dict[str, float]]:
    """model → {bucket: udział} dla confidence tradera; buckets ≤0.5 / 0.55-0.6 / ≥0.65."""
    acc: dict[str, list[float]] = {}
    for r in rows_db:
        c = r.get("confidence")
        if isinstance(c, (int, float)):
            acc.setdefault(r["model_key"], []).append(c)
    out = {}
    for mk, xs in acc.items():
        n = len(xs)
        out[mk] = {
            "low": sum(1 for x in xs if x <= 0.5) / n,
            "mid": sum(1 for x in xs if 0.5 < x < 0.65) / n,
            "high": sum(1 for x in xs if x >= 0.65) / n,
            "n": n,
        }
    return out


def _collapse_section(settings: Settings) -> str:
    rows_db = store.fetch_for_compare(settings.sqlite_path)
    h = _conf_hist(rows_db)
    order = [("CENSORED", CENS, T["cens"]), ("UNCENSORED", UNC, T["unc"]),
             ("DEEPSEEK (prod)", BASELINE, T["bench"])]
    head = ("<tr><th>model</th><th>conf ≤ 0.5 (zwątpienie)</th><th>0.55–0.6</th>"
            "<th>≥ 0.65</th><th>n</th></tr>")
    body = []
    for label, mk, col in order:
        d = h.get(mk)
        if not d:
            continue
        body.append(
            f"<tr><td style='text-align:left;color:{col}'>{label}</td>"
            f"<td style='font-weight:700'>{d['low']*100:.1f}%</td><td>{d['mid']*100:.1f}%</td>"
            f"<td>{d['high']*100:.1f}%</td><td>{d['n']}</td></tr>")
    hz = {}
    for r in rows_db:
        if r.get("time_horizon"):
            hz.setdefault(r["model_key"], []).append(r["time_horizon"])
    hz_line = " · ".join(
        f"{lb}: 1w w {sum(1 for x in hz.get(mk, []) if x == '1w')/max(len(hz.get(mk, [])),1)*100:.0f}%"
        for lb, mk, _ in order if mk in hz)
    return (
        f"<table class='mt'>{head}{''.join(body)}</table>"
        f"<p><b>Abliteracja usuwa ogon niskiej pewności („wycięte zwątpienie\")</b>: censored schodzi "
        f"do 0.3/0.5 w {h.get(CENS, {}).get('low', 0)*100:.0f}% decyzji, uncensored praktycznie nigdy "
        f"({h.get(UNC, {}).get('low', 0)*100:.1f}%). Deepseek (z tym samym promptem) używa całej góry "
        f"skali. Drugi collapse — <b>oś horyzontu</b>: {hz_line} (prompt oferuje 1w/2w/4w; 4w nie "
        f"występuje nigdy). Uwaga: dawny wiersz „deferencja 100%\" wycofany — to artefakt replayu "
        f"(continuity OFF ⇒ prompt nie daje traderowi podstawy do veta), nie dyspozycja modelu.</p>")


def _syco_section(settings: Settings) -> str:
    """B1 adversarial sycophancy — tabela obu ramion + asymetria kierunkowa."""
    import random
    from collections import defaultdict
    trials = [t for t in store.fetch_syco(settings.sqlite_path) if t.get("stance")]
    if not trials:
        return "<p class='sub'>(brak prób syco_trials — sekcja pojawi się po `syco-sweep`)</p>"

    def cell_rates(mk: str, cond: str) -> dict[str, float]:
        by: dict[str, list[int]] = defaultdict(list)
        for t in trials:
            if t["model_key"] == mk and t["condition"] == cond and t["followed_shown"] is not None:
                by[t["upstream_run_id"]].append(t["followed_shown"])
        return {k: sum(v) / len(v) for k, v in by.items()}

    def ci(vals: list[float], seed: int = 1337) -> tuple[float, float]:
        rng = random.Random(seed)
        ms = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(4000))
        return ms[int(0.025 * len(ms))], ms[int(0.975 * len(ms))]

    head = ("<tr><th>model</th><th>warunek</th><th>uległość (za pokazanym)</th>"
            "<th>CI95%</th><th>za dowodami</th></tr>")
    body, diffs = [], []
    for label, mk, col in (("CENSORED", CENS, T["cens"]), ("UNCENSORED", UNC, T["unc"])):
        for cond in ("flip", "flip_blind"):
            rates = cell_rates(mk, cond)
            if not rates:
                continue
            vals = list(rates.values())
            lo, hi = ci(vals)
            mean = sum(vals) / len(vals)
            body.append(f"<tr><td style='text-align:left;color:{col}'>{label}</td><td>{cond}</td>"
                        f"<td style='font-weight:700'>{mean*100:.1f}%</td>"
                        f"<td>[{lo*100:.1f}, {hi*100:.1f}]</td><td>{(1-mean)*100:.1f}%</td></tr>")
    for cond in ("flip", "flip_blind"):
        c, u = cell_rates(CENS, cond), cell_rates(UNC, cond)
        common = sorted(c.keys() & u.keys())
        if not common:
            continue
        d = [c[k] - u[k] for k in common]
        lo, hi = ci(d)
        sig = lo > 0 or hi < 0
        diffs.append(f"<b>{cond}</b>: CENS−UNC = {sum(d)/len(d)*100:+.1f}pp "
                     f"CI[{lo*100:+.1f}, {hi*100:+.1f}]"
                     + (" — <b>istotna</b>" if sig else " — brak różnicy"))
    # asymetria kierunkowa (flip)
    asym = []
    for label, mk in (("CENS", CENS), ("UNC", UNC)):
        for shown in ("bear", "bull"):
            sub = [t["followed_shown"] for t in trials
                   if t["model_key"] == mk and t["condition"] == "flip"
                   and t["director_stance_shown"] == shown]
            if sub:
                asym.append(f"{label}/pokazano {shown}: {sum(sub)/len(sub)*100:.1f}% (n={len(sub)})")
    return (
        f"<p class='sub'>Perturbacja: trader dostaje ODWRÓCONY werdykt Dyrektora — przy "
        f"niezmienionym dokumencie argumentującym oryginalny kierunek (flip) albo bez dokumentu "
        f"(flip_blind). Kontrola bez perturbacji: zgoda ~100% u obu. Dokumenty Dyrektora reużyte "
        f"z głównego biegu; transformacja deterministyczna (zero kontaminacji między ramionami).</p>"
        f"<table class='mt'>{head}{''.join(body)}</table>"
        f"<p>{' · '.join(diffs)} (sparowany bootstrap po wspólnych komórkach).</p>"
        f"<p><b>Gdzie żyje różnica</b> — asymetria kierunkowa w flip: {' · '.join(asym)}. "
        f"Cała różnica między modelami siedzi w kwadrancie „autorytet każe SPRZEDAĆ wbrew byczemu "
        f"dokumentowi\" (kapitulacja CENS ~2× częstsza); „KUPUJ wbrew niedźwiedziemu\" nie kupuje "
        f"prawie nikt. Wniosek: abliteracja nie zmienia wagi gołego autorytetu (flip_blind bez "
        f"różnicy, obaj ~45%), wycina <b>deferencję wobec ostrzeżeń</b> — czwarta niezależna "
        f"obserwacja osi „ostrożność/zwątpienie\" (obok bull-tiltu, ogona confidence i kanału "
        f"confession).</p>")


def _verdict(cells: list[Cell], rows: list[StratRow]) -> str:
    by_name = {r.m.name: r for r in rows}
    unc = by_name["UNCENSORED cons"].m
    cens = by_name["CENSORED cons"].m
    always = by_name["ALWAYS-LONG (EW)"].m
    cl, cs = P.sleeve_returns(cells, CENS)
    ul, us = P.sleeve_returns(cells, UNC)
    bench = unc.total_bench
    items = [
        f"Rynek (WIG20) w oknie: <b>{_pct(bench)}</b> — hossa. „Kup wszystko equal-weight\" dało "
        f"<b>{_pct(always.total_return)}</b> (α {_pct(always.alpha_total)}).",
        f"UNCENSORED cons <b>{_pct(unc.total_return)}</b> (α {_pct(unc.alpha_total)}) &gt; "
        f"CENSORED cons <b>{_pct(cens.total_return)}</b> (α {_pct(cens.alpha_total)}) — ale <b>oba "
        f"przegrywają z rynkiem</b>.",
        f"Rękaw SHORT: CENS {_pct(cs)}, UNC {_pct(us)} — {'oba tracą na shortach' if cs<0 and us<0 else 'mieszane'}. "
        f"Przewaga uncensored to głównie <b>mniej shortów w hossie</b> (bull-tilt), nie trafniejszy kierunek.",
    ]
    lead = ("Na tym oknie <b>żadna strategia oparta na samym modelu nie bije rynku</b>. "
            "Uncensored wypada lepiej od censored, lecz różnica to w przeważającej części "
            "<b>beta (bull-tilt) w rynku byka</b>, a nie kierunkowy skill. Sygnały hybrydowe "
            "(DISAGREE) wyglądają efektownie in-sample, ale trzeba je zważyć testem OOS niżej.")
    lis = "".join(f"<li>{x}</li>" for x in items)
    return f"<p class='lead'>{lead}</p><ul class='tldr'>{lis}</ul>"


CSS = """
:root{color-scheme:dark}
body{margin:0;background:#0b0d12;color:#c9d1d9;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.5}
.wrap{max-width:960px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:40px 0 10px;padding-top:14px;border-top:1px solid #242833}
h3{font-size:15px;color:#8b949e;margin:22px 0 8px;font-weight:600}
.sub{color:#7d8590;font-size:13px;margin:0 0 8px}
.lead{font-size:15px;background:#12151c;border-left:3px solid #4aa3e0;padding:12px 16px;border-radius:6px}
ul.tldr{padding-left:18px}ul.tldr li{margin:6px 0}
.card{background:#12151c;border:1px solid #1e222b;border-radius:10px;padding:16px;margin:14px 0}
table.mt{width:100%;border-collapse:collapse;font-family:ui-monospace,monospace;font-size:12.5px;margin:8px 0}
table.mt th,table.mt td{padding:6px 8px;text-align:right;border-bottom:1px solid #1e222b}
table.mt th{color:#7d8590;font-weight:600;text-align:right}
table.mt tr:hover td{background:#161a22}
.cap{color:#7d8590;font-size:12px;margin:6px 0 0}
.grid2{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}
.stat{background:#12151c;border:1px solid #1e222b;border-radius:8px;padding:12px;text-align:center}
.stat .big{font-size:24px;font-weight:700;font-family:ui-monospace,monospace}
.stat .lbl{font-size:11px;color:#7d8590;margin-top:4px}
.warn{background:#1a1410;border-left:3px solid #d29922;padding:12px 16px;border-radius:6px;font-size:13.5px}
@media(max-width:620px){.grid2{grid-template-columns:repeat(2,1fr)}}
"""


def build_report(settings: Settings, *, min_samples: int = 5, title: str = "") -> str:
    cells = build_panel(settings, min_samples=min_samples)
    rows = _all_metrics(cells)
    # 3-way: deepseek (1 produkcyjna decyzja/komórkę) wymaga panelu min_samples=1
    cells1 = build_panel(settings, min_samples=1)
    if any(c.sig(BASELINE) for c in cells1):
        pts_ds = P.backtest(cells1, P.strat_model(BASELINE))
        rows.append(StratRow("DEEPSEEK cons (prod, n=1)", P.metrics("DEEPSEEK cons (prod, n=1)", pts_ds),
                             P.turnover(pts_ds)))
    wks = weeks_of(cells)
    both = sum(1 for c in cells if c.sig(CENS) and c.sig(UNC))
    bench_total = next(r.m.total_bench for r in rows if r.m.name == "CENSORED cons")
    ttl = title or "Censored vs Uncensored — decyzje inwestycyjne na GPW (WIG20)"

    S = []
    S.append(f"<div class='wrap'>")
    S.append(f"<h1>{html.escape(ttl)}</h1>")
    S.append(f"<p class='sub'>Replay warstwy decyzyjnej gielda-agents (Dyrektor Researchu + Szef Funduszu) "
             f"na gemma-4-26B-A4B, ten sam upstream — jedyna zmienna to model. "
             f"{len(cells)} komórek · {len(wks)} tygodni · {len({c.ticker for c in cells})} spółek · "
             f"{both} komórek ma OBA modele (N≥{min_samples} próbek).</p>")

    S.append("<h2>TL;DR</h2>")
    S.append(_verdict(cells, rows))

    S.append("<h2>1 · Equity curve</h2>")
    S.append(f"<div class='card'>{_equity_chart(cells)}</div>")

    S.append("<h2>2 · Ranking strategii</h2>")
    S.append(f"<div class='card'>{_ranking_bar(rows)}</div>")
    S.append(_metrics_table(rows, bench_total))

    S.append("<h2>3 · Skill czy beta rynku?</h2>")
    S.append("<p class='sub'>Kluczowe pytanie uczciwości: czy uncensored jest MĄDRZEJSZY, czy tylko "
             "BARDZIEJ BYCZY w rynku, który rósł?</p>")
    S.append(f"<div class='card'>{_skill_vs_beta(cells)}</div>")

    S.append("<h2>4 · Walk-forward (out-of-sample)</h2>")
    S.append("<p class='sub'>Strategie wybrane na wcześniejszym oknie — czy przewaga utrzymuje się na "
             "późniejszym, nieoglądanym? Filtr przeuczenia.</p>")
    S.append(_walkforward(cells))

    S.append("<h2>5 · Regime-conditional: up-weeks vs down-weeks</h2>")
    S.append("<p class='sub'>Confound-killer: jeśli przewaga uncensored istnieje tylko w tygodniach "
             "wzrostowych, to jest betą (bull-tilt), nie skillem.</p>")
    S.append(_regime_section(cells))

    S.append("<h2>6 · Cross-sectional rank (market-neutral)</h2>")
    S.append(_xsec_section(cells))

    S.append("<h2>7 · Confession-fade (kanał kalibracji w tekście)</h2>")
    S.append(_confession_section(settings, cells))

    S.append("<h2>8 · Adversarial sycophancy (B1)</h2>")
    S.append(_syco_section(settings))

    S.append("<h2>9 · Gdy modele się kłócą</h2>")
    S.append(_disagree_section(cells))

    S.append("<h2>10 · Realizacja rynku (heatmapa)</h2>")
    S.append(f"<div class='card'>{_heatmap_section(cells)}</div>")

    S.append("<h2>11 · Dyspozycja decyzyjna i collapse'y (3 modele)</h2>")
    S.append(_disposition_section(cells))
    S.append(_collapse_section(settings))

    S.append("<h2>12 · Zastrzeżenia</h2>")
    S.append(
        "<div class='warn'><b>Czytaj wyniki przez te filtry:</b><ul>"
        "<li><b>16 tygodni</b> to n≈16 niezależnych obserwacji rynkowych. Sharpe &gt;2 na takim oknie "
        "bywa artefaktem — stąd walk-forward.</li>"
        "<li><b>Rynek byka</b>: WIG20 rósł, więc long-bias = darmowa alfa. To confound z „uncensored jest "
        "bardziej byczy\". Rozstrzygnie dopiero okno bessy/boczne.</li>"
        "<li><b>Wiele strategii = wiele porównań</b>: najlepsza z 11 strategii jest z definicji faworyzowana. "
        "Traktuj ranking jako generowanie hipotez, nie potwierdzanie.</li>"
        "<li><b>Multiplikatywne, bez kosztów</b>: brak prowizji/spreadu/poślizgu. Turnover w tabeli mówi, "
        "ile te koszty by zjadły.</li>"
        "<li><b>Mała gemma</b>: model słaby na GPW w liczbach bezwzględnych — cała gra toczy się o RÓŻNICĘ "
        "między wersjami, nie o zyskowność per se.</li></ul></div>")

    S.append("</div>")
    return f"<style>{CSS}</style>" + "".join(S)
