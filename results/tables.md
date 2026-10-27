### Saved candidate pool compared with the documented Phase 1 rule (coverage)

| SDK | Saved pool N | Reference rule | Both | Missing, earlier | Missing, in range | of which label-only | Saved only | Subject-only messages |
|---|---|---|---|---|---|---|---|---|
| Qiskit | 259 | 685 | 259 | 14 | 412 | 3 | 0 | 205 |
| Cirq | 1,079 | 1,117 | 1,079 | 32 | 6 | 6 | 0 | 0 |
| PennyLane | 1,162 | 1,209 | 1,162 | 45 | 2 | 2 | 0 | 0 |
| Q# | 446 | 577 | 446 | 131 | 0 | 0 | 0 | 0 |


### Saved-pool coverage of each window's first release interval (coverage_start)

| SDK | Start milestone | Pool begins after start (days) | Pool candidates at start | Missing, earlier | of which at start | Sampled | Sampled subject-only messages |
|---|---|---|---|---|---|---|---|
| Qiskit | 1.2.0rc1 | -78.8 | 22 | 14 | 14 | 155 | 122 |
| Cirq | v0.1 | 1.3 | 0 | 32 | 32 | 284 | 0 |
| PennyLane | v0.25.0 | -10.2 | 5 | 45 | 45 | 289 | 0 |
| Q# | v1.0.10-rc | 1.8 | 0 | 131 | 130 | 207 | 0 |


### Candidate extraction, inclusion yield, and binary repeat-audit agreement (data_mining)

| SDK | N | n | n/N | Re-audit | Yield k/n | p_o | p_e | κ |
|---|---|---|---|---|---|---|---|---|
| Qiskit | 259 | 155 | 59.8% | 31 | 58.06% | 100.00% | 50.05% | 1.000 |
| Cirq | 1,079 | 284 | 26.3% | 57 | 26.76% | 92.98% | 55.56% | 0.842 |
| PennyLane | 1,162 | 289 | 24.9% | 58 | 25.95% | 91.38% | 56.54% | 0.802 |
| Q# | 446 | 207 | 46.4% | 42 | 55.07% | 95.24% | 52.83% | 0.899 |
| Pooled | 2,946 | 935 | 31.7% | 188 | 37.97% | 94.15% | 51.36% | 0.880 |


### Defect taxonomy distribution among included fixes (defect_taxonomy)

| SDK | Algorithmic logic | Incorrect output | Runtime failure | Semantic violation | Regression |
|---|---|---|---|---|---|
| Qiskit | 52.22% | 26.67% | 7.78% | 12.22% | 1.11% |
| Cirq | 35.53% | 27.63% | 25.00% | 11.84% | 0.00% |
| PennyLane | 29.33% | 28.00% | 22.67% | 20.00% | 0.00% |
| Q# | 49.12% | 18.42% | 14.04% | 13.16% | 5.26% |


### Maintenance taxonomy among explicitly excluded candidates (exclusion_taxonomy)

| SDK | Build | CI | Dependency | Docs | Nonfunctional | Performance | Refactor | Style | Tests |
|---|---|---|---|---|---|---|---|---|---|
| Qiskit | 6.35% | 1.59% | 3.17% | 36.51% | 17.46% | 4.76% | 0.00% | 14.29% | 15.87% |
| Cirq | 4.83% | 9.18% | 10.14% | 24.15% | 22.22% | 0.97% | 11.59% | 8.70% | 8.21% |
| PennyLane | 1.00% | 3.50% | 5.50% | 13.50% | 39.00% | 5.50% | 19.00% | 1.50% | 11.50% |
| Q# | 4.49% | 0.00% | 17.98% | 16.85% | 32.58% | 2.25% | 11.24% | 4.49% | 10.11% |


### Release-milestone windows and candidate exposure (demographics)

| SDK | Window | K | Aliases collapsed | Milestones with fixes | Days to cutoff | Candidates | Included fixes | Median interval days (IQR) | Included per 1,000 sampled |
|---|---|---|---|---|---|---|---|---|---|
| Qiskit | 1.2.0rc1 to 2.3.0rc1 | 38 | 0 | 8 | 517 | 259 | 90 | 9.0 (10.2) | 580.6 |
| Cirq | v0.1 to v1.6.1 | 31 | 0 | 20 | 2,815 | 1,079 | 76 | 77.5 (114.6) | 267.6 |
| PennyLane | v0.25.0 to v0.43.2 | 38 | 6 | 19 | 1,234 | 1,162 | 75 | 32.3 (47.1) | 259.5 |
| Q# | v1.0.10-rc to v1.23.0 | 26 | 0 | 23 | 835 | 446 | 114 | 31.1 (9.1) | 550.7 |


### Separate-release-line backports with recorded copy evidence (backports)

| SDK | Validated | Mapped denominator | Backported | PR reference only | Rate | 95% Wilson CI |
|---|---|---|---|---|---|---|
| Qiskit | 90 | 90 | 70 | 1 | 77.78% | [68.15, 85.13]% |
| Cirq | 76 | 76 | 1 | 0 | 1.32% | [0.23, 7.08]% |
| PennyLane | 75 | 75 | 0 | 0 | 0.00% | [0.00, 4.87]% |
| Q# | 114 | 114 | 1 | 0 | 0.88% | [0.16, 4.80]% |


