# Data & paper license — CC BY 4.0

The **research data** and the **paper** in this repository are licensed under the
**Creative Commons Attribution 4.0 International License (CC BY 4.0)**.

This covers, specifically:

- `final_research/data/public_snapshot.db` — the de-identified snapshot of all
  21,600 model decision outputs (and their PnL marks).
- `final_research/paper/PAPER.md`, the compiled PDF, the LaTeX source under
  `final_research/paper/arxiv/`, and all figures in
  `final_research/paper/figures/`.

You are free to **share** and **adapt** this material for any purpose, including
commercially, provided you give **appropriate credit**, link to the license, and
indicate if changes were made.

Full license text: <https://creativecommons.org/licenses/by/4.0/legalcode>
Human-readable summary: <https://creativecommons.org/licenses/by/4.0/>

## How to attribute

> Fafuła, A. (2026). *Abliteration Is Not a Scalpel: Off-Target Effects of
> Refusal Removal on Decision Disposition Across Model Families.* Data and paper
> released under CC BY 4.0. Dataset: <https://doi.org/10.5281/zenodo.21314839>.

The dataset (snapshot + analysis bundle) is permanently archived on Zenodo:
**DOI [10.5281/zenodo.21314839](https://doi.org/10.5281/zenodo.21314839)**
(all versions: 10.5281/zenodo.21314838). (Add the arXiv ID once assigned.)

## Not covered by this license

The proprietary **upstream** consumed to generate the decisions — analyst
briefs, expert-debate transcripts, and the production multi-agent pipeline of
funduszai.pl / gielda-agents — is **not** included in this repository and is
**not** released. The public snapshot contains only the model's *outputs*, from
which every table, figure, and confidence interval in the paper regenerates; it
does not contain the upstream text. Reproducing the *analysis* needs only this
repository; reproducing the *generation* step would need the proprietary
pipeline. See the paper, Appendix A.

The **source code** is separately licensed under MIT — see `LICENSE`.
