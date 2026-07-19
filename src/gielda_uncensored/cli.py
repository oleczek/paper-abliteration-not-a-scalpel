"""CLI `gielda-unc` — e2e dla jednej spółki, potem drugi model + porównanie.

Typowy przebieg (e2e):
    gielda-unc init-db
    gielda-unc list-upstream --ticker PKN
    gielda-unc decide --ticker PKN --as-of 2026-06-13 --model gb10-gemma4 --model-name gemma4
    gielda-unc pnl    --ticker PKN --as-of 2026-06-13 --model gb10-gemma4
    # wieczorem — model uncensored na TYM SAMYM upstreamie:
    gielda-unc decide --ticker PKN --as-of 2026-06-13 --model gb10-gemma4-uncensored --model-name gemma4-uncensored
    gielda-unc pnl    --ticker PKN --as-of 2026-06-13 --model gb10-gemma4-uncensored
    gielda-unc import-baseline --ticker PKN --as-of 2026-06-13
    gielda-unc compare --ticker PKN
"""
from __future__ import annotations

import asyncio
from datetime import date

import click

from gielda_uncensored import compare as compare_mod
from gielda_uncensored.config import load_settings
from gielda_uncensored.db import schema, store
from gielda_uncensored.pipeline.decide import run_decision
from gielda_uncensored.pipeline.sweep import run_sweep
from gielda_uncensored.pnl.runner import price_decisions, price_rows
from gielda_uncensored.upstream.select import list_upstream, pick_run, resolve_tickers


def _parse_date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


@click.group()
def main() -> None:
    """Replay warstwy decyzyjnej gielda-agents na własnym modelu + PnL benchmark."""


@main.command("init-db")
def init_db_cmd() -> None:
    """Utwórz SQLite ze schematem."""
    s = load_settings()
    schema.init_db(s.sqlite_path)
    click.echo(f"OK: {s.sqlite_path}")


@main.command("list-upstream")
@click.option("--ticker", required=True)
def list_upstream_cmd(ticker: str) -> None:
    """Wypisz dostępne runy deepseeka (upstream) dla spółki."""
    s = load_settings()
    refs = asyncio.run(list_upstream(s.agents_dsn, ticker=ticker))
    if not refs:
        click.echo("(brak runów)")
        return
    for r in refs:
        click.echo(f"{r.as_of_date}  {r.variant:12}  {r.status:6}  {r.run_id}")


def _resolve_run_id(s, ticker: str, as_of: str, variant: str, run_id: str | None) -> str:
    if run_id:
        return run_id
    rid = asyncio.run(pick_run(s.agents_dsn, ticker=ticker, as_of=date.fromisoformat(as_of), variant=variant))
    if not rid:
        raise click.ClickException(
            f"nie znaleziono runu upstream dla {ticker} {as_of} variant={variant}"
        )
    return rid


@main.command("decide")
@click.option("--ticker", required=True)
@click.option("--as-of", "as_of", required=True, help="data decyzji, ISO (zwykle sobota)")
@click.option("--model", "model_key", required=True, help="etykieta modelu w SQLite, np. gb10-gemma4")
@click.option("--model-name", "wire_model", default=None, help="nazwa modelu na serwerze (domyślnie z .env GB10_MODEL)")
@click.option("--variant", default="kierunkowy")
@click.option("--run-id", default=None, help="wymuś konkretny upstream run_id")
@click.option("--reasoning/--no-reasoning", default=None, help="nadpisz GB10_REASONING")
@click.option("--samples", default=1, type=int, help="ile próbek (pomiar zmienności)")
def decide_cmd(ticker, as_of, model_key, wire_model, variant, run_id, reasoning, samples) -> None:
    """Odpal manager+trader na modelu dla jednej spółki/daty (upstream z bazy).
    --samples N dokłada N próbek tego samego modelu (nie nadpisuje)."""
    s = load_settings()
    wire = wire_model or s.gb10_model
    use_reasoning = s.gb10_reasoning if reasoning is None else reasoning
    rid = _resolve_run_id(s, ticker, as_of, variant, run_id)
    base = store.next_sample_idx(s.sqlite_path, upstream_run_id=rid, model_key=model_key)
    click.echo(f"upstream run_id={rid}  model={model_key} (wire={wire}, reasoning={use_reasoning}) "
               f"samples={samples} od idx={base}")
    for i in range(samples):
        sidx = base + i
        row = asyncio.run(run_decision(
            s, run_id=rid, model_key_label=model_key, wire_model=wire,
            reasoning=use_reasoning, sample_idx=sidx,
        ))
        click.echo(
            f"  s{sidx}: stance={row['stance']} conf={row['confidence']} "
            f"dyrektor={row['director_stance']}/{row['director_strategy']} "
            f"horizon={row['time_horizon']} style={row['investment_style']}"
        )
        click.echo(f"       teza: {row['thesis_one_liner']}")


