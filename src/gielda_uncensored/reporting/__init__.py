"""Warstwa raportowa: backtest portfelowy, wskaźniki hybrydowe, wykresy SVG, raport HTML.

Cała warstwa jest CZYSTO ODCZYTOWA względem SQLite (SELECT) — liczy na tym, co już zebrał sweep.
Zero nowych biegów LLM, zero zapisu do bazy gielda-agents.
"""
