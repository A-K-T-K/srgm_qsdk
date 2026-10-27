#!/usr/bin/env python3
"""Detect separate-release-line backports of the validated fixes.

Run: python backport_rates.py
Reads output/phase2_manual_validation_sample.csv (included = annotator_label
'include'), the release milestones rebuilt from the frozen clones by
generate_results.release_milestones, and data/repos/<repository>. No fetching or
checkouts. Prints rates, denominators, Wilson intervals and per-fix evidence, and
writes results/backport_details.csv and results/backport_summary.csv.

The denominator is the included sample commits whose earliest containing release
falls in the study window. A match requires a distinct copied commit in a
different major/minor release line and a containing tag that does not descend
from the primary release. Evidence is stable patch-id equality or an explicit
cherry-pick/backport SHA or source PR reference. Ordinary ancestry in multiple
tags is never evidence of backporting.

Scope: released commits in the bounded tag windows through 2026-01-01. Untagged
maintenance-branch copies and copies after the window are not counted. Adapted
patches without explicit references may be missed; PR-only evidence merits
manual review. Empty patches and merge commits have no patch-id evidence.
"""
import csv
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from math import sqrt

import pandas as pd

from generate_results import (CUTOFF, REPOS_DIR, REPOSITORIES, RESULTS, SAMPLE, STUDY, check_frozen_clones, git,
                              release_milestones)

COPY_CUE = re.compile(r"back[ -]?port|cherry[ -]?pick", re.I)
SHA_REF = re.compile(r"\b[0-9a-f]{7,40}\b", re.I)
PR_REF = re.compile(r"\(#(\d+)\)|(?:pull request|PR)\s*#(\d+)", re.I)


