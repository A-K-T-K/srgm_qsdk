# Defect-fix growth in open-source quantum SDKs

Code and data for mining and manual validation of defect-fix commits in Qiskit, Cirq,
PennyLane and Q#, mapped to release milestones and fitted with Goel-Okumoto and Yamada delayed S-shaped
NHPP models.

## Layout

| Path | Contents |
|---|---|
| `01-phase_1_2.py` | Phase 1 pre-filter and Phase 2 sampling (see the note below) |
| `generate_results.py` | Fits the models, computes all statistics, writes CSVs and `results/tables.md` |
| `backport_rates.py` | Separate-release-line backport detection (called by `generate_results.py` for the summary; run alone for the per-fix CSVs) |
| `plot_results.py` | Draws the figures from the CSVs; does no fitting |
| `PHASE2_INCLUSION_EXCLUSION_RULES.md` | Written annotation protocol |
| `output/` | Phase 1 and 2 data: candidate pool, tags, annotated samples, frozen-HEAD manifest |
| `data/api_cache.json` | Saved GitHub issue and collaborator metadata used by the pre-filter and `coverage()` |
| `data/repos/<repository>/` | Full clones, not tracked (see Setup) |
| `results/` | Generated: `tables.md`, `data/*.csv`, `figs/*.pdf`, backport CSVs |

## Setup

Python 3.13 with `numpy`, `pandas`, `scipy` and `matplotlib`; `git` on the PATH. The mining script
`01-phase_1_2.py` also needs `requests`.

Clone the four repositories into `data/repos/` (`qiskit`, `cirq`, `pennylane`, `qsharp`) with full history, and
check out the commits in `output/reproducibility_manifest.csv`:

```
git clone https://github.com/Qiskit/qiskit.git data/repos/qiskit
git -C data/repos/qiskit checkout <frozen_mining_head_sha>
```

`generate_results.py` refuses to run if a clone's HEAD differs from the manifest or the clone is shallow.

## Reproducing the results

```
python generate_results.py   # fits, tables.md, results/data/*.csv  (about 80 s)
python plot_results.py       # results/figs/*.pdf
python backport_rates.py     # results/backport_details.csv and backport_summary.csv (per-fix evidence)
```

`generate_results.py` also re-draws both annotation samples with the recorded seeds (42, 43) and fails if they
differ from the annotated files, so the annotations cannot drift from the sampling frame.

### Files written to `results/data/`

`fits.csv` (all fitted parameters, intervals, covariances, AIC), `series.csv` (per-release counts and residuals),
`repositories.csv`, `inclusion_shares.csv`, `exclusion_shares.csv`, `mining.csv`, `windows.csv`, `clocks.csv`,
`backports.csv`, `coverage.csv` (including coverage of each window's first release interval and subject-only
sampled messages), `start_sensitivity.csv` (AIC selection with the first release interval dropped),
`label_counts.csv`, `residual_flagged.csv`, `scalars.csv` (including the pool-size-weighted pooled yield).
`mining.csv` also carries per-repository inclusion-category agreement.

## Note on the Phase 1 script

`01-phase_1_2.py` implements the documented Phase 1 rule and the Phase 2 sampling, but it does not regenerate
the saved candidate pool in `output/phase1_candidate_pool.csv`. The saved pool came from resumed runs of an
earlier implementation, which stored only subject lines for most Qiskit commits, and the script derives its
own start date per repository, which differs from where the saved pools begin. The saved pool is the
sampling frame all annotations and results use, and the release windows (`STUDY` in `generate_results.py`)
start where it begins. `coverage()` in `generate_results.py` quantifies the difference
(`results/data/coverage.csv`).

To run the script yourself, set a GitHub token in the `GITHUB_TOKEN` variable; without one the API is limited
to 60 requests per hour. Do not commit the token. The script rewrites files in `output/`.

`output/phase3_inclusion_thresholds.csv` is a Phase 1 pre-flight count of pool size and tags. It is not the
modelling threshold; that check (at least 30 validated fixes and 5 milestones) is in `generate_results.py`.

## Annotation

Defect labels were assigned by hand by a single annotator, and a 20% subset was relabelled for the repeat audit.
The rules are in `PHASE2_INCLUSION_EXCLUSION_RULES.md`. Nothing in this repository relabels commits.
