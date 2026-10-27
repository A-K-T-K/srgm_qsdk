# Phase 2 — Manual Validation: Inclusion / Exclusion Rules

Source of truth: this document and `output/phase2_annotation_guide.csv` (allowed label values).
This document is the single reference used to annotate `output/phase2_manual_validation_sample.csv`
row-by-row (`annotator_label`, `inclusion_rule_triggered`, `exclusion_rule_triggered`, `annotation_notes`).

## Operational definition of a defect fix

> A true quantum defect fix is a code modification correcting an **observable runtime failure**,
> an **incorrect simulation or physical output**, a **semantic violation**, or a **core algorithmic
> logic flaw**.

Only commits meeting this definition are labeled `include`. Everything else — even if it matches a
"bug"/"fix" keyword — is `exclude` unless it demonstrably changes program behavior to correct a
wrong result.

## Labels

| `annotator_label` | Meaning |
|---|---|
| `include` | Commit is a true defect fix per the definition above. |
| `exclude` | Commit is a non-defect maintenance change. |
| `uncertain` | Commit message/diff context is insufficient to decide with confidence; excluded from defect counts and from the exclusion taxonomy (no adjudication was performed). |

## Inclusion rules (`inclusion_rule_triggered`)

Applied only when `annotator_label = include`. Pick the single rule that best matches the primary
reason for inclusion.

| Rule | Trigger |
|---|---|
| `incorrect_output` | Fixes a case where the program produced a wrong numeric/simulation/physical result (e.g., wrong statevector, wrong expectation value, wrong gate matrix, wrong measurement counts). |
| `runtime_failure` | Fixes a crash, exception, hang, deadlock, or unhandled error during execution. |
| `semantic_violation` | Fixes behavior that violates a documented/intended API contract or physical/mathematical semantics (e.g., wrong qubit ordering, incorrect unitarity, violated commutation rule) without necessarily crashing. |
| `logic_fix` | Fixes an internal algorithmic/control-flow logic error (off-by-one, wrong condition, incorrect state transition) not better captured by the three rules above. |
| `regression_fix` | Fixes behavior that used to work correctly and broke due to a prior change (explicitly referenced as a regression). |

## Exclusion rules (`exclusion_rule_triggered`)

Applied only when `annotator_label = exclude`. Pick the single rule that best matches the primary
reason for exclusion.

| Rule | Trigger |
|---|---|
| `refactor` | Structural code reorganization with no behavior change (renames, moves, extraction, deduplication). |
| `docs` | Documentation-only changes (docstrings, README, tutorials, comments). |
| `test_only` | Changes confined to test files/fixtures (new tests, test flakiness fixes, test data) with no production code fix. |
| `dependency_update` | Version bumps or changes to third-party dependencies/requirements, not accompanied by a behavior fix in this repo's own code. |
| `performance_only` | Optimization changes (speed/memory) that do not correct incorrect output or a failure. |
| `style_only` | Formatting, linting, naming/style-only edits with no behavior change. |
| `build_only` | Build system, packaging, or release-tooling changes. |
| `ci_only` | Continuous-integration configuration/workflow changes. |
| `nonfunctional` | Any other non-functional maintenance change not captured by the categories above (e.g., typo in a log message, license header, changelog entry). |

## Decision procedure per commit

1. Read the full commit message (and, where the message is ambiguous, the linked issue/PR title and
   labels already extracted in Phase 1: `has_bug_keyword_in_message`, `has_bug_label`,
   `has_explicit_bug_label`). For Qiskit the saved message is usually the subject line only
   (122 of 155 sampled messages omit a longer body), so Qiskit labels rest on less evidence.
2. Ask: *does this commit change program behavior to correct a wrong result, a crash, a semantic
   violation, or an algorithmic logic error?*
   - **Yes, clearly** → `include`, choose the matching `inclusion_rule_triggered`.
   - **No, clearly** → `exclude`, choose the matching `exclusion_rule_triggered`.
   - **Not enough information in the message alone** → `uncertain`, leave rule fields blank, note
     what additional context (diff, issue body) would resolve it.
3. A commit matching a bug-ish keyword (`fix`, `bug`, `error`, …) is **not** automatically
   `include` — keyword presence is only the Phase 1 recall filter. Phase 2 requires reading intent:
   e.g. "fix typo in docstring" → `exclude` / `docs`; "fix test flakiness" → `exclude` / `test_only`;
   "fix CI pipeline" → `exclude` / `ci_only`.
4. A commit with no bug-ish keyword can still be `include` if the message describes correcting wrong
   behavior in substance (e.g., "correct qubit ordering in `apply_gate`").
5. Backported fixes (`is_backported = 1`) are labeled using the same criteria as their primary
   commit; backport status affects release-sequence mapping only, not the include/exclude decision.
6. Record a one-line rationale in `annotation_notes` for every row, especially `uncertain` ones.

## Sampling / audit parameters (for context, not decided per-row)

- Sample drawn via uniform random sampling without replacement, seed-fixed, sized with the finite
  population correction formula ($z=1.96$, $e=0.05$, $p=0.5$).
- Intended procedure: after the full sample is labeled and a 72-hour cooling period, a blind
  second-pass audit by the same annotator is run on a seed-fixed random 20% subset
  (`output/phase2_second_pass_audit_sample.csv`, ceil(0.2n) per repository) to measure intra-rater
  consistency before the label set is locked.
- Recorded evidence: first-pass rows carry no timestamps and `cooling_period_completed = 0`; the
  second-pass rows carry `cooling_period_completed = 1` and a single audit date (2026-09-13). The
  cooling period and blinding therefore cannot be independently established from the files.
