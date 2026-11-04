"""Predictability metrics from Rabanser et al., "Towards a Science of AI Agent
Reliability" (ICML 2026), Table 2. Scores in [0, 1]; higher is better.

Our choices where the paper is not explicit:
- Each run is one sample; runs without a confidence (e.g. crashes) are skipped.
- Calibration uses equal-width bins (default 10).
- AUROC follows the paper's strict `>`: ties score 0 (standard AUROC uses 0.5).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from agentrel.core.runner import RunRecord


def _confidence_pairs(records: Sequence[RunRecord]) -> list[tuple[float, int]]:
    """(confidence, outcome) for every run that reported a confidence."""
    pairs: list[tuple[float, int]] = []
    for record in records:
        if record.confidence is not None:
            pairs.append((record.confidence, int(record.is_correct)))
    if not pairs:
        raise ValueError("no records with a confidence score")
    return pairs


def brier_predictability(records: Sequence[RunRecord]) -> float:
    """P_brier = 1 - mean((c - y)^2). The paper's overall R_Pred."""
    pairs = _confidence_pairs(records)
    return 1.0 - statistics.fmean((c - y) ** 2 for c, y in pairs)


def calibration_predictability(records: Sequence[RunRecord], n_bins: int = 10) -> float:
    """P_cal = 1 - ECE, with equal-width confidence bins."""
    if n_bins < 1:
        raise ValueError(f"n_bins must be at least 1, got {n_bins}")
    pairs = _confidence_pairs(records)

    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for c, y in pairs:
        index = min(int(c * n_bins), n_bins - 1)  # c == 1.0 goes in the last bin
        bins[index].append((c, y))

    total = len(pairs)
    ece = 0.0
    for bin_pairs in bins:
        if bin_pairs:
            mean_conf = statistics.fmean(c for c, _ in bin_pairs)
            accuracy = statistics.fmean(y for _, y in bin_pairs)
            ece += len(bin_pairs) / total * abs(accuracy - mean_conf)
    return 1.0 - ece


def auroc_predictability(records: Sequence[RunRecord]) -> float | None:
    """P_AUROC = share of (success, failure) pairs where success is more confident.

    Returns None when there are no successes or no failures (undefined).
    O(n * m); fine for evaluation-sized data.
    """
    pairs = _confidence_pairs(records)
    successes = [c for c, y in pairs if y == 1]
    failures = [c for c, y in pairs if y == 0]
    if not successes or not failures:
        return None
    wins = sum(1 for s in successes for f in failures if s > f)
    return wins / (len(successes) * len(failures))
