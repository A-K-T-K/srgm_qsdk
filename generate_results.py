#!/usr/bin/env python3
"""Generate the result figures and tables from the annotated phase_1_2.py samples.

Reads (never modifies), relative to this folder:
  output/phase2_manual_validation_sample.csv  Phase 2 sample with first-pass labels
  output/phase2_second_pass_audit_sample.csv  Phase 2 audit subset with second-pass labels
  output/phase1_candidate_pool.csv            Phase 1 sampling frame (N, commit-exposure clock)
  output/phase1_tags.csv                      Phase 1 tag names
  output/reproducibility_manifest.csv         frozen HEAD of each clone
  data/repos/<repository>                     frozen full clones (read-only Git queries)

Writes results/data/*.csv (fits, series, residuals, ...) and results/tables.md; plot_results.py
reads those CSVs to draw results/figs/*.pdf. Backport rates come from
backport_rates.py, which this script calls.

Release milestones and release mapping are recomputed from the clones: for
annotated tags, phase1_tags.csv records the tag object and tagger date, not
the tagged commit and its committer date, and Phase 1's mapped_release_tag
places some Cirq commits on the non-version tag fmt-black.
"""
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import gammaln
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent
PHASE = ROOT / 'output'
REPOS_DIR = ROOT / 'data' / 'repos'
RESULTS = ROOT / 'results'
DATA = RESULTS / 'data'

SAMPLE = PHASE / 'phase2_manual_validation_sample.csv'
AUDIT = PHASE / 'phase2_second_pass_audit_sample.csv'
POOL = PHASE / 'phase1_candidate_pool.csv'
TAGS = PHASE / 'phase1_tags.csv'
MANIFEST = PHASE / 'reproducibility_manifest.csv'
API_CACHE = ROOT / 'data' / 'api_cache.json'

CUTOFF = pd.Timestamp('2026-01-01T00:00:00Z')
SEED_SAMPLE, SEED_AUDIT, AUDIT_FRACTION = 42, 43, 0.20
COCHRAN = dict(z=1.96, e=0.05, p=0.5)
MIN_FIXES, MIN_RELEASES = 30, 5
BUG_PATTERN = re.compile(r'\b(bug|fix|error|defect|regression)\b', re.IGNORECASE)
ISSUE_REF = re.compile(r'#(\d+)')
CLOSES_REF = re.compile(r'\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b', re.IGNORECASE)
LABEL_WORDS = ('bug', 'defect', 'regression')
GITHUB_NAMES = {'qiskit': 'Qiskit/qiskit', 'cirq': 'quantumlib/cirq', 'pennylane': 'PennyLaneAI/pennylane',
                'qsharp': 'microsoft/qsharp'}
A_BOUNDS = (1e-3, 1e4)
B_BOUNDS = (1e-6, 10.0)
VERSION_TAG = re.compile(r'^v?\d+\.\d+(?:\D|$)')
FINAL_RELEASE_TAG = re.compile(r'^v?\d+\.\d+\.\d+$')

REPOSITORIES = ['qiskit', 'cirq', 'pennylane', 'qsharp']
STUDY = {
    'qiskit': dict(name='Qiskit', start='1.2.0rc1', end='2.3.0rc1', color='#2980b9'),
    'cirq': dict(name='Cirq', start='v0.1', end='v1.6.1', color='#e74c3c'),
    'pennylane': dict(name='PennyLane', start='v0.25.0', end='v0.43.2', color='#27ae60'),
    'qsharp': dict(name='Q#', start='v1.0.10-rc', end='v1.23.0', color='#8e44ad'),
}
INCLUSION_CATEGORIES = ['logic_fix', 'incorrect_output', 'runtime_failure', 'semantic_violation', 'regression_fix']
EXCLUSION_CATEGORIES = ['build_only', 'ci_only', 'dependency_update', 'docs', 'nonfunctional',
                        'performance_only', 'refactor', 'style_only', 'test_only']


# ============================================================
# Git: frozen clones, release milestones, release mapping
# ============================================================