def date(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Date must include a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def release_line(tag):
    match = re.match(r"^v?(\d+)\.(\d+)(?:\D|$)", tag)
    return tuple(map(int, match.groups())) if match else None


def patch_ids(repository, refs, since):
    """Stream patches through Git instead of retaining repository diffs in RAM."""
    command = ["git", "-C", str(REPOS_DIR / repository), "log", "--no-merges",
               "--no-ext-diff", "--no-textconv", "--pretty=format:commit %H",
               "-p", f"--since-as-filter={since}", *refs, "--"]
    with tempfile.TemporaryFile() as errors:
        log = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            result = subprocess.run(["git", "patch-id", "--stable"], stdin=log.stdout,
                                    capture_output=True, text=True, check=True)
        finally:
            log.stdout.close()
            status = log.wait()
        if status:
            errors.seek(0)
            raise RuntimeError(errors.read().decode("utf-8", "replace"))
    return {sha: patch for patch, sha in (line.split() for line in result.stdout.splitlines())}


def reference_evidence(source, source_message, copy_message):
    if not COPY_CUE.search(copy_message):
        return ""
    if any(source.startswith(ref.lower()) for ref in SHA_REF.findall(copy_message)):
        return "explicit_commit_reference"
    source_prs = {a or b for a, b in PR_REF.findall(source_message)}
    if source_prs & set(re.findall(r"#(\d+)\b", copy_message)):
        return "explicit_pr_reference_review"
    return ""


def wilson_interval(successes, total):
    """Two-sided 95% Wilson score interval as percentages."""
    if total == 0:
        return None, None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half_width = z * sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return 100 * max(0.0, center - half_width), 100 * min(1.0, center + half_width)


def analyze(repository, sample, milestones):
    """Per-fix backport evidence and the summary row for one repository."""
    start, end = STUDY[repository]['start'], STUDY[repository]['end']
    tags = milestones[milestones.repository.eq(repository)].sort_values('milestone_order').to_dict('records')
    names = [row['tag'] for row in tags]
    first, last = names.index(start), names.index(end)
    window = range(first, last + 1)
    memberships = defaultdict(int)
    tag_shas = []
    for i, row in enumerate(tags):
        tag_shas.append(row['tag_commit_sha'])
        for commit in git(repository, "rev-list", row['tag_commit_sha'], "--").splitlines():
            memberships[commit] |= 1 << i

    fixes = {}
    for row in sample[sample.repository.eq(repository) & sample.annotator_label.eq('include')].to_dict('records'):
        sha = row["commit_hash"].lower()
        if sha in fixes:
            raise ValueError(f"{repository}: duplicate included commit {sha}")
        fixes[sha] = row
    since = min(date(row["commit_date"]) for row in fixes.values()).isoformat()
    refs = [tags[i]['tag_commit_sha'] for i in window]
    # Explicit timestamps enforce the cutoff; since-as-filter traverses
    # old-dated parents, unlike --since's early traversal stopping behavior.
    fields = git(repository, "log", f"--since-as-filter={since}", "--format=%H%x00%cI%x00%B%x00", *refs, "--").split("\x00")
    messages = {}
    for i in range(0, len(fields) - 2, 3):
        sha, stamp, message = fields[i:i + 3]
        if date(stamp) < CUTOFF:
            messages[sha.strip()] = message
    patches = patch_ids(repository, refs, since)
    by_patch = defaultdict(set)
    references = []
    for sha, message in messages.items():
        if sha in patches:
            by_patch[patches[sha]].add(sha)
        if COPY_CUE.search(message):
            references.append(sha)

    details = []
    for source, row in fixes.items():
        bits = memberships.get(source, 0)
        primary = (bits & -bits).bit_length() - 1 if bits else None
        mapped = primary is not None and primary in window
        detail = dict(repository=repository, fix_commit=source, fix_date=row['commit_date'],
                      fix_message=str(row['message']).strip(),
                      primary_release=names[primary] if primary is not None else "",
                      primary_release_date=tags[primary]['tag_commit_date'] if primary is not None else "",
                      primary_release_line='.'.join(map(str, release_line(names[primary]))) if primary is not None else "",
                      mapped_in_window=int(mapped), is_backported=0,
                      status="no_evidence" if mapped else "outside_window_or_unreleased",
                      copy_commits="", copy_releases="", evidence="", copy_metadata_json="[]")
        if mapped:
            primary_line = release_line(names[primary])
            # A destination must be on a different version line and must not
            # contain the primary release itself in its ancestry.
            eligible = [i for i in window if release_line(names[i]) != primary_line
                        and not memberships[tag_shas[primary]] & (1 << i)]
            candidates = set(by_patch.get(patches.get(source), ()))
            source_message = messages.get(source) or git(repository, "show", "-s", "--format=%B", source)
            for copy in references:
                if reference_evidence(source, source_message, messages[copy]):
                    candidates.add(copy)
            hits = []
            for copy in sorted(candidates - {source}):
                dest = [i for i in eligible if memberships[copy] & (1 << i)]
                # The copied commit must be absent from the primary release.
                if not dest or memberships[copy] & (1 << primary):
                    continue
                evidence = reference_evidence(source, source_message, messages[copy])
                if patches.get(source) and patches.get(source) == patches.get(copy):
                    evidence = "stable_patch_id" + ("+" + evidence if evidence else "")
                if evidence:
                    hits.append((copy, names[dest[0]], evidence))
            if hits:
                detail.update(is_backported=1, status="backported",
                              copy_commits=";".join(h[0] for h in hits),
                              copy_releases=";".join(h[1] for h in hits),
                              evidence=";".join(h[2] for h in hits),
                              copy_metadata_json=json.dumps([
                                  dict(commit=sha, release=tag, evidence=evidence,
                                       date=git(repository, 'show', '-s', '--format=%cI', sha),
                                       message=messages[sha].strip())
                                  for sha, tag, evidence in hits]))
        details.append(detail)
    denominator = sum(row["mapped_in_window"] for row in details)
    numerator = sum(row["is_backported"] for row in details)
    lower, upper = wilson_interval(numerator, denominator)
    pr_only = sum(1 for row in details if row['is_backported']
                  and all(e == 'explicit_pr_reference_review' for e in row['evidence'].split(';')))
    summary = dict(repository=repository, validated_fixes=len(fixes), mapped_fixes=denominator,
                   backported_fixes=numerator, pr_reference_only=pr_only,
                   backport_rate_percent=100 * numerator / denominator if denominator else float('nan'),
                   wilson_lower_percent=lower, wilson_upper_percent=upper, window_start=start, window_end=end,
                   outside_window_or_unreleased=len(fixes) - denominator)
    return details, summary


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    # Commit messages contain Unicode; Windows consoles default to a legacy code page.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    check_frozen_clones()
    milestones, _ = release_milestones()
    sample = pd.read_csv(SAMPLE)
    details, summaries = [], []
    for repository in REPOSITORIES:
        rows, summary = analyze(repository, sample, milestones)
        details += rows
        summaries.append(summary)
    print(f"{'Repository':<10} {'Validated':>9} {'Mapped':>6} {'Backported':>10} {'Rate':>8}  95% Wilson CI")
    for s in summaries:
        print(f"{STUDY[s['repository']]['name']:<10} {s['validated_fixes']:>9} {s['mapped_fixes']:>6} "
              f"{s['backported_fixes']:>10} {s['backport_rate_percent']:>7.2f}%  "
              f"[{s['wilson_lower_percent']:.2f}%, {s['wilson_upper_percent']:.2f}%]")
    for row in details:
        if row['is_backported']:
            print(f"\n{row['repository']} {row['fix_commit']} ({row['primary_release']}): {row['fix_message'].splitlines()[0]}")
            for commit, release, evidence in zip(row['copy_commits'].split(';'), row['copy_releases'].split(';'),
                                                 row['evidence'].split(';')):
                print(f"  copy {commit} in {release}: {evidence}")
    RESULTS.mkdir(exist_ok=True)
    write_csv(RESULTS / 'backport_details.csv', details)
    write_csv(RESULTS / 'backport_summary.csv', summaries)
    print('\nWrote results/backport_details.csv and results/backport_summary.csv')


if __name__ == "__main__":
    main()