@main.command("decide-v2")
@click.option("--ticker", required=True)
@click.option("--as-of", "as_of", required=True, help="data decyzji, ISO (zwykle sobota)")
@click.option("--model", "model_key", required=True, help="etykieta modelu w SQLite, np. final-gemma4-cens")
@click.option("--model-name", "wire_model", default=None, help="nazwa modelu na serwerze (domyślnie GB10_MODEL)")
@click.option("--variant", default="kierunkowy")
@click.option("--run-id", default=None, help="wymuś konkretny upstream run_id")
@click.option("--samples", default=1, type=int, help="ile próbek")
@click.option("--dry-run", is_flag=True, help="NIE zapisuj do SQLite (sanity promptu przed freeze)")
def decide_v2_cmd(ticker, as_of, model_key, wire_model, variant, run_id, samples, dry_run) -> None:
    """Manager-only v2 (final_research) dla jednej spółki/daty — sanity/pojedynczy strzał.
    Pełny output (z sondami confession/reversal_trigger/p_wrong) idzie na stdout."""
    from gielda_uncensored.pipeline.decide_v2 import run_decision_v2
    s = load_settings()
    wire = wire_model or s.gb10_model
    rid = _resolve_run_id(s, ticker, as_of, variant, run_id)
    base = 0 if dry_run else store.next_sample_idx(
        s.sqlite_path, upstream_run_id=rid, model_key=model_key)
    click.echo(f"upstream run_id={rid}  model={model_key} (wire={wire}) "
               f"samples={samples} od idx={base}{'  [DRY-RUN]' if dry_run else ''}")
    for i in range(samples):
        row = asyncio.run(run_decision_v2(
            s, run_id=rid, model_key_label=model_key, wire_model=wire,
            sample_idx=base + i, persist=not dry_run,
        ))
        click.echo(f"--- s{base + i} ({row['mgr_input_tokens']} tok in / "
                   f"{row['mgr_output_tokens']} out, {row['mgr_duration_s']:.1f}s) ---")
        click.echo(row["manager_structured"])


@main.command("pnl")
@click.option("--ticker", required=True)
@click.option("--as-of", "as_of", required=True)
@click.option("--model", "model_key", required=True)
def pnl_cmd(ticker, as_of, model_key) -> None:
    """Policz PnL WSZYSTKICH próbek decyzji (open pon → close pt tygodnia po as_of)."""
    s = load_settings()
    rows = asyncio.run(price_decisions(s, ticker=ticker, as_of=as_of, model_key=model_key))
    for row in rows:
        if row["status"] != "closed":
            click.echo(f"PnL: {row['status']} (tydzień {row['week_monday']}..{row['week_friday']})")
            continue
        click.echo(
            f"PnL {ticker} {row['week_monday']}..{row['week_friday']}  pos={row['position']:+d}  "
            f"entry={row['entry_open']:.2f} exit={row['exit_close']:.2f}  "
            f"signed={row['signed_return_pct'] * 100:+.2f}%  "
            f"bench={(row['bench_return_pct'] or 0) * 100:+.2f}%  "
            f"alpha={(row['alpha_pct'] or 0) * 100:+.2f}%"
        )


