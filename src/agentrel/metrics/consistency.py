"""Consistency metrics from Rabanser et al., "Towards a Science of AI Agent
Reliability" (ICML 2026), Table 2. All scores are in [0, 1]; higher is better.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence

from agentrel.core.runner import RunRecord

EPS = 1e-8  # paper's small constant, avoids division by zero


def _group_by_task(records: Sequence[RunRecord]) -> dict[str, list[RunRecord]]:
    if not records:
        raise ValueError("records must not be empty")
    groups: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        groups[record.task_id].append(record)
    return dict(groups)


def outcome_consistency(records: Sequence[RunRecord]) -> float:
    """C_out = mean over tasks of (2 * p_hat - 1)^2.

    p_hat is the task's success rate across runs. Crashed runs count as
    failures. Always-right and always-wrong are both fully consistent.
    """
    scores: list[float] = []
    for task_runs in _group_by_task(records).values():
        p_hat = sum(r.is_correct for r in task_runs) / len(task_runs)
        scores.append((2 * p_hat - 1) ** 2)
    return statistics.fmean(scores)


def _cv(values: list[float]) -> float:
    return statistics.pstdev(values) / (statistics.fmean(values) + EPS)


def resource_consistency(records: Sequence[RunRecord]) -> float | None:
    """C_res = exp(-mean over resource types of CV), conditioned on success.

    Paper (Table 2 and Appendix C.1): per task, the coefficient of variation of each
    resource across the K runs, computed over the *successful* runs only (a failed
    run that stops early is not a resource "inconsistency"), averaged over resource
    types before the exponential. Resources: run time, model calls and tokens (the
    cost of a local model) when every run reports them.

    Tasks with fewer than two successful runs have no variance and are skipped.
    Returns None when no task has two successful runs.
    """
    if not records:
        raise ValueError("no runs")
    groups = {
        task: [r for r in runs if r.is_correct]
        for task, runs in _group_by_task(records).items()
    }
    groups = {task: runs for task, runs in groups.items() if len(runs) >= 2}
    if not groups:
        return None
    getters: list[Callable[[RunRecord], float]] = [lambda r: r.duration_s]
    if all(r.api_calls is not None for r in records):
        getters.append(lambda r: float(r.api_calls or 0))
    if all(r.tokens is not None for r in records):
        getters.append(lambda r: float(r.tokens or 0))

    per_resource = [
        statistics.fmean(_cv([get(r) for r in runs]) for runs in groups.values())
        for get in getters
    ]
    return math.exp(-statistics.fmean(per_resource))
