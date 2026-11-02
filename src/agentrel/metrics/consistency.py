"""Consistency metrics from Rabanser et al., "Towards a Science of AI Agent
Reliability" (ICML 2026), Table 2. All scores are in [0, 1]; higher is better.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence

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


def resource_consistency(records: Sequence[RunRecord]) -> float:
    """C_res = exp(-mean CV), using run duration as the resource.

    Our choices where the paper is not explicit: CV is computed per task and
    averaged across tasks, using the population standard deviation.
    """
    cvs: list[float] = []
    for task_runs in _group_by_task(records).values():
        durations = [r.duration_s for r in task_runs]
        mean = statistics.fmean(durations)
        cvs.append(statistics.pstdev(durations) / (mean + EPS))
    return math.exp(-statistics.fmean(cvs))