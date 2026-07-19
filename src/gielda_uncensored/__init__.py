"""gielda-research-uncensored — replay warstwy decyzyjnej (manager+trader) gielda-agents
na własnym modelu (gemma4 @ gb10) + PnL benchmark censored vs uncensored.

gielda-agents i jego baza są READ-ONLY: reużywamy kod (import) i czytamy dane (SELECT),
nigdy nie modyfikujemy. Wyniki lądują w lokalnym SQLite.
"""

__version__ = "0.1.0"