@main.command("import-baseline")
@click.option("--ticker", required=True)
@click.option("--as-of", "as_of", required=True)
@click.option("--variant", default="kierunkowy")
@click.option("--run-id", default=None)
def import_baseline_cmd(ticker, as_of, variant, run_id) -> None:
    """Skopiuj decyzję deepseeka (baseline) z bazy gielda-agents do SQLite (read-only)."""
    s = load_settings()
    rid = _resolve_run_id(s, ticker, as_of, variant, run_id)
    row = asyncio.run(compare_mod.import_baseline(s, rid))
    if row is None:
        click.echo("brak decyzji baseline w bazie dla tego runu")
        return
    click.echo(f"baseline zaimportowany: stance={row['stance']} conf={row['confidence']}")


@main.command("run-sweep")
@click.option("--model", "model_key", required=True, help="etykieta modelu w SQLite")
@click.option("--model-name", "wire_model", default=None, help="nazwa modelu na serwerze (domyślnie GB10_MODEL)")
@click.option("--tickers", default="ALL", help="ALL (WIG20+mWIG40) | WIG20 | mWIG40 | lista PKN,PKO,...")
@click.option("--from", "date_from", default=None, help="as_of od (ISO)")
@click.option("--to", "date_to", default=None, help="as_of do (ISO)")
@click.option("--variant", default="kierunkowy")
@click.option("--samples", default=1, type=int, help="DOŁÓŻ N próbek na (ticker, tydzień)")
@click.option("--target-samples", "samples_target", default=None, type=int,
              help="UZUPEŁNIJ DO N próbek (restart-safe: skończone komórki pomijane; nadpisuje --samples)")
@click.option("--concurrency", default=3, type=int, help="ile decyzji równolegle")
@click.option("--limit", default=None, type=int, help="ogranicz liczbę runów (test)")
@click.option("--reasoning/--no-reasoning", default=None)
@click.option("--skip-pnl", is_flag=True, help="nie licz PnL po sweepie")
@click.option("--verify", type=click.Choice(["censored", "uncensored"]), default=None,
              help="przed biegiem sprawdź sondą, że NA MASZYNIE jest oczekiwany model (abort przy niezgodności)")
@click.option("--temp", default=None, type=float,
              help="nadpisz temperaturę OBU person (oś temperatury; domyślnie 0.8/0.6 z person)")
@click.option("--top-p", default=None, type=float, help="top_p (np. 0.95 wg rekomendacji Gemmy)")
@click.option("--top-k", default=None, type=int, help="top_k (np. 64 wg rekomendacji Gemmy; vLLM extra_body)")
@click.option("--flow", type=click.Choice(["v1", "v2"]), default="v1",
              help="v1 = manager+trader (pilot) | v2 = manager-only, prompt final_research (nowe badanie)")
def run_sweep_cmd(model_key, wire_model, tickers, date_from, date_to, variant,
                  samples, samples_target, concurrency, limit, reasoning, skip_pnl, verify,
                  temp, top_p, top_k, flow) -> None:
    """Przelot po całym WIG20+mWIG40 × dostępne tygodnie × próbki, na jednym modelu."""
    s = load_settings()
    wire = wire_model or s.gb10_model
    use_reasoning = s.gb10_reasoning if reasoning is None else reasoning
    sampling = {k: v for k, v in
                (("temperature", temp), ("top_p", top_p), ("top_k", top_k)) if v is not None} or None
    if sampling:
        click.echo(f"[sampling override] {sampling}")
    try:
        res = asyncio.run(run_sweep(
            s, model_key=model_key, wire_model=wire, reasoning=use_reasoning,
            tickers=resolve_tickers(tickers), date_from=_parse_date(date_from),
            date_to=_parse_date(date_to), variant=variant, samples=samples,
            samples_target=samples_target,
            concurrency=concurrency, with_pnl=not skip_pnl, limit=limit,
            verify=verify, sampling=sampling, flow=flow, echo=click.echo,
        ))
    except RuntimeError as e:
        raise click.ClickException(str(e))
    click.echo(f"\nSWEEP: total={res.total} ok={res.ok} failed={res.failed}")
    for e in res.errors[:20]:
        click.echo(f"  ! {e}")


@main.command("pnl-sweep")
@click.option("--model", "model_key", default=None, help="tylko ten model (domyślnie: wszystkie)")
def pnl_sweep_cmd(model_key) -> None:
    """Policz PnL dla wszystkich decyzji bez marki (dowolne tickery/daty/próbki)."""
    s = load_settings()
    rows = store.decisions_without_marks(s.sqlite_path, model_key=model_key)
    n = asyncio.run(price_rows(s, rows))
    click.echo(f"policzono {n} marek PnL (było {len(rows)} decyzji bez marki)")