### Constrained grouped-Poisson model fits and AIC selection (srgm_fits)

| SDK | Model | a [95% CI] | b [95% CI] | 1/b | Sat. | ln L | AIC | ΔAIC | w_i | RMSE | MAE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Qiskit | GO (selected)† | 10000.00 unavailable | 0.000238 unavailable | — | 0.90% | -172.65 | 349.30 | 0.00 | 1.0000 | 6.56 | 5.10 |
| Qiskit | Yamada | 143.61 [87.8, 199.4] | 0.055915 [0.034510, 0.077321] | 17.88 | 62.67% | -186.93 | 377.85 | 28.55 | 0.0000 | 7.40 | 6.17 |
| Cirq | GO (selected)‡ | 3019.29 [0.0, 93968.8] | 0.000822 [0.000000, 0.025912] | — | 2.52% | -85.50 | 175.00 | 0.00 | 0.9319 | 2.56 | 1.98 |
| Cirq | Yamada | 100.69 [69.6, 131.8] | 0.087709 [0.059004, 0.116415] | 11.40 | 75.48% | -88.12 | 180.23 | 5.23 | 0.0681 | 3.09 | 2.59 |
| PennyLane | GO† | 10000.00 unavailable | 0.000198 unavailable | — | 0.75% | -93.56 | 191.12 | 10.04 | 0.0065 | 6.64 | 5.38 |
| PennyLane | Yamada (selected) | 133.20 [65.8, 200.6] | 0.049706 [0.026229, 0.073184] | 20.12 | 56.31% | -88.54 | 181.08 | 0.00 | 0.9935 | 3.02 | 2.51 |
| Q# | GO (selected) | 212.34 [87.8, 336.9] | 0.029606 [0.004768, 0.054445] | — | 53.69% | -78.78 | 161.57 | 0.00 | 0.9998 | 4.08 | 3.44 |
| Q# | Yamada | 128.93 [103.2, 154.6] | 0.142481 [0.113585, 0.171377] | 7.02 | 88.42% | -87.14 | 178.28 | 16.72 | 0.0002 | 8.01 | 6.67 |


### Exposure-clock sensitivity with the same grouped-likelihood estimator (sensitivity_clocks)

| SDK | Model | b release | b candidate commit | b day | Release rank | Commit rank | Day rank |
|---|---|---|---|---|---|---|---|
| Qiskit | GO | 0.00023783† | 0.00112989 | 0.00001818† | 4 | 2 | 4 |
| Cirq | GO | 0.00082238 | 0.00059854 | 0.00027361 | 3 | 4 | 3 |
| PennyLane | Yamada | 0.04970633 | 0.00115021 | 0.00201027 | 1 | 1 | 1 |
| Q# | GO | 0.02960649 | 0.00066315 | 0.00099010 | 2 | 3 | 2 |


### AIC selection with the first release interval dropped (start_sensitivity)

| SDK | Dropped milestone | Fixes dropped | K | AIC GO | AIC Yamada | Selected | ΔAIC | Same as release-index selection |
|---|---|---|---|---|---|---|---|---|
| Qiskit | 1.2.0rc1 | 5 | 37 | 343.55† | 355.15 | GO | 11.60 | yes |
| Cirq | v0.1 | 0 | 30 | 169.70 | 191.97 | GO | 22.28 | yes |
| PennyLane | v0.25.0 | 1 | 37 | 188.52† | 175.87 | Yamada | 12.65 | yes |
| Q# | v1.0.10-rc | 0 | 25 | 147.95 | 205.77 | GO | 57.83 | yes |


### Repeat-audit inclusion-category agreement (category_agreement)

| SDK | Included in both passes | Agreement | κ |
|---|---|---|---|
| Qiskit | 15 | 66.67% | — |
| Cirq | 17 | 76.47% | — |
| PennyLane | 16 | 50.00% | — |
| Q# | 25 | 76.00% | — |
| Pooled | 73 | 68.49% | 0.581 |


### Pooled inclusion yield (pooled_yield)

| Sample-pooled | Weighted by pool size N |
|---|---|
| 37.97% | 33.48% |


### First-pass annotation counts (label_counts)

| Label | Count |
|---|---|
| exclude | 559 |
| include | 355 |
| uncertain | 21 |


### Spearman correlations between exposure clocks (clock_correlations)

| Clock pair | ρ |
|---|---|
| release_commit | 0.40 |
| release_day | 1.00 |
| commit_day | 0.40 |


### Cumulative residual threshold counts (residual_summary)

| Milestones | \|r_k\|≥1.96 |
|---|---|
| 133 | 6 |


### Cumulative residual threshold exceedances (residual_flags)

| SDK | Milestone | Index | r_k |
|---|---|---|---|
| Qiskit | 1.4.0 | 16 | -1.98 |
| Qiskit | 1.4.1 | 17 | -2.34 |
| Cirq | v0.8.2 | 11 | -2.03 |
| Cirq | v0.14.0 | 20 | 2.63 |
| PennyLane | v0.33.1 | 16 | -2.07 |
| Q# | v1.16.0 | 19 | -2.05 |