def git(repository, *args):
    result = subprocess.run(['git', '-C', str(REPOS_DIR / repository), *args], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', check=True)
    return result.stdout.strip()


def check_frozen_clones():
    manifest = pd.read_csv(MANIFEST).set_index('repository')
    for repository in REPOSITORIES:
        head = git(repository, 'rev-parse', 'HEAD')
        if head != manifest.loc[repository, 'frozen_mining_head_sha']:
            raise ValueError(f'{repository}: clone HEAD {head} is not the frozen mining commit')
        if git(repository, 'rev-parse', '--is-shallow-repository') != 'false':
            raise ValueError(f'{repository}: full Git history is required')


def release_milestones():
    """Version tags before the cutoff, peeled to commits, one milestone per commit.

    Same-commit aliases collapse to the final X.Y.Z tag (else the first name).
    Milestones are ordered by the tagged commit's committer date, then tag name.
    Returns (milestones, aliases).
    """
    names = pd.read_csv(TAGS)
    rows = []
    for repository in REPOSITORIES:
        for tag in names[names.repository.eq(repository)].tag:
            if not VERSION_TAG.match(tag):
                continue
            sha = git(repository, 'rev-parse', f'refs/tags/{tag}^{{commit}}')
            stamp = git(repository, 'show', '-s', '--format=%cI', sha)
            rows.append(dict(repository=repository, tag=tag, tag_commit_sha=sha, tag_commit_date=stamp))
    tags = pd.DataFrame(rows)
    tags['_date'] = pd.to_datetime(tags.tag_commit_date, utc=True)
    tags = tags[tags._date < CUTOFF].copy()
    tags['_preference'] = [(0 if FINAL_RELEASE_TAG.match(t) else 1, t) for t in tags.tag]
    tags = tags.sort_values(['repository', 'tag_commit_sha', '_preference'], kind='mergesort')
    kept = tags.drop_duplicates(['repository', 'tag_commit_sha']).copy()
    aliases = tags[tags.duplicated(['repository', 'tag_commit_sha'])].merge(
        kept[['repository', 'tag_commit_sha', 'tag']].rename(columns={'tag': 'kept_tag'}),
        on=['repository', 'tag_commit_sha'])
    kept = kept.sort_values(['repository', '_date', 'tag'], kind='mergesort')
    kept['milestone_order'] = kept.groupby('repository').cumcount() + 1
    columns = ['repository', 'milestone_order', 'tag', 'tag_commit_sha', 'tag_commit_date']
    return kept[columns].reset_index(drop=True), aliases[['repository', 'tag', 'kept_tag']]


def window(milestones, repository):
    """Milestones from the study's start tag through its end tag, inclusive."""
    sub = milestones[milestones.repository.eq(repository)].sort_values('milestone_order')
    names = sub.tag.tolist()
    first, last = names.index(STUDY[repository]['start']), names.index(STUDY[repository]['end'])
    if first > last:
        raise ValueError(f'{repository}: window start follows window end')
    return sub.iloc[first:last + 1].reset_index(drop=True)


def release_map(milestones, repository):
    """Earliest milestone (in milestone order) whose tagged commit contains each commit."""
    mapping = {}
    for row in milestones[milestones.repository.eq(repository)].sort_values('milestone_order').itertuples():
        for commit in git(repository, 'rev-list', row.tag_commit_sha).split():
            mapping.setdefault(commit, row.tag)
    return mapping


# ============================================================
# Models
# ============================================================

def goel_okumoto_m(t, a, b):
    """Mean value function m(t) = a(1 - exp(-bt))."""
    return a * (-np.expm1(-b * np.asarray(t)))


def goel_okumoto_lambda(t, a, b):
    """Intensity lambda(t) = ab exp(-bt)."""
    return a * b * np.exp(-b * t)


def yamada_m(t, a, b):
    """Mean value function m(t) = a(1 - (1 + bt)exp(-bt))."""
    x = b * np.asarray(t)
    # Avoid cancellation when x is small.
    small = x ** 2 * (0.5 - x / 3.0 + x ** 2 / 8.0 - x ** 3 / 30.0 + x ** 4 / 144.0)
    return a * np.where(np.abs(x) < 1e-3, small, -np.expm1(-x) - x * np.exp(-x))


def yamada_lambda(t, a, b):
    """Intensity lambda(t) = ab^2 t exp(-bt)."""
    return a * (b ** 2) * t * np.exp(-b * t)

MEAN = {'goel': goel_okumoto_m, 'yamada': yamada_m}
INTENSITY = {'goel': goel_okumoto_lambda, 'yamada': yamada_lambda}


def fitted_mean_standard_error(t, fit):
    """Delta-method standard error for the fitted mean value function."""
    t = np.asarray(t, dtype=float)
    a, b = fit['a'], fit['b']
    if fit['model_type'] == 'goel':
        da = 1.0 - np.exp(-b * t)
        db = a * t * np.exp(-b * t)
    else:
        da = 1.0 - (1.0 + b * t) * np.exp(-b * t)
        db = a * b * (t ** 2) * np.exp(-b * t)
    gradient = np.column_stack([da, db])
    variance = np.einsum('ij,jk,ik->i', gradient, fit['covariance'], gradient)
    return np.sqrt(np.maximum(variance, 0.0))


# ============================================================
# Statistics
# ============================================================

def cochran_sample_size(population):
    n0 = COCHRAN['z'] ** 2 * COCHRAN['p'] * (1 - COCHRAN['p']) / COCHRAN['e'] ** 2
    return min(population, math.ceil(n0 / (1 + (n0 - 1) / population)))


def cohen_kappa(first, second):
    """Observed agreement, chance agreement, and Cohen's kappa."""
    first, second = pd.Series(first).reset_index(drop=True), pd.Series(second).reset_index(drop=True)
    p_o = float((first == second).mean())
    p_e = sum(float((first == label).mean()) * float((second == label).mean())
              for label in set(first) | set(second))
    kappa = 1.0 if p_e >= 1.0 else (p_o - p_e) / (1.0 - p_e)
    return p_o, p_e, kappa


def compute_numerical_hessian(func, params, eps=1e-5):
    """Observed-information Hessian by central differences."""
    n = len(params)
    hessian = np.zeros((n, n))
    f0 = func(params)
    for i in range(n):
        p_plus = np.array(params, dtype=float)
        p_plus[i] += eps
        p_minus = np.array(params, dtype=float)
        p_minus[i] -= eps
        hessian[i, i] = (func(p_plus) - 2.0 * f0 + func(p_minus)) / (eps ** 2)
        for j in range(i + 1, n):
            corners = []
            for si, sj in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                p = np.array(params, dtype=float)
                p[i] += si * eps
                p[j] += sj * eps
                corners.append(func(p))
            hessian[i, j] = hessian[j, i] = (corners[0] - corners[1] - corners[2] + corners[3]) / (4.0 * eps * eps)
    return hessian


def fit_grouped_interval_srgm(t_k, y_k, model_type='goel'):
    """Constrained grouped-Poisson MLE with the amplitude profiled analytically.

    Search box a in [1e-3, 1e4], b in [1e-6, 10]. Boundary estimates have no
    local Wald interval; failed covariance calculations never invent errors.
    """
    t_k = np.asarray(t_k, dtype=float)
    y_k = np.asarray(y_k, dtype=float)
    if len(t_k) != len(y_k) or len(y_k) == 0 or np.any(y_k < 0):
        raise ValueError('Invalid grouped counts')
    if np.any(t_k < 0) or np.any(np.diff(t_k) < 0) or t_k[-1] <= 0 or y_k.sum() <= 0:
        raise ValueError('Exposure must be nonnegative and nondecreasing with positive endpoint')
    m_func = MEAN[model_type]
    total = float(y_k.sum())
    Y_k = np.cumsum(y_k)

    def neg_log_lik(params):
        a, b = params
        means = m_func(t_k, a, b)
        dm = np.diff(np.r_[0.0, means])
        if np.any(dm < 0) or np.any((dm <= 0) & (y_k > 0)):
            return np.inf
        nonzero = y_k > 0
        return float(means[-1] - np.sum(y_k[nonzero] * np.log(dm[nonzero])) + gammaln(y_k + 1).sum())

    def profile(log_b):
        b = np.exp(log_b)
        a = np.clip(total / float(m_func(t_k[-1], 1.0, b)), *A_BOUNDS)
        return neg_log_lik((a, b))

    grid = np.linspace(np.log(B_BOUNDS[0]), np.log(B_BOUNDS[1]), 301)
    values = np.array([profile(x) for x in grid])
    candidates = [(values[0], grid[0]), (values[-1], grid[-1])]
    for i in range(1, len(grid) - 1):
        if values[i] <= values[i - 1] and values[i] <= values[i + 1] and np.isfinite(values[i]):
            opt = minimize_scalar(profile, bounds=(grid[i - 1], grid[i + 1]), method='bounded',
                                  options={'xatol': 1e-12})
            if opt.success and np.isfinite(opt.fun):
                candidates.append((opt.fun, opt.x))
    loss, log_b = min(candidates)
    if not np.isfinite(loss):
        raise RuntimeError('No finite grouped likelihood fit')
    b_hat = float(np.exp(log_b))
    a_hat = float(np.clip(total / float(m_func(t_k[-1], 1.0, b_hat)), *A_BOUNDS))
    boundary = bool(any(np.isclose(a_hat, bound, rtol=1e-5, atol=0.0) for bound in A_BOUNDS) or
                    any(np.isclose(b_hat, bound, rtol=1e-5, atol=0.0) for bound in B_BOUNDS))
    covariance = np.full((2, 2), np.nan)
    se_a = se_b = np.nan
    if not boundary:
        try:
            H = compute_numerical_hessian(lambda q: neg_log_lik(np.exp(q)), np.log([a_hat, b_hat]), eps=1e-4)
            if not np.all(np.isfinite(H)) or np.min(np.linalg.eigvalsh(H)) <= 0:
                raise ValueError('Information matrix is not positive definite')
            scale = np.diag([a_hat, b_hat])
            covariance = scale @ np.linalg.inv(H) @ scale
            se_a, se_b = np.sqrt(np.diag(covariance))
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            pass
    ci_a = (max(0.0, a_hat - 1.96 * se_a), a_hat + 1.96 * se_a) if np.isfinite(se_a) else (np.nan, np.nan)
    ci_b = (max(0.0, b_hat - 1.96 * se_b), b_hat + 1.96 * se_b) if np.isfinite(se_b) else (np.nan, np.nan)
    ci_beyond_bounds = bool(np.isfinite(se_a) and np.isfinite(se_b) and (
        ci_a[0] < A_BOUNDS[0] or ci_a[1] > A_BOUNDS[1] or ci_b[0] < B_BOUNDS[0] or ci_b[1] > B_BOUNDS[1]))
    predicted = m_func(t_k, a_hat, b_hat)
    residuals = Y_k - predicted
    return dict(model_type=model_type, a=a_hat, b=b_hat, se_a=float(se_a), se_b=float(se_b),
                covariance=covariance, ci_a=ci_a, ci_b=ci_b, ci_beyond_bounds=ci_beyond_bounds,
                t_inf=1.0 / b_hat if model_type == 'yamada' else None,
                saturation=float(predicted[-1] / a_hat * 100), log_lik=-loss, aic=2 * loss + 4,
                rmse=float(np.sqrt(np.mean(residuals ** 2))), mae=float(np.mean(np.abs(residuals))),
                residuals=residuals, boundary=boundary)


# ============================================================
# Inputs
# ============================================================

def load_inputs():
    pool = pd.read_csv(POOL)
    sample = pd.read_csv(SAMPLE)
    audit = pd.read_csv(AUDIT)
    pool = pool[pool.repository.isin(REPOSITORIES)].copy()
    for frame in (sample, audit):
        frame['included'] = frame.annotator_label.eq('include').astype(int)
    if 'include_exclude' in audit and not audit.include_exclude.eq(audit.included).all():
        raise ValueError('audit include_exclude disagrees with annotator_label')
    for name, frame in (('sample', sample), ('audit', audit)):
        if not frame.annotator_label.isin(['include', 'exclude', 'uncertain']).all():
            raise ValueError(f'{name}: unlabelled or unknown annotator_label values')
        if frame.loc[frame.included.eq(1), 'inclusion_rule_triggered'].isna().any():
            raise ValueError(f'{name}: included commits lack an inclusion category')
        if frame.loc[frame.annotator_label.eq('exclude'), 'exclusion_rule_triggered'].isna().any():
            raise ValueError(f'{name}: excluded commits lack an exclusion category')
    check_frozen_clones()
    milestones, aliases = release_milestones()
    tags, release_maps = [], {}
    for repository in REPOSITORIES:
        mapping = release_maps[repository] = release_map(milestones, repository)
        in_window = set(window(milestones, repository).tag)
        hashes = pool[pool.repository.eq(repository)].commit_hash
        missing = [sha for sha in hashes if mapping.get(sha) not in in_window]
        if missing:
            raise ValueError(f'{repository}: {len(missing)} candidates are not released inside the window')
        tags += [(repository, sha, mapping[sha]) for sha in hashes]
    release = pd.DataFrame(tags, columns=['repository', 'commit_hash', 'release_tag'])
    pool = pool.merge(release, on=['repository', 'commit_hash'], validate='one_to_one')
    sample = sample.merge(release, on=['repository', 'commit_hash'], how='left', validate='one_to_one')
    if sample.release_tag.isna().any():
        raise ValueError('sampled commits are missing from the candidate pool')
    check_samples(pool, sample, audit)
    return dict(pool=pool, sample=sample, audit=audit, milestones=milestones, aliases=aliases,
                release_maps=release_maps)


def check_samples(pool, sample, audit):
    """Both annotated files must be exactly the phase_1_2.py seeded draws."""
    for repository in REPOSITORIES:
        frame = pool[pool.repository.eq(repository)].sort_values('commit_date', kind='mergesort').reset_index(drop=True)
        drawn = set(frame.sample(n=cochran_sample_size(len(frame)), random_state=SEED_SAMPLE).commit_hash)
        if drawn != set(sample[sample.repository.eq(repository)].commit_hash):
            raise ValueError(f'{repository}: validation sample is not the seed-{SEED_SAMPLE} Cochran sample')
        first = sample[sample.repository.eq(repository)].sort_values('commit_date', kind='mergesort').reset_index(drop=True)
        drawn = set(first.sample(n=math.ceil(AUDIT_FRACTION * len(first)), random_state=SEED_AUDIT).commit_hash)
        if drawn != set(audit[audit.repository.eq(repository)].commit_hash):
            raise ValueError(f'{repository}: audit is not the seed-{SEED_AUDIT} 20% subset')


# ============================================================
# Analysis
# ============================================================

def analyze(inputs):
    pool, sample, audit = inputs['pool'], inputs['sample'], inputs['audit']
    merged = audit[['repository', 'commit_hash', 'included', 'inclusion_rule_triggered']].merge(
        sample[['repository', 'commit_hash', 'included', 'inclusion_rule_triggered']],
        on=['repository', 'commit_hash'], suffixes=('_audit', '_first'), validate='one_to_one')
    included = sample[sample.included.eq(1)]
    excluded = sample[sample.annotator_label.eq('exclude')]

    mining = []
    for repository in REPOSITORIES + ['pooled']:
        pick = (lambda f: f) if repository == 'pooled' else (lambda f: f[f.repository.eq(repository)])
        sub_sample, sub_audit = pick(sample), pick(merged)
        p_o, p_e, kappa = cohen_kappa(sub_audit.included_first, sub_audit.included_audit)
        k = int(sub_sample.included.sum())
        sub_both = sub_audit[sub_audit.included_first.eq(1) & sub_audit.included_audit.eq(1)]
        mining.append(dict(repository=repository, N=len(pick(pool)), n=len(sub_sample), n_audit=len(sub_audit),
                           k=k, yield_percent=100 * k / len(sub_sample),
                           sampling_fraction_percent=100 * len(sub_sample) / len(pick(pool)),
                           p_o=100 * p_o, p_e=100 * p_e, kappa=kappa, category_n=len(sub_both),
                           category_agreement_percent=100 * float(sub_both.inclusion_rule_triggered_first.eq(
                               sub_both.inclusion_rule_triggered_audit).mean())))
    # The pooled sample over-represents repositories with larger sampling fractions; weight by pool size N.
    weighted_yield = sum(m['N'] * m['yield_percent'] for m in mining[:-1]) / mining[-1]['N']

    both = merged[merged.included_first.eq(1) & merged.included_audit.eq(1)]
    category_agreement, _, category_kappa = cohen_kappa(both.inclusion_rule_triggered_first,
                                                        both.inclusion_rule_triggered_audit)
    inclusion = (pd.crosstab(included.repository, included.inclusion_rule_triggered, normalize='index') * 100
                 ).reindex(index=REPOSITORIES, columns=INCLUSION_CATEGORIES, fill_value=0.0).fillna(0.0)
    exclusion = (pd.crosstab(excluded.repository, excluded.exclusion_rule_triggered, normalize='index') * 100
                 ).reindex(index=REPOSITORIES, columns=EXCLUSION_CATEGORIES, fill_value=0.0).fillna(0.0)

    fits, windows, start_sensitivity = {}, [], []
    for repository in REPOSITORIES:
        milestones = window(inputs['milestones'], repository)
        tags = milestones.tag.tolist()
        fixes = included[included.repository.eq(repository)].release_tag.value_counts()
        candidates = pool[pool.repository.eq(repository)].release_tag.value_counts()
        y_k = np.array([fixes.get(tag, 0) for tag in tags], dtype=float)
        c_k = np.array([candidates.get(tag, 0) for tag in tags], dtype=float)
        t_k = np.arange(1, len(tags) + 1, dtype=float)
        dates = pd.to_datetime(milestones.tag_commit_date, utc=True)
        days_k = np.maximum(((dates - dates.iloc[0]).dt.total_seconds() / 86400.0).to_numpy(), 1.0)
        for i in range(1, len(days_k)):
            if days_k[i] <= days_k[i - 1]:
                days_k[i] = days_k[i - 1] + 1.0
        k_window = int(y_k.sum())
        if k_window != int(included.repository.eq(repository).sum()):
            raise ValueError(f'{repository}: included fixes fall outside the window')
        if k_window < MIN_FIXES or len(tags) < MIN_RELEASES:
            raise ValueError(f'{repository}: below the {MIN_FIXES}-fix / {MIN_RELEASES}-release modeling threshold')
        go = fit_grouped_interval_srgm(t_k, y_k, 'goel')
        ya = fit_grouped_interval_srgm(t_k, y_k, 'yamada')
        best = min(go['aic'], ya['aic'])
        weights = np.exp(-0.5 * (np.array([go['aic'], ya['aic']]) - best))
        for fit, weight in zip((go, ya), weights / weights.sum()):
            fit['delta_aic'] = fit['aic'] - best
            fit['akaike_w'] = float(weight)
        preferred = 'goel' if go['aic'] <= ya['aic'] else 'yamada'
        go['selected'], ya['selected'] = preferred == 'goel', preferred == 'yamada'
        fits[repository] = dict(tags=tags, t_k=t_k, y_k=y_k, Y_k=np.cumsum(y_k), C_k=np.cumsum(c_k),
                                days_k=days_k, goel=go, yamada=ya, preferred_model=preferred,
                                preferred=go if preferred == 'goel' else ya)
        # The saved pool covers the first interval incompletely (coverage: missing_earlier_at_start), so
        # refit with the start milestone as t_0 and check whether the AIC selection changes.
        go1 = fit_grouped_interval_srgm(t_k[:-1], y_k[1:], 'goel')
        ya1 = fit_grouped_interval_srgm(t_k[:-1], y_k[1:], 'yamada')
        selected1 = 'goel' if go1['aic'] <= ya1['aic'] else 'yamada'
        start_sensitivity.append(dict(repository=repository, dropped_tag=tags[0], dropped_fixes=int(y_k[0]),
                                      K=len(tags) - 1, aic_goel=go1['aic'], aic_yamada=ya1['aic'],
                                      goel_boundary=go1['boundary'], yamada_boundary=ya1['boundary'],
                                      selected=selected1, delta_aic=abs(go1['aic'] - ya1['aic']),
                                      same_selection=selected1 == preferred))
        intervals = dates.diff().dt.total_seconds().dropna() / 86400.0
        aliases = inputs['aliases']
        windows.append(dict(repository=repository, start=tags[0], end=tags[-1], K=len(tags),
                            days_to_cutoff=int((CUTOFF - dates.iloc[0]).total_seconds() / 86400.0),
                            milestones_with_fixes=int((y_k > 0).sum()),
                            aliases_collapsed=int((aliases.repository.eq(repository) & aliases.kept_tag.isin(tags)).sum()),
                            candidates=int(pool.repository.eq(repository).sum()), included_in_window=k_window,
                            median_interval_days=float(intervals.median()),
                            iqr_interval_days=float(intervals.quantile(0.75) - intervals.quantile(0.25))))

    clocks = []
    for repository in REPOSITORIES:
        res = fits[repository]
        model = res['preferred_model']
        commit_fit = fit_grouped_interval_srgm(res['C_k'], res['y_k'], model)
        day_fit = fit_grouped_interval_srgm(res['days_k'], res['y_k'], model)
        clocks.append(dict(repository=repository, model=model, b_release=res['preferred']['b'],
                           b_commit=commit_fit['b'], b_day=day_fit['b'],
                           release_boundary=res['preferred']['boundary'], commit_boundary=commit_fit['boundary'],
                           day_boundary=day_fit['boundary']))
    for key in ('release', 'commit', 'day'):
        order = np.argsort(-np.array([c[f'b_{key}'] for c in clocks]), kind='stable')
        for rank, index in enumerate(order, start=1):
            clocks[index][f'rank_{key}'] = rank
    rate = {key: [c[f'b_{key}'] for c in clocks] for key in ('release', 'commit', 'day')}
    correlations = dict(release_commit=float(spearmanr(rate['release'], rate['commit'])[0]),
                        release_day=float(spearmanr(rate['release'], rate['day'])[0]),
                        commit_day=float(spearmanr(rate['commit'], rate['day'])[0]))

    residual_total, flagged = 0, []
    for repository in REPOSITORIES:
        fit = fits[repository]['preferred']
        standardized = fit['residuals'] / fit['rmse']
        residual_total += len(standardized)
        for i, value in enumerate(standardized):
            if abs(value) >= 1.96:
                flagged.append(dict(repository=repository, tag=fits[repository]['tags'][i], t=i + 1, r=float(value)))

    return dict(mining=mining, category_agreement=category_agreement, category_agreement_n=len(both),
                category_kappa=category_kappa, weighted_yield=weighted_yield, start_sensitivity=start_sensitivity,
                inclusion=inclusion, exclusion=exclusion, windows=windows,
                fits=fits, clocks=clocks, correlations=correlations, residual_total=residual_total,
                residual_flagged=flagged, label_counts=sample.annotator_label.value_counts().to_dict())


def coverage(inputs):
    """Saved candidate pool versus the documented Phase 1 rule, re-applied to the frozen clones.

    Reference rule: non-merge commits before the cutoff whose earliest containing milestone lies in the
    window and whose full message matches the keyword pattern or references an issue that the saved API
    cache labels bug, defect, or regression.
    """
    issues = json.loads(API_CACHE.read_text(encoding='utf-8')).get('issue', {})
    rows = []
    for repository in REPOSITORIES:
        milestones = window(inputs['milestones'], repository)
        in_window, start = set(milestones.tag), milestones.tag.iloc[0]
        mapping = inputs['release_maps'][repository]
        saved = inputs['pool'][inputs['pool'].repository.eq(repository)]
        sampled = set(inputs['sample'][inputs['sample'].repository.eq(repository)].commit_hash)
        saved_message = dict(zip(saved.commit_hash, saved.message.fillna('')))
        first_saved = pd.to_datetime(saved.commit_date, utc=True).min()
        log = git(repository, 'log', 'HEAD', '--no-merges', '--format=%H%x1f%cI%x1f%B%x00')
        reference = {}
        for block in log.split('\x00'):
            if not block.strip():
                continue
            sha, stamp, message = block.strip().split('\x1f', 2)
            if mapping.get(sha) not in in_window or pd.Timestamp(stamp) >= CUTOFF:
                continue
            keyword = bool(BUG_PATTERN.search(message))
            refs = set(ISSUE_REF.findall(message)) | set(CLOSES_REF.findall(message))
            labelled = any(any(word in label.lower() for label in issues.get(f'{GITHUB_NAMES[repository]}#{ref}', {})
                               .get('issue_labels', []) for word in LABEL_WORDS) for ref in refs)
            if keyword or labelled:
                reference[sha] = (pd.Timestamp(stamp), keyword, message.strip())
        missing = {sha: v for sha, v in reference.items() if sha not in saved_message}
        in_range = {sha: v for sha, v in missing.items() if v[0] >= first_saved}
        subject_only = {sha for sha, text in saved_message.items()
                        if sha in reference and text.strip() == reference[sha][2].split('\n', 1)[0].strip()
                        and reference[sha][2] != text.strip()}
        earlier = set(missing) - set(in_range)
        rows.append(dict(repository=repository, saved_pool=len(saved), reference_rule=len(reference),
                         both=len(reference) - len(missing), missing_earlier=len(earlier),
                         missing_in_range=len(in_range), label_only=sum(1 for v in in_range.values() if not v[1]),
                         saved_only=len(set(saved_message) - set(reference)), subject_only_messages=len(subject_only),
                         start_milestone=start, start_milestone_date=milestones.tag_commit_date.iloc[0],
                         pool_first_commit_date=first_saved.isoformat(),
                         # Positive: the pool begins after the start milestone's tagged commit.
                         pool_start_after_milestone_days=(first_saved - pd.Timestamp(
                             milestones.tag_commit_date.iloc[0])).total_seconds() / 86400.0,
                         pool_at_start=int(saved.release_tag.eq(start).sum()),
                         missing_earlier_at_start=sum(1 for sha in earlier if mapping[sha] == start),
                         sampled=len(sampled), subject_only_sampled=len(subject_only & sampled)))
    return rows


def backports(inputs):
    """Separate-release-line backports of the included fixes (backport_rates.py)."""
    import backport_rates
    summaries = []
    for repository in REPOSITORIES:
        _, summary = backport_rates.analyze(repository, inputs['sample'], inputs['milestones'])
        summaries.append(summary)
    return summaries


# ============================================================
# CSV export (consumed by plot_results.py)
# ============================================================

def export_csvs(results, backport_rows, coverage_rows):
    """Write every value the figures and tables use as flat CSVs in results/data/."""
    DATA.mkdir(parents=True, exist_ok=True)
    fits = results['fits']
    repo_rows, series, fit_rows, residuals = [], [], [], []
    for repository in REPOSITORIES:
        res, meta = fits[repository], STUDY[repository]
        pref = res['preferred']
        repo_rows.append(dict(repository=repository, name=meta['name'], color=meta['color'], start=meta['start'],
                              end=meta['end'], K=len(res['t_k']), preferred_model=res['preferred_model']))
        for i, tag in enumerate(res['tags']):
            series.append(dict(repository=repository, t=int(res['t_k'][i]), tag=tag, fixes=res['y_k'][i],
                               cumulative_fixes=res['Y_k'][i], cumulative_candidates=res['C_k'][i],
                               days=res['days_k'][i], residual=pref['residuals'][i],
                               standardized_residual=pref['residuals'][i] / pref['rmse']))
        for model in ('goel', 'yamada'):
            f = res[model]
            fit_rows.append(dict(
                repository=repository, model=model, selected=f['selected'], boundary=f['boundary'],
                ci_beyond_bounds=f['ci_beyond_bounds'], a=f['a'], b=f['b'], se_a=f['se_a'], se_b=f['se_b'],
                ci_a_lower=f['ci_a'][0], ci_a_upper=f['ci_a'][1], ci_b_lower=f['ci_b'][0], ci_b_upper=f['ci_b'][1],
                cov_aa=f['covariance'][0, 0], cov_ab=f['covariance'][0, 1], cov_bb=f['covariance'][1, 1],
                t_inf=f['t_inf'] if f['t_inf'] is not None else np.nan, saturation_percent=f['saturation'],
                log_lik=f['log_lik'], aic=f['aic'], delta_aic=f['delta_aic'], akaike_w=f['akaike_w'],
                rmse=f['rmse'], mae=f['mae']))
    frames = dict(
        repositories=pd.DataFrame(repo_rows), series=pd.DataFrame(series), fits=pd.DataFrame(fit_rows),
        mining=pd.DataFrame(results['mining']), windows=pd.DataFrame(results['windows']),
        clocks=pd.DataFrame(results['clocks']), backports=pd.DataFrame(backport_rows),
        coverage=pd.DataFrame(coverage_rows), start_sensitivity=pd.DataFrame(results['start_sensitivity']),
        residual_flagged=pd.DataFrame(results['residual_flagged'], columns=['repository', 'tag', 't', 'r']),
        inclusion_shares=results['inclusion'].rename_axis('repository').reset_index(),
        exclusion_shares=results['exclusion'].rename_axis('repository').reset_index(),
        label_counts=pd.DataFrame(sorted(results['label_counts'].items()), columns=['label', 'count']),
        scalars=pd.DataFrame(
            [('category_agreement', results['category_agreement']), ('category_agreement_n', results['category_agreement_n']),
             ('category_kappa', results['category_kappa']), ('residual_total', results['residual_total']),
             ('pooled_yield_weighted_percent', results['weighted_yield'])]
            + [(f'spearman_{k}', v) for k, v in results['correlations'].items()], columns=['name', 'value']))
    for key, frame in frames.items():
        frame.to_csv(DATA / f'{key}.csv', index=False)


# ============================================================
# Tables
# ============================================================

def pct(value, digits=2):
    return f'{float(value):.{digits}f}%'


def num(value, digits=2):
    return f'{float(value):.{digits}f}'


def name(repository):
    return 'Pooled' if repository == 'pooled' else STUDY[repository]['name']


def model_name(model):
    return 'GO' if model == 'goel' else 'Yamada'


def table(label, caption, headings, rows):
    def cell(value):
        return str(value).replace('|', r'\|')
    lines = [f'### {caption} ({label})', '', '| ' + ' | '.join(map(cell, headings)) + ' |', '|' + '---|' * len(headings)]
    lines += ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]
    return '\n'.join(lines) + '\n\n'


