"""Statystyka pomocnicza: moving-block bootstrap dla krótkich serii tygodniowych.

n=16 tygodni to za mało na asymptotykę — każdy headline (średni zwrot, alfa, diff-in-diff)
musi mieć słupki błędu. Moving-block bootstrap (bloki zachodzące) zachowuje krótką
autokorelację tygodni; dla serii bez struktury czasowej użyj block=1 (bootstrap iid).
Czyste funkcje, deterministyczne przy ustalonym seed.
"""
from __future__ import annotations

import random
from typing import Callable, Sequence


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def total_compounded(xs: Sequence[float]) -> float:
    eq = 1.0
    for x in xs:
        eq *= 1 + x
    return eq - 1


def block_bootstrap_ci(
    series: Sequence[float],
    agg: Callable[[Sequence[float]], float] = mean,
    *,
    n_boot: int = 5000,
    block: int = 4,
    seed: int = 1337,
    ci: float = 0.95,
) -> tuple[float, float, float]:
    """95% CI statystyki `agg` przez moving-block bootstrap. Zwraca (lo, mediana, hi).

    Bloki zachodzące długości `block` losowane ze zwracaniem i sklejane do długości
    serii (ostatni blok przycięty). Gdy seria krótsza niż blok — blok skracany.
    """
    n = len(series)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    b = max(1, min(block, n))
    rng = random.Random(seed)
    max_start = n - b
    stats: list[float] = []
    for _ in range(n_boot):
        out: list[float] = []
        while len(out) < n:
            s = rng.randint(0, max_start)
            out.extend(series[s:s + b])
        stats.append(agg(out[:n]))
    stats.sort()
    lo_q = (1 - ci) / 2

    def q(p: float) -> float:
        i = min(len(stats) - 1, max(0, int(p * len(stats))))
        return stats[i]

    return q(lo_q), q(0.5), q(1 - lo_q)


def diff_ci_two_groups(
    a: Sequence[float],
    b: Sequence[float],
    *,
    n_boot: int = 5000,
    seed: int = 1337,
    ci: float = 0.95,
) -> tuple[float, float, float]:
    """CI różnicy średnich mean(a) − mean(b), bootstrap iid osobno w każdej grupie.

    Do porównań między reżimami (up-weeks vs down-weeks) — grupy nie są ciągłe w czasie,
    więc bloki nie mają sensu; tygodniowe zwroty mają niską autokorelację (caveat w raporcie).
    """
    if not a or not b:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(n_boot):
        ra = [a[rng.randint(0, len(a) - 1)] for _ in a]
        rb = [b[rng.randint(0, len(b) - 1)] for _ in b]
        diffs.append(mean(ra) - mean(rb))
    diffs.sort()
    lo_q = (1 - ci) / 2

    def q(p: float) -> float:
        i = min(len(diffs) - 1, max(0, int(p * len(diffs))))
        return diffs[i]

    return q(lo_q), q(0.5), q(1 - lo_q)