@main.command("compare")
@click.option("--ticker", default=None)
def compare_cmd(ticker) -> None:
    """Zestaw decyzje per model na wspólnym upstream_run_id (+ PnL/alfa)."""
    s = load_settings()
    groups = compare_mod.build_comparison(s, ticker=ticker)
    click.echo(compare_mod.render_comparison_text(groups))


@main.command("disposition")
@click.option("--ticker", default=None)
def disposition_cmd(ticker) -> None:
    """Metryki dyspozycji decyzyjnej per model (bias byczy, hedżowanie, flip-rate,
    kalibracja, skill...) + kontrast parowy. Do wyłowienia różnic censored vs uncensored."""
    from gielda_uncensored import disposition
    s = load_settings()
    click.echo(disposition.render_disposition(s, ticker=ticker))


@main.command("probe-model")
def probe_model_cmd() -> None:
    """Wykryj który model jest załadowany na gb10 (censored vs uncensored) — sonda odmów.
    Odpal RAZ gdy wiesz, że to censored (baseline), i RAZ po podmianie → porównaj refusal-rate."""
    from gielda_uncensored import model_probe
    s = load_settings()
    res = asyncio.run(model_probe.probe_model(s.gb10_base_url, s.gb10_api_key, s.gb10_model))
    click.echo(model_probe.render(res))


@main.command("report")
@click.option("--out", default="report.html", help="ścieżka wyjściowa HTML")
@click.option("--min-samples", default=5, type=int, help="min. próbek na komórkę modelu")
@click.option("--title", default="", help="tytuł raportu")
def report_cmd(out, min_samples, title) -> None:
    """Zbuduj self-contained raport HTML: equity curves, ranking strategii, skill-vs-beta,
    walk-forward OOS, spory modeli, heatmapa, dyspozycja. Zero nowych biegów LLM."""
    from gielda_uncensored.reporting.report import build_report
    s = load_settings()
    doc = build_report(s, min_samples=min_samples, title=title)
    with open(out, "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset='utf-8'>" + doc)
    click.echo(f"OK: {out} ({len(doc)} B)")


@main.command("export-cufolio")
@click.option("--out-dir", default="export", help="katalog na CSV")
@click.option("--min-samples", default=5, type=int)
def export_cufolio_cmd(out_dir, min_samples) -> None:
    """Wyeksportuj macierze week×ticker (returns, bench, signal/conv per model) do CSV
    pod cuFOLIO / optymalizator portfela."""
    from gielda_uncensored.reporting.export import export_matrices
    s = load_settings()
    files = export_matrices(s, out_dir, min_samples=min_samples)
    for f in files:
        click.echo(f"  {f}")
    click.echo(f"OK: {len(files)} plików w {out_dir}/")


@main.command("regime")
@click.option("--min-samples", default=5, type=int, help="min. próbek na komórkę modelu")
def regime_cmd(min_samples) -> None:
    """Regime-conditional split: metryki per model w tygodniach WIG20-up vs WIG20-down
    + diff-in-diff z bootstrap CI. Rozstrzyga beta (bull-tilt) vs skill."""
    from gielda_uncensored.reporting.regime import render_regime
    s = load_settings()
    click.echo(render_regime(s, min_samples=min_samples))


@main.command("xsec")
@click.option("--min-samples", default=5, type=int, help="min. próbek na komórkę modelu")
def xsec_cmd(min_samples) -> None:
    """Cross-sectional rank (market-neutral): long góra / short dół przekroju p_bull.
    Kasuje confound bull-tiltu; mierzy sygnał WZGLĘDNY z bootstrap CI."""
    from gielda_uncensored.reporting.xsec import render_xsec
    s = load_settings()
    click.echo(render_xsec(s, min_samples=min_samples))


@main.command("confession")
@click.option("--min-samples", default=5, type=int, help="min. próbek na komórkę modelu")
def confession_cmd(min_samples) -> None:
    """Confession-vs-stance fade: lęk kontr-kierunkowy w tekście confession jako filtr
    jakości decyzji (LOW vs HIGH doubt, split po medianie tygodnia, bootstrap CI)."""
    from gielda_uncensored.reporting.confession import render_confession
    s = load_settings()
    click.echo(render_confession(s, min_samples=min_samples))


