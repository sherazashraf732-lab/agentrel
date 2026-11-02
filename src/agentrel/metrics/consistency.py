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


def resource_consistency(records: Sequence[RunRecord]) -> float:
    """C_res = exp(-mean over resource types of CV).

    Resources: run duration always; model calls and tokens when every run
    reports them (tool agents do, plain Q&A agents don't).

    Our choices where the paper is not explicit: for each resource, CV is
    computed per task (population std) and averaged across tasks.
    """
    groups = _group_by_task(records)
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