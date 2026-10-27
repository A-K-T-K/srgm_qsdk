#!/usr/bin/env python3
"""Draw the result figures from the CSVs written by generate_results.py.

Reads results/data/{repositories,series,fits,inclusion_shares}.csv; refits nothing.
Writes results/figs/fig1a-d, fig2a-d, fig3, fig4a, fig4b (.pdf).
"""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'results' / 'data'
FIGURES = ROOT / 'results' / 'figs'

INCLUSION_CATEGORIES = ['logic_fix', 'incorrect_output', 'runtime_failure', 'semantic_violation', 'regression_fix']

plt.rcParams.update({
    'font.family': 'serif', 'font.serif': ['DejaVu Serif', 'Times New Roman', 'serif'],
    'font.size': 10, 'axes.labelsize': 11, 'xtick.labelsize': 9, 'ytick.labelsize': 9,
    'legend.fontsize': 8.5, 'lines.linewidth': 1.8, 'axes.grid': True, 'grid.alpha': 0.3,
    'grid.linestyle': '--',
})
FIGURE_SIZE = (8.0, 4.5)
PDF_METADATA = {'CreationDate': None, 'Creator': None, 'Producer': None}


# ============================================================
# Model curves (evaluated from the fitted a, b stored in fits.csv)
# ============================================================

def goel_okumoto_m(t, a, b):
    return a * (-np.expm1(-b * np.asarray(t)))


def goel_okumoto_lambda(t, a, b):
    return a * b * np.exp(-b * t)


def yamada_m(t, a, b):
    x = b * np.asarray(t)
    small = x ** 2 * (0.5 - x / 3.0 + x ** 2 / 8.0 - x ** 3 / 30.0 + x ** 4 / 144.0)
    return a * np.where(np.abs(x) < 1e-3, small, -np.expm1(-x) - x * np.exp(-x))


def yamada_lambda(t, a, b):
    return a * (b ** 2) * t * np.exp(-b * t)


MEAN = {'goel': goel_okumoto_m, 'yamada': yamada_m}
INTENSITY = {'goel': goel_okumoto_lambda, 'yamada': yamada_lambda}


def fitted_mean_standard_error(t, fit):
    """Delta-method standard error of the fitted mean value function."""
    t = np.asarray(t, dtype=float)
    a, b = fit.a, fit.b
    if fit.model == 'goel':
        da = 1.0 - np.exp(-b * t)
        db = a * t * np.exp(-b * t)
    else:
        da = 1.0 - (1.0 + b * t) * np.exp(-b * t)
        db = a * b * (t ** 2) * np.exp(-b * t)
    gradient = np.column_stack([da, db])
    covariance = np.array([[fit.cov_aa, fit.cov_ab], [fit.cov_ab, fit.cov_bb]])
    variance = np.einsum('ij,jk,ik->i', gradient, covariance, gradient)
    return np.sqrt(np.maximum(variance, 0.0))


# ============================================================
# Figures
# ============================================================

def release_ticks(K):
    ticks = [0] + list(range(5, K, 5)) + [K]
    if len(ticks) >= 2 and ticks[-1] - ticks[-2] < 2:
        ticks.pop(-2)
    return ticks


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIGURES / name, format='pdf', bbox_inches='tight', metadata=PDF_METADATA)
    plt.close(fig)


def load():
    repos = pd.read_csv(DATA / 'repositories.csv').set_index('repository')
    series = pd.read_csv(DATA / 'series.csv')
    fits = pd.read_csv(DATA / 'fits.csv')
    shares = pd.read_csv(DATA / 'inclusion_shares.csv').set_index('repository')
    return repos, series, fits, shares


def fit_row(fits, repository, model):
    return next(fits[fits.repository.eq(repository) & fits.model.eq(model)].itertuples())


