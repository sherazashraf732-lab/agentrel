"""Trajectory consistency from Rabanser et al. (ICML 2026), Table 2.

C_traj^d = 1 - mean pairwise Jensen-Shannon divergence between the runs'
           action-type distributions ("does it use the same tools?")
C_traj^s = 1 - mean pairwise normalized Levenshtein distance between the
           runs' action sequences ("in the same order?")
Pairs are taken within a task and averaged over tasks, which equals the
paper's 2 / (T K (K-1)) normalization when every task has K runs.

Our choices where the paper is not explicit:
- JSD uses log base 2, so it lies in [0, 1].
- Two empty action sequences are identical (distance 0); empty vs non-empty
  is maximally different (distance 1). Crashed runs have no actions.
- Returns None when no run recorded any action (e.g. a plain Q&A agent),
  or no task has two or more runs.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from itertools import combinations

from agentrel.core.runner import RunRecord


def jensen_shannon(a: Sequence[str], b: Sequence[str]) -> float:
    """JSD (base 2) between the action-type frequency distributions of a and b."""
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    ca, cb = Counter(a), Counter(b)
    total = 0.0
    for action in set(ca) | set(cb):
        p = ca[action] / len(a)
        q = cb[action] / len(b)
        m = (p + q) / 2
        if p:
            total += 0.5 * p * math.log2(p / m)
        if q:
            total += 0.5 * q * math.log2(q / m)
    return min(max(total, 0.0), 1.0)


def normalized_levenshtein(a: Sequence[str], b: Sequence[str]) -> float:
    """Edit distance between two sequences divided by the longer length."""
    if not a and not b:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        current = [i]
        for j, y in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y))
            )
        previous = current
    return previous[-1] / max(len(a), len(b))


def _pairwise_consistency(
    records: Sequence[RunRecord],
    distance: Callable[[Sequence[str], Sequence[str]], float],
) -> float | None:
    if not any(r.actions for r in records):
        return None
    groups: dict[str, list[list[str]]] = defaultdict(list)
    for r in records:
        groups[r.task_id].append(r.actions)
    per_task = [
        statistics.fmean(distance(x, y) for x, y in combinations(runs, 2))
        for runs in groups.values()
        if len(runs) >= 2
    ]
    if not per_task:
        return None
    return 1.0 - statistics.fmean(per_task)


def trajectory_distribution_consistency(records: Sequence[RunRecord]) -> float | None:
    """C_traj^d: same tools used across runs (order ignored)."""
    return _pairwise_consistency(records, jensen_shannon)


def trajectory_sequence_consistency(records: Sequence[RunRecord]) -> float | None:
    """C_traj^s: same tools in the same order across runs."""
    return _pairwise_consistency(records, normalized_levenshtein)
