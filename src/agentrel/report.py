"""Build a reliability report from run records.

Aggregation follows Rabanser et al. (ICML 2026), Section 3.5:
- R_Con  = mean of consistency sub-metrics
- R_Pred = P_brier
- R_Rob  = mean of robustness sub-metrics
- R      = mean of R_Con, R_Pred, R_Rob (safety is reported separately)

Where a sub-metric cannot be measured yet (e.g. trajectory consistency needs
tool-call logs), we average what is available and say so in `notes`.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from pydantic import BaseModel

from agentrel.core.runner import RunRecord
from agentrel.metrics.consistency import outcome_consistency, resource_consistency
from agentrel.metrics.predictability import (
    auroc_predictability,
    brier_predictability,
    calibration_predictability,
)
from agentrel.metrics.robustness import accuracy, robustness_ratio
from agentrel.metrics.trajectory import (
    trajectory_distribution_consistency,
    trajectory_sequence_consistency,
)


class ReliabilityReport(BaseModel):
    agent_name: str
    n_tasks: int
    n_runs: int
    accuracy: float

    consistency_outcome: float
    consistency_trajectory_distribution: float | None = None
    consistency_trajectory_sequence: float | None = None
    consistency_resource: float
    consistency: float

    predictability_brier: float | None
    predictability_calibration: float | None
    predictability_auroc: float | None
    predictability: float | None

    robustness_fault: float | None
    robustness_prompt: float | None
    robustness: float | None

    overall: float | None
    notes: list[str]


def _mean_or_none(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.fmean(present) if present else None


def build_report(
    agent_name: str,
    baseline: Sequence[RunRecord],
    fault_runs: Sequence[RunRecord] | None = None,
    prompt_runs: Sequence[RunRecord] | None = None,
) -> ReliabilityReport:
    """Compute every available metric and the paper's aggregates."""
    notes: list[str] = []

    c_out = outcome_consistency(baseline)
    c_res = resource_consistency(baseline)
    c_traj_d = trajectory_distribution_consistency(baseline)
    c_traj_s = trajectory_sequence_consistency(baseline)
    c_traj = _mean_or_none([c_traj_d, c_traj_s])
    if c_traj is None:
        notes.append("trajectory consistency not measured (agent reported no tool calls)")
    # Paper eq. (1): R_Con = mean(C_out, C_traj, C_res), C_traj = mean(d, s)
    consistency = statistics.fmean(v for v in (c_out, c_traj, c_res) if v is not None)

    has_confidence = any(r.confidence is not None for r in baseline)
    if has_confidence:
        brier: float | None = brier_predictability(baseline)
        calibration: float | None = calibration_predictability(baseline)
        auroc = auroc_predictability(baseline)
        if auroc is None:
            notes.append("AUROC undefined: all runs had the same outcome")
    else:
        brier = calibration = auroc = None
        notes.append("predictability not measured: agent gave no confidence")

    r_fault = robustness_ratio(baseline, fault_runs) if fault_runs else None
    r_prompt = robustness_ratio(baseline, prompt_runs) if prompt_runs else None
    if not fault_runs:
        notes.append("fault robustness not measured")
    if not prompt_runs:
        notes.append("prompt robustness not measured")
    notes.append("environment robustness not measured (needs tool interfaces)")
    robustness = _mean_or_none([r_fault, r_prompt])

    return ReliabilityReport(
        agent_name=agent_name,
        n_tasks=len({r.task_id for r in baseline}),
        n_runs=len(baseline),
        accuracy=accuracy(baseline),
        consistency_outcome=c_out,
        consistency_trajectory_distribution=c_traj_d,
        consistency_trajectory_sequence=c_traj_s,
        consistency_resource=c_res,
        consistency=consistency,
        predictability_brier=brier,
        predictability_calibration=calibration,
        predictability_auroc=auroc,
        predictability=brier,
        robustness_fault=r_fault,
        robustness_prompt=r_prompt,
        robustness=robustness,
        overall=_mean_or_none([consistency, brier, robustness]),
        notes=notes,
    )


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.2f}"


def format_report(report: ReliabilityReport) -> str:
    """Plain-text report for the terminal."""
    lines = [
        f"Agent Reliability Report: {report.agent_name}",
        "-" * 44,
        f"Tasks: {report.n_tasks}   Runs: {report.n_runs}",
        f"Accuracy                   {report.accuracy:5.0%}",
        "",
        f"Consistency                {_fmt(report.consistency)}",
        f"  outcome                  {_fmt(report.consistency_outcome)}",
        f"  trajectory (tools used)  {_fmt(report.consistency_trajectory_distribution)}",
        f"  trajectory (tool order)  {_fmt(report.consistency_trajectory_sequence)}",
        f"  resource                 {_fmt(report.consistency_resource)}",
        f"Predictability (Brier)     {_fmt(report.predictability)}",
        f"  calibration              {_fmt(report.predictability_calibration)}",
        f"  discrimination (AUROC)   {_fmt(report.predictability_auroc)}",
        f"Robustness                 {_fmt(report.robustness)}",
        f"  fault                    {_fmt(report.robustness_fault)}",
        f"  prompt                   {_fmt(report.robustness_prompt)}",
        "-" * 44,
        f"Overall reliability        {_fmt(report.overall)}",
        "",
        "Notes:",
        *[f"  - {note}" for note in report.notes],
    ]
    return "\n".join(lines)