def make_figures():
    FIGURES.mkdir(parents=True, exist_ok=True)
    repos, series, fits, shares = load()
    repositories = list(repos.index)
    by_repo = {r: series[series.repository.eq(r)].sort_values('t') for r in repositories}

    for suffix, repository in zip('abcd', repositories):
        s, meta = by_repo[repository], repos.loc[repository]
        t_k, Y_k, K = s.t.to_numpy(), s.cumulative_fixes.to_numpy(), len(s)
        t_fine = np.linspace(0, K, 400)
        fig, ax = plt.subplots(figsize=FIGURE_SIZE)
        ax.step(np.r_[0, t_k], np.r_[0, Y_k], where='post', color='#7f8c8d', alpha=0.45, linestyle=':')
        ax.scatter(t_k, Y_k, color='#2c3e50', s=24, zorder=4,
                   label=f"Observed cumulative fixes ($k={int(Y_k[-1])}$)")
        for model, color, style, title in (('goel', '#2980b9', '-', 'Goel-Okumoto'),
                                           ('yamada', '#c0392b', '--', 'Yamada delayed S-shaped')):
            fit = fit_row(fits, repository, model)
            tag = (' (selected)' if fit.selected else '') + (', bound active' if fit.boundary else '')
            ax.plot(t_fine, MEAN[model](t_fine, fit.a, fit.b), color=color, linestyle=style,
                    label=f"{title}{tag}: $\\hat a={fit.a:.1f}$, $\\hat b={fit.b:.4f}$")
        pref = fit_row(fits, repository, meta.preferred_model)
        if np.all(np.isfinite([pref.cov_aa, pref.cov_ab, pref.cov_bb])):
            mean = MEAN[pref.model](t_fine, pref.a, pref.b)
            se = fitted_mean_standard_error(t_fine, pref)
            ax.fill_between(t_fine, np.maximum(0, mean - 1.96 * se), mean + 1.96 * se,
                            color='#3498db' if pref.model == 'goel' else '#e74c3c', alpha=0.12,
                            label='95% local delta-method band (selected model)')
        ax.set_xticks(release_ticks(K))
        ax.set_xlim(0, K)
        ax.set_xlabel(f"Release milestone index $t$ ({meta.start} to {meta.end})")
        ax.set_ylabel('Cumulative validated fixes')
        ax.set_ylim(0, Y_k[-1] * 1.35)
        ax.legend(loc='upper left', frameon=True, facecolor='white', framealpha=0.95, edgecolor='#bdc3c7')
        save(fig, f'fig1{suffix}.pdf')

    for suffix, repository in zip('abcd', repositories):
        s, meta = by_repo[repository], repos.loc[repository]
        t_k, y_k, K = s.t.to_numpy(), s.fixes.to_numpy(), len(s)
        t_fine = np.linspace(0, K, 400)
        fig, ax = plt.subplots(figsize=FIGURE_SIZE)
        ax.bar(t_k - 0.5, y_k, width=0.6, color='#bdc3c7', alpha=0.8, edgecolor='#7f8c8d',
               label=f"Validated fixes per release interval (total {int(y_k.sum())})")
        peak = 0.0
        for model, color, style, formula in (('goel', '#2980b9', '-', r'$\lambda_{\mathrm{GO}}(t)=abe^{-bt}$'),
                                             ('yamada', '#c0392b', '--', r'$\lambda_{\mathrm{Y}}(t)=ab^2te^{-bt}$')):
            fit = fit_row(fits, repository, model)
            values = INTENSITY[model](t_fine, fit.a, fit.b)
            peak = max(peak, float(values.max()))
            ax.plot(t_fine, values, color=color, linestyle=style, label=formula)
        t_inf = fit_row(fits, repository, 'yamada').t_inf
        if np.isfinite(t_inf) and t_inf <= K:
            ax.axvline(t_inf, color='#8e44ad', linestyle=':', linewidth=1.5,
                       label=f"Yamada intensity peak ($1/\\hat b={t_inf:.1f}$)")
        ax.set_xticks(release_ticks(K))
        ax.set_xlim(0, K)
        ax.set_xlabel(f"Release milestone index $t$ ({meta.start} to {meta.end})")
        ax.set_ylabel('Fixes per release interval')
        ax.set_ylim(0, max(float(y_k.max()), peak) * 1.35)
        ax.legend(loc='upper right', frameon=True, facecolor='white', framealpha=0.95, edgecolor='#bdc3c7')
        save(fig, f'fig2{suffix}.pdf')

    max_K = max(len(s) for s in by_repo.values())

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    styles = {'qiskit': '-', 'cirq': ':', 'pennylane': '--', 'qsharp': '-.'}
    for repository in repositories:
        meta, K = repos.loc[repository], len(by_repo[repository])
        pref = fit_row(fits, repository, meta.preferred_model)
        t = np.linspace(0, K, 300)
        model = 'GO' if pref.model == 'goel' else 'Yamada'
        bound = ', bound active' if pref.boundary else ''
        ax.plot(t, MEAN[pref.model](t, pref.a, pref.b) / pref.a, color=meta.color,
                linestyle=styles[repository], linewidth=2.2, label=f"{meta['name']} ({model}{bound}; $K={K}$)")
    ax.axhline(0.80, color='#34495e', linestyle='--', linewidth=1.2, label='80% of fitted $a$')
    ax.set_xlabel('Release milestone index $t$ within each window')
    ax.set_ylabel(r'Fitted $\hat m(t)/\hat a$')
    ax.set_xlim(0, max_K)
    ax.set_ylim(0.0, 1.05)
    ax.yaxis.set_major_formatter(ticker.PercentFormatter(1.0))
    ax.legend(loc='upper left', frameon=True, facecolor='white', framealpha=0.95, edgecolor='#bdc3c7')
    save(fig, 'fig3.pdf')

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    markers = {'qiskit': 'o', 'cirq': 's', 'pennylane': '^', 'qsharp': 'D'}
    for repository in repositories:
        meta, s = repos.loc[repository], by_repo[repository]
        pref = fit_row(fits, repository, meta.preferred_model)
        ax.plot(s.t, s.standardized_residual, color=meta.color, marker=markers[repository], markersize=4.5,
                linewidth=1.3, label=f"{meta['name']} (RMSE {pref.rmse:.2f})")
    ax.axhline(0, color='black', linewidth=0.9, alpha=0.7)
    ax.axhline(1.96, color='#c0392b', linestyle='--', linewidth=1.1, label=r'$\pm1.96$')
    ax.axhline(-1.96, color='#c0392b', linestyle='--', linewidth=1.1)
    ax.set_xlabel('Release milestone index $t_k=k$')
    ax.set_ylabel(r'Standardized cumulative residual $r_k$')
    ax.set_xlim(1, max_K)
    ax.set_ylim(-3.0, 3.0)
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02), ncol=3, frameon=True, facecolor='white',
              framealpha=0.95, edgecolor='#bdc3c7', fontsize=8.2)
    save(fig, 'fig4a.pdf')

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    order = list(reversed(repositories))
    names = {'logic_fix': 'Algorithmic / logic flaw', 'incorrect_output': 'Incorrect simulation / output',
             'runtime_failure': 'Runtime crash / exception', 'semantic_violation': 'Semantic violation',
             'regression_fix': 'Regression'}
    colors = {'logic_fix': '#34495e', 'incorrect_output': '#e67e22', 'runtime_failure': '#c0392b',
              'semantic_violation': '#2980b9', 'regression_fix': '#27ae60'}
    shares = shares.reindex(order)
    left = np.zeros(len(order))
    for category in INCLUSION_CATEGORIES:
        values = shares[category].to_numpy()
        ax.barh([repos.loc[r, 'name'] for r in order], values, left=left, color=colors[category],
                edgecolor='white', linewidth=1.0, label=names[category])
        left += values
    ax.set_xlabel('Share of validated fixes')
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(ticker.PercentFormatter(100.0))
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02), ncol=3, frameon=True, facecolor='white',
              framealpha=0.95, edgecolor='#bdc3c7', fontsize=8.0)
    save(fig, 'fig4b.pdf')


if __name__ == '__main__':
    make_figures()
    print('Generated results/figs/*.pdf')