def tables_markdown(results, backport_rows, coverage_rows):
    mining, inclusion, exclusion = results['mining'], results['inclusion'], results['exclusion']
    parts = [table('coverage', 'Saved candidate pool compared with the documented Phase 1 rule',
                   ['SDK', 'Saved pool N', 'Reference rule', 'Both', 'Missing, earlier', 'Missing, in range',
                    'of which label-only', 'Saved only', 'Subject-only messages'],
                   [[name(c['repository']), f"{c['saved_pool']:,}", f"{c['reference_rule']:,}", f"{c['both']:,}",
                     c['missing_earlier'], c['missing_in_range'], c['label_only'], c['saved_only'],
                     c['subject_only_messages']] for c in coverage_rows]),
             table('coverage_start', 'Saved-pool coverage of each window\'s first release interval',
                   ['SDK', 'Start milestone', 'Pool begins after start (days)', 'Pool candidates at start',
                    'Missing, earlier', 'of which at start', 'Sampled', 'Sampled subject-only messages'],
                   [[name(c['repository']), c['start_milestone'], num(c['pool_start_after_milestone_days'], 1),
                     c['pool_at_start'], c['missing_earlier'],
                     c['missing_earlier_at_start'], c['sampled'], c['subject_only_sampled']] for c in coverage_rows]),
             table('data_mining', 'Candidate extraction, inclusion yield, and binary repeat-audit agreement',
                   ['SDK', 'N', 'n', 'n/N', 'Re-audit', 'Yield k/n', 'p_o', 'p_e', 'κ'],
                   [[name(m['repository']), f"{m['N']:,}", m['n'], pct(m['sampling_fraction_percent'], 1), m['n_audit'],
                     pct(m['yield_percent']), pct(m['p_o']), pct(m['p_e']), num(m['kappa'], 3)] for m in mining]),
             table('defect_taxonomy', 'Defect taxonomy distribution among included fixes',
                   ['SDK', 'Algorithmic logic', 'Incorrect output', 'Runtime failure', 'Semantic violation', 'Regression'],
                   [[name(r)] + [pct(v) for v in inclusion.loc[r]] for r in REPOSITORIES]),
             table('exclusion_taxonomy', 'Maintenance taxonomy among explicitly excluded candidates',
                   ['SDK', 'Build', 'CI', 'Dependency', 'Docs', 'Nonfunctional', 'Performance', 'Refactor', 'Style', 'Tests'],
                   [[name(r)] + [pct(v) for v in exclusion.loc[r]] for r in REPOSITORIES])]
    rows = []
    for w in results['windows']:
        k = next(m for m in mining if m['repository'] == w['repository'])
        rows.append([name(w['repository']), f"{w['start']} to {w['end']}", w['K'], w['aliases_collapsed'],
                     w['milestones_with_fixes'], f"{w['days_to_cutoff']:,}", f"{w['candidates']:,}",
                     w['included_in_window'], f"{w['median_interval_days']:.1f} ({w['iqr_interval_days']:.1f})",
                     num(1000 * k['k'] / k['n'], 1)])
    parts.append(table('demographics', 'Release-milestone windows and candidate exposure',
                       ['SDK', 'Window', 'K', 'Aliases collapsed', 'Milestones with fixes', 'Days to cutoff',
                        'Candidates', 'Included fixes', 'Median interval days (IQR)', 'Included per 1,000 sampled'], rows))
    parts.append(table('backports', 'Separate-release-line backports with recorded copy evidence',
                       ['SDK', 'Validated', 'Mapped denominator', 'Backported', 'PR reference only', 'Rate', '95% Wilson CI'],
                       [[name(b['repository']), b['validated_fixes'], b['mapped_fixes'], b['backported_fixes'],
                         b['pr_reference_only'], pct(b['backport_rate_percent']),
                         f"[{b['wilson_lower_percent']:.2f}, {b['wilson_upper_percent']:.2f}]%"] for b in backport_rows]))
    rows = []
    for r in REPOSITORIES:
        for model in ('goel', 'yamada'):
            f = results['fits'][r][model]
            a_ci = f"[{f['ci_a'][0]:.1f}, {f['ci_a'][1]:.1f}]" if np.isfinite(f['se_a']) else 'unavailable'
            b_ci = f"[{f['ci_b'][0]:.6f}, {f['ci_b'][1]:.6f}]" if np.isfinite(f['se_b']) else 'unavailable'
            title = (model_name(model) + (' (selected)' if f['selected'] else '')
                     + ('†' if f['boundary'] else '') + ('‡' if f['ci_beyond_bounds'] else ''))
            rows.append([name(r), title, f"{f['a']:.2f} {a_ci}", f"{f['b']:.6f} {b_ci}",
                         num(f['t_inf']) if f['t_inf'] else '—', pct(f['saturation']), num(f['log_lik']),
                         num(f['aic']), num(f['delta_aic']), num(f['akaike_w'], 4), num(f['rmse']), num(f['mae'])])
    parts.append(table('srgm_fits', 'Constrained grouped-Poisson model fits and AIC selection',
                       ['SDK', 'Model', 'a [95% CI]', 'b [95% CI]', '1/b', 'Sat.', 'ln L', 'AIC', 'ΔAIC', 'w_i',
                        'RMSE', 'MAE'], rows))
    parts.append(table('sensitivity_clocks', 'Exposure-clock sensitivity with the same grouped-likelihood estimator',
                       ['SDK', 'Model', 'b release', 'b candidate commit', 'b day', 'Release rank', 'Commit rank', 'Day rank'],
                       [[name(c['repository']), model_name(c['model'])]
                        + [num(c[f'b_{k}'], 8) + ('†' if c[f'{k}_boundary'] else '') for k in ('release', 'commit', 'day')]
                        + [c['rank_release'], c['rank_commit'], c['rank_day']] for c in results['clocks']]))
    parts.append(table('start_sensitivity', 'AIC selection with the first release interval dropped',
                       ['SDK', 'Dropped milestone', 'Fixes dropped', 'K', 'AIC GO', 'AIC Yamada', 'Selected',
                        'ΔAIC', 'Same as release-index selection'],
                       [[name(s['repository']), s['dropped_tag'], s['dropped_fixes'], s['K'],
                         num(s['aic_goel']) + ('†' if s['goel_boundary'] else ''),
                         num(s['aic_yamada']) + ('†' if s['yamada_boundary'] else ''), model_name(s['selected']),
                         num(s['delta_aic']), 'yes' if s['same_selection'] else 'no']
                        for s in results['start_sensitivity']]))
    parts.append(table('category_agreement', 'Repeat-audit inclusion-category agreement',
                       ['SDK', 'Included in both passes', 'Agreement', 'κ'],
                       [[name(m['repository']), m['category_n'], pct(m['category_agreement_percent']),
                         num(results['category_kappa'], 3) if m['repository'] == 'pooled' else '—']
                        for m in mining]))
    parts.append(table('pooled_yield', 'Pooled inclusion yield', ['Sample-pooled', 'Weighted by pool size N'],
                       [[pct(mining[-1]['yield_percent']), pct(results['weighted_yield'])]]))
    parts.append(table('label_counts', 'First-pass annotation counts', ['Label', 'Count'],
                       [[label, count] for label, count in results['label_counts'].items()]))
    parts.append(table('clock_correlations', 'Spearman correlations between exposure clocks', ['Clock pair', 'ρ'],
                       [[pair, num(value)] for pair, value in results['correlations'].items()]))
    parts.append(table('residual_summary', 'Cumulative residual threshold counts', ['Milestones', '|r_k|≥1.96'],
                       [[results['residual_total'], len(results['residual_flagged'])]]))
    parts.append(table('residual_flags', 'Cumulative residual threshold exceedances', ['SDK', 'Milestone', 'Index', 'r_k'],
                       [[name(f['repository']), f['tag'], f['t'], num(f['r'])] for f in results['residual_flagged']]))
    return '\n'.join(parts)


def main():
    inputs = load_inputs()
    results = analyze(inputs)
    backport_rows = backports(inputs)
    coverage_rows = coverage(inputs)
    RESULTS.mkdir(parents=True, exist_ok=True)
    export_csvs(results, backport_rows, coverage_rows)
    (RESULTS / 'tables.md').write_text(tables_markdown(results, backport_rows, coverage_rows), encoding='utf-8')
    print('Wrote results/data/*.csv and results/tables.md; run plot_results.py for figures')

if __name__ == '__main__':
    main()
