# FAQ — odpowiedzi na przewidywalne zarzuty (wewnętrzne, PL)

Ściąga do dyskusji (HN/X/recenzje/media). Wersja kanoniczna po angielsku:
`paper/PAPER.md` §5.4 „Anticipated objections". Wszystkie liczby policzone
z `data/final.db` (4 czyste kohorty, 21 600 decyzji), stan 2026-07-07.

---

## 1. „No ale co porównujesz do czego?"

Model bazowy vs **jego własna** abliterowana pochodna, na bajt-w-bajt
identycznych wejściach: te same 60 spółek, te same 18 sobót, te same mrożone
briefy analityków i debaty, ten sam prompt (hash `305718d8…`), ten sam stack
serwujący, n=5 na komórkę. Każda liczba w paperze to kontrast within-pair.
**Nigdy** Gemma vs Qwen wprost, **nigdy** model vs rynek. Wszystko co wspólne
dla obu ramion — rynek, newsy, prompt, pipeline — skraca się w różnicy.

## 2. „Była hossa, to jasne że model jest byczy."

Oba ramiona widziały DOKŁADNIE ten sam rynek. Hossa może podnieść *poziom*
optymizmu obu ramion; nie może otworzyć systematycznej *luki* między dwoma
modelami czytającymi te same dane. I luka nie jest artefaktem hossy:

- delta optymizmu jest dodatnia **także w tygodniach spadkowych**, w obu
  rodzinach: Gemma +13,8 pp (UP) / **+9,0 pp (DOWN)**; Qwen +5,9 pp (UP) /
  **+10,3 pp (DOWN)** — u Qwena w spadkach WIĘKSZA niż we wzrostach;
- dodatnia w **16 z 18** pojedynczych tygodni per rodzina; dwa wyjątki to
  −2,0 i −2,3 pp (Gemma 04-11 i 07-04; Qwen 03-07 i 03-21=0,0);
- replikuje się na dwóch segmentach (WIG20 +14,3 / mWIG40 +11,1 pp Gemma;
  +5,7 / +8,2 pp Qwen).

## 3. „Przy innym trendzie wynik się zmieni."

Od reżimu zależy *poziom* („X% byczych") — i my sami to pokazujemy
(Qwen-base to silny regime-follower). Claim papera to *delta*: ramię
abliterowane siedzi nad własną bazą na identycznych danych, w obu reżimach,
na obu segmentach. Tam gdzie reżim naprawdę gryzie — wynik tradingowy —
paper mówi to wprost: pozorna przewaga PnL ramion abliterowanych to beta,
która w bessie odwróci znak (§4.6, regime split: Gemma WIG20 UP +0,57% vs
base −0,04%; DOWN −0,61% vs +0,05%). **To jest nasz wynik, nie nasza dziura.**

## 4. „18 tygodni to za mało."

Na wnioski ekonomiczne — tak, dlatego jedyny claim ekonomiczny w paperze to
NULL (nikt nie ma skillu). Delty dyspozycji stoją na 21 600 decyzjach,
z niepewnością liczoną konserwatywnie: bootstrap klastrowany po tygodniach
(dopuszcza dowolną korelację wewnątrz tygodnia). Dłuższe okno zwęziłoby CI,
nie wyczarowało efektów — widać je tydzień po tygodniu.

## 5. „Tygodniowy kierunek akcji to rzut monetą, więc zadanie bez sensu."

Odwrotnie: beznadziejność predykcji to **design, nie wada**. Skoro żaden
model nie może wygrać umiejętnością, każda stabilna różnica między ramionami
musi być *inklinacją*, nie inteligencją. Finanse są tu instrumentem:
generatorem powtarzalnych, konsekwentnych decyzji pod niepewnością
z datowaną, nieprzeciekalną prawdą gruntową. Sam system produkcyjny nazywa
tygodniowy kierunek „close to a coin flip".

## 6. „Może huihui po prostu zepsuł modele."

Uszkodzenie przewiduje szum i połamane instruction-following. Mamy odwrotność:
100% poprawnych JSON wszędzie, kowariaty capability zachowane (u Qwena
IDENTYCZNE między ramionami, a jego delty dyspozycji są największe),
a przesunięcia są spójnie *skierowane* w dwóch rodzinach i dwóch segmentach.
Struktura, nie uszkodzenie. Jedyny ubytek: specyficzność `reversal_trigger`
Gemmy-abl 95%→85% — odnotowany w limitations, za mały żeby unieść główne efekty.

## 7. (bonus) „Skąd wiadomo, że to nie kwantyzacja/szablon/serving?"

Bo dokładnie NA TYM się przejechaliśmy i to naprawiliśmy: pilot na parze
z niezgodnymi kwantyzatorami dał efektowny wynik („excision of doubt"),
który na czystej parze BF16 się odwrócił; audyt tokenowy złapał drugi kanał
(stary chat_template w repo huihui renderujący system prompt jako repr listy)
— skażone ramię wyrzucone i zebrane od nowa. Werdykty: `PROVENANCE.md`.
W finale: oficjalne checkpointy BF16, jeden autor abliteracji, identyczny
vLLM, render zweryfikowany co do tokena. Dwa złapane kanały skażenia to
sam w sobie wynik metodologiczny papera (§5.3).

## Kontekst rynkowy okna (do cytowania z głowy)

- Okno: 18 sobót 2026-03-07…2026-07-04; rozliczone ekonomicznie **wszystkie
  18 tygodni** (07-04 domknięty 2026-07-11, bench +2,46% → UP).
- Benchmark WIG20: **+15,4%** skumulowane w 18 tygodniach; **12 UP / 6 DOWN**.
- Benchmark dla wszystkich decyzji (także mWIG40) = indeks WIG20
  (`bench_key='wig20'`); dla regime-splitu OK, segmentowy benchmark mWIG40
  = opcjonalny robustness-upgrade, gdy będzie seria indeksu w upstreamie.
