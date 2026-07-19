# Abliteration Is Not a Scalpel

Off-target effects of refusal removal on decision **disposition**, across two
model families — the data, code, and paper.

> **Abliteration** (orthogonalizing a model's refusal direction out of its
> weights) is the standard recipe behind "uncensored" open-weight models. It is
> sold as surgical: remove refusals, change nothing else. On **21,600
> preregistered decisions** under uncertainty — base vs. abliterated, identical
> frozen evidence, two Mixture-of-Experts families — the surgery is not clean.
> Abliterated models become systematically **more optimistic** (+12.2 pp Gemma,
> +7.4 pp Qwen), **justify themselves at greater length**, and use **fewer
> uncertainty words** — in both families. The *same* edit moves expressed
> confidence in **opposite** directions (Gemma down, Qwen up). No arm gains
> economic skill. An "uncensored" model is not the base model minus refusals; it
> is a measurably different decision-maker.

**Paper:** [`final_research/paper/PAPER.md`](final_research/paper/PAPER.md)
· PDF: [`final_research/paper/PAPER_draft.pdf`](final_research/paper/PAPER_draft.pdf)
· Dataset DOI: [10.5281/zenodo.21314839](https://doi.org/10.5281/zenodo.21314839)
· arXiv: *(to be added)*

Author: **Aleksander Fafuła, PhD** — independent researcher ·
[funduszai.pl](https://funduszai.pl) · aleksander@fafula.com

## What is in this repository

| Path | Contents |
|---|---|
| `final_research/paper/` | The paper (Markdown, PDF, LaTeX/arXiv source, figures, tables) |
| `final_research/data/public_snapshot.db` | De-identified snapshot of all 21,600 decision outputs + PnL marks (the only data needed to reproduce every number) |
| `final_research/analysis/` | Analysis scripts — pure Python 3 stdlib, deterministic, read-only |
| `final_research/PREREGISTRATION-v2.md` | Preregistration (frozen before the grid) |
| `final_research/prompts/` | The frozen decision prompt (sha-256 recorded) |
| `final_research/PROVENANCE.md` | Weight/config/tokenizer/template audit of all four checkpoints |
| `final_research/faq.md` | Plain-language answers to common objections |
| `src/gielda_uncensored/` | The generation harness (needs the proprietary upstream — see below) |

## Reproduce every table, figure, and CI

The analysis scripts are **pure Python 3 standard library** (tested on 3.12,
needs ≥3.11). No dependencies, no install:

```bash
export RESEARCH_DB="$PWD/final_research/data/public_snapshot.db"

python3 final_research/analysis/tables.py               # paper/tables/*.md
python3 final_research/analysis/figures.py              # paper/figures/*.svg
python3 final_research/analysis/capability_bootstrap.py # deltas + weeks-clustered bootstrap CIs
python3 final_research/analysis/p2_p3_prereg.py         # P2/P3 preregistration accounting
```

`RESEARCH_DB` points the scripts at the public snapshot; without it they look for
the author's private `final.db`, which is not distributed. Every number in the
paper regenerates from the public snapshot alone — the results are deterministic
(the bootstrap is seeded), so repeated runs are byte-identical.

## What is *not* included (and why)

The proprietary **upstream** consumed to produce each decision — analyst briefs,
expert-debate transcripts, and the production multi-agent pipeline of
funduszai.pl / gielda-agents — is **not** released. The public snapshot contains
only the model's *outputs*, from which the entire analysis regenerates; it holds
no upstream text (leak-checked).

Consequently the **generation harness** in `src/` depends on the private
gielda-agents package (`pyproject.toml` has a local path dependency) and **will
not `uv sync` for outside users** — that is expected. Reproducing the *analysis*
needs only the snapshot and stdlib Python; reproducing the *generation* step
would need the proprietary pipeline. See the paper, Appendix A.

## License

- **Code** (`src/`, `final_research/analysis/`, scripts): MIT — see [`LICENSE`](LICENSE).
- **Data & paper** (`public_snapshot.db`, `final_research/paper/`): CC BY 4.0 —
  see [`LICENSE-DATA.md`](LICENSE-DATA.md).

## Citation

```bibtex
@misc{fafula2026abliteration,
  title  = {Abliteration Is Not a Scalpel: Off-Target Effects of Refusal
            Removal on Decision Disposition Across Model Families},
  author = {Fafu{\l}a, Aleksander},
  year   = {2026},
  note   = {Preprint. Data and code: this repository.}
}

@dataset{fafula2026abliteration_data,
  title     = {Abliteration Is Not a Scalpel --- decision-level dataset
               (21,600 LLM trading decisions, base vs abliterated,
               Gemma \& Qwen)},
  author    = {Fafu{\l}a, Aleksander},
  year      = {2026},
  publisher = {Zenodo},
  version   = {1.0},
  doi       = {10.5281/zenodo.21314839},
  url       = {https://doi.org/10.5281/zenodo.21314839}
}
```
