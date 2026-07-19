# PREREGISTRATION v2 — ZAMROŻONA 2026-07-05

**Status: ZAMROŻONA 2026-07-05 (akcept usera), commit + tag `freeze-v2-2026-07-05`,
PRZED pierwszą komórką siatki głównej.** Prompt zamrożony równolegle
(`prompts/FROZEN.txt`).
Stara pre-rejestracja C1 (`../PREREGISTRATION.md`) zamknięta przez redesign
2026-07-04, przed zebraniem danych testowych.

## Pytanie badawcze

Czy abliteracja (usunięcie refusal-direction) zmienia dyspozycję decyzyjną
modelu w zadaniu finansowym niezwiązanym z odmowami — i czy delta ma ten sam
znak/kształt w dwóch rodzinach modeli (Gemma-4-26B-A4B, Qwen3-30B-A3B).

## Design (skrót — pełny w README.md)

Czysta para BF16 od jednego wydawcy per rodzina, serwowana identycznie
(vLLM, te same flagi, `max-model-len 16384`). Replay manager-only na mrożonym
upstreamie gielda-agents. Prompt `manager_v2.md` bajt-w-bajt identyczny dla
obu ramion, zamrożony hashem. Rama: DELTY within-pair; nigdy Gemma vs Qwen wprost.

**Design FAZOWY (decyzja usera 2026-07-05, PRZED biegiem):**
- **Faza 1 (pierwotna): WIG20 × 18 sobót (2026-03-01…2026-07-04) × n=5
  × 2 ramiona.** Endpointy P1-P3 ocenia się NA TEJ próbie.
- **Bramka mWIG40:** bieg mWIG40 uruchamiany TYLKO gdy Faza 1 pokaże
  niezerową deltę (dowolny z P1-P3 spełniony) — wtedy mWIG40 jest
  WEWNĄTRZRODZINNĄ replikacją na świeżym segmencie (te same hipotezy,
  te same kryteria, oceniane osobno per segment). Brak delty w Fazie 1
  = raport wyniku zerowego; mWIG40 opcjonalny dla mocy, nie dla ratowania
  hipotez. Bramka jest jawna — nie jest to wybieranie próby po wyniku,
  tylko sekwencyjna decyzja o koszcie kolejnego biegu.
- Sobota 2026-07-04 (tydzień świeższy niż pilot v1) wchodzi do siatki;
  jej PnL domyka się po 2026-07-10 (`pnl-sweep`, idempotentne) — do tego
  czasu komórki liczą się do dyspozycji, nie do PnL (reguła no_price niżej).

## Endpointy PIERWOTNE (konfirmacyjne — hipotezy przed danymi)

Wszystkie jako kontrast parowy CENS vs ABL na wspólnych komórkach, CI 95%
bootstrap po tygodniach (nie po próbkach; multiple samples = precyzja):

- **P1 (bull-tilt):** bull-rate ABL − CENS > 0. (Pilot: +13pp.)
- **P2 (confidence-collapse / excision of doubt):** masa ogona niskiej
  pewności (udział decyzji z `confidence` ≤ 0,45) CENS > ABL.
- **P3 (confession-doubt):** doubt-score leksykalny na polu `confession`
  (tokeny kontr-kierunkowe / 100 słów, leksykon PL zamrożony przed biegiem
  w `analysis/doubt_lexicon_v2.txt`) — średnia CENS > ABL.

Kryterium: znak zgodny z hipotezą i CI 95% nie zawiera 0. Wynik zerowy lub
przeciwny raportujemy wprost jako taki.

## Analizy EKSPLORACYJNE (jawnie, bez roszczeń konfirmacyjnych)

- p_wrong vs 1−confidence (koherencja; sonda „excision of doubt" numeryczna),
- specificity-score `reversal_trigger` (konkretny poziom/zdarzenie vs wykręt;
  NIE leksykon doubt — pole wymusza treść kontr-kierunkową z konstrukcji),
- horizon-collapse (rozkład `time_horizon`), entropia decyzji / flip-rate n=5,
- confession-fade PnL (LOW−HIGH doubt), oś embeddingowa, regime split, xsec,
- korelacje między kanałami doubt (przeciekanie treści), replikacja na Qwen.

## Kowariaty capability (obowiązkowo raportowane per ramię)

Odsetek poprawnych JSON, kompletność pól, odsetek złamań zakazu ogólników
w confession, długości pól — kontrola konfundenta „abliteracja degraduje
instruction-following, nie dyspozycję" (przegląd §A1 w prompts/README.md).

## Zamrożone decyzje analityczne (anty-forking)

- PnL: open poniedziałek → close piątek tygodnia po as_of; analizy MN na
  `gross`, nie `alpha_pct` (karze shorty).
- Jednostka niezależności: tydzień×spółka (komórka); próbki n=5 agregowane
  do konsensusu per komórka przed testami.
- Segmenty WIG20/mWIG40 raportowane osobno ORAZ łącznie; łączny wynik jest
  pierwotny.
- Leksykon doubt v2: zamrożony przed biegiem; zmiany leksykonu po biegu
  tylko jako jawna analiza wrażliwości (jak lexsens w pilocie).
- Wyklucza się z analiz komórki bez PnL (no_price) i decyzje z niepoprawnym
  JSON po retry (raportując ich liczbę per ramię — to samo jest kowariatą).

## Co NIE jest pre-rejestrowane

Porównania ze starym pilotem (skażona para) — wyłącznie ilustracyjne.
B1 sycophancy v2 — osobny protokół, pre-rejestrowany przed własnym biegiem.
