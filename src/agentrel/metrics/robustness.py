"""Robustness metrics from Rabanser et al., "Towards a Science of AI Agent
Reliability" (ICML 2026), Table 2.

Each robustness metric is a clamped accuracy ratio:
    R = min(Acc_perturbed / Acc_baseline, 1)
used for fault (R_fault), environment (R_env) and prompt (R_prompt) robustness.

Our choice where the paper is not explicit: if baseline accuracy is 0 the
ratio is undefined and we return None instead of inventing a number.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from agentrel.core.runner import RunRecord


def accuracy(records: Sequence[RunRecord]) -> float:
    """Share of runs that were correct. Crashed runs count as wrong."""
    if not records:
        raise ValueError("records must not be empty")
    return statistics.fmean(int(r.is_correct) for r in records)


def robustness_ratio(
    baseline: Sequence[RunRecord], perturbed: Sequence[RunRecord]
) -> float | None:
    """min(Acc_perturbed / Acc_baseline, 1); None if baseline accuracy is 0."""
    acc_base = accuracy(baseline)
    if acc_base == 0:
        return None
    return min(accuracy(perturbed) / acc_base, 1.0)