@main.command("confession-sens")
@click.option("--min-samples", default=5, type=int)
@click.option("--draws", default=200, type=int, help="losowań podzbiorów leksykonu")
def confession_sens_cmd(min_samples, draws) -> None:
    """Analiza wrażliwości leksykonu confession-fade: częstości tokenów, leave-one-out,
    losowe podzbiory 75%/50%, wariant normalizacji. Odpiera zarzut spec-miningu."""
    from gielda_uncensored.reporting.lexsens import render_sensitivity
    s = load_settings()
    click.echo(render_sensitivity(s, min_samples=min_samples, n_draws=draws))


@main.command("confession-emb")
@click.option("--min-samples", default=5, type=int)
def confession_emb_cmd(min_samples) -> None:
    """Semantyczny doubt na embeddingach (qwen3 @ haxor): oś kierunkowa z kotwic
    lustrzanych zamiast leksykonu. Walidacja confession-fade niezależna od listy słów."""
    from gielda_uncensored.reporting.embdoubt import compute
    s = load_settings()
    click.echo(asyncio.run(compute(s, min_samples=min_samples, echo=click.echo)))


@main.command("syco-sweep")
@click.option("--model", "model_key", required=True, help="gb10-gemma4 | gb10-gemma4-uncensored")
@click.option("--model-name", "wire_model", default=None, help="nazwa modelu na serwerze (domyślnie GB10_MODEL)")
@click.option("--tickers", default="WIG20", help="WIG20 | lista PKN,PKO,...")
@click.option("--samples", default=3, type=int, help="ile próbek źródłowych na komórkę (dokumenty Dyrektora)")
@click.option("--conditions", default="flip,flip_blind", help="flip,flip_blind")
@click.option("--concurrency", default=8, type=int)
@click.option("--limit", default=None, type=int, help="ogranicz liczbę prób (pilot/test)")
@click.option("--verify", type=click.Choice(["censored", "uncensored"]), required=True,
              help="sonda modelu na maszynie przed biegiem (OBOWIĄZKOWA)")
def syco_sweep_cmd(model_key, wire_model, tickers, samples, conditions, concurrency, limit, verify) -> None:
    """B1 adversarial sycophancy: trader na ODWRÓCONYM werdykcie Dyrektora (dokumenty
    reużyte z decisions — leci tylko trader). Idempotentny, wznawia po przerwaniu."""
    from gielda_uncensored.pipeline.adversarial import run_syco_sweep
    from gielda_uncensored.upstream.select import resolve_tickers
    s = load_settings()
    res = asyncio.run(run_syco_sweep(
        s, model_key=model_key, wire_model=wire_model or s.gb10_model,
        reasoning=s.gb10_reasoning, tickers=resolve_tickers(tickers),
        samples=samples, conditions=tuple(conditions.split(",")),
        concurrency=concurrency, limit=limit, verify=verify, echo=click.echo,
    ))
    click.echo(f"\nSYCO: total={res.total} ok={res.ok} failed={res.failed} skipped={res.skipped}")
    for e in res.errors[:20]:
        click.echo(f"  ! {e}")


@main.command("syco-report")
def syco_report_cmd() -> None:
    """Wyniki adversarial sycophancy: uległość wobec odwróconego werdyktu per model×warunek."""
    from gielda_uncensored.pipeline.adversarial import render_syco_report
    s = load_settings()
    click.echo(render_syco_report(s))


@main.command("consensus")
@click.option("--ticker", default=None)
@click.option("--conviction", default=0.6, type=float, help="próg siły zgody dla filtra high-conviction")
def consensus_cmd(ticker, conviction) -> None:
    """Konsensus między biegami (głosowanie większościowe per komórka): czy odszumienie
    wydobywa sygnał i czy edge rośnie przy wysokiej zgodzie."""
    from gielda_uncensored import consensus
    s = load_settings()
    click.echo(consensus.render_consensus(s, ticker=ticker, conviction_thr=conviction))


if __name__ == "__main__":
    main()
