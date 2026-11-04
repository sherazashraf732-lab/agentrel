"""Reliability Lab: runs the paper's evaluation on the support agent and stores the results.

A run is the agentrel store protocol (Rabanser et al., ICML 2026): the same 8 customer
scenarios under four conditions (baseline, tool faults, changed tool formats, reworded
requests), repeated k times. The 12 metrics, per-task outcomes and every safety
violation are saved so the admin console can chart and audit them.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from kavrelle.db import session_scope
from kavrelle.models import METRIC_KEYS, SEVERITIES, EvalRun, MetricResult, Violation

log = logging.getLogger("kavrelle.lab")

# report field (agentrel.report.ReliabilityReport) for each of the paper's 12 metrics
REPORT_FIELDS = {
    "C_out": "consistency_outcome",
    "C_traj_d": "consistency_trajectory_distribution",
    "C_traj_s": "consistency_trajectory_sequence",
    "C_res": "consistency_resource",
    "R_fault": "robustness_fault",
    "R_env": "robustness_env",
    "R_prompt": "robustness_prompt",
    "P_cal": "predictability_calibration",
    "P_auroc": "predictability_auroc",
    "P_brier": "predictability_brier",
    "S_comp": "safety_compliance",
    "S_harm": "safety_harm",
}
assert set(REPORT_FIELDS) == set(METRIC_KEYS)
CONDITIONS = ("baseline", "fault", "environment", "prompt")


def _clamp(v: Any) -> float | None:
    if v is None:
        return None
    f = float(v)
    if math.isnan(f):
        return None
    return max(0.0, min(1.0, f))


def summarize_records(runs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Per-task, per-condition success, duration and tokens, for the console tables."""
    tasks: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for cond, records in runs.items():
        by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            base = str(r.get("task_id", "")).split("~")[0].split("#")[0]
            by_task[base].append(r)
        for task, rs in by_task.items():
            n = len(rs)
            tasks[task][cond] = {
                "runs": n,
                "success": sum(bool(r.get("is_correct")) for r in rs) / n,
                "mean_seconds": sum(float(r.get("duration_s") or 0) for r in rs) / n,
                "mean_tokens": sum(float(r.get("tokens") or 0) for r in rs) / n,
                "mean_confidence": sum(float(r.get("confidence") or 0) for r in rs) / n,
                "errors": sum(bool(r.get("error")) for r in rs),
            }
    total = sum(len(v) for v in runs.values())
    seconds = sum(float(r.get("duration_s") or 0) for v in runs.values() for r in v)
    return {
        "tasks": dict(tasks),
        "total_runs": total,
        "total_seconds": round(seconds, 1),
    }


def save_run(
    session: Session,
    *,
    name: str,
    label: str,
    report: dict[str, Any],
    runs: dict[str, list[dict[str, Any]]],
    run: EvalRun | None = None,
) -> EvalRun:
    """Store a finished evaluation (new row, or fill in a queued/running one)."""
    if run is None:
        run = EvalRun(name=name, assistant_label=label, n_runs=1)
        session.add(run)
    run.status = "done"
    run.finished_at = datetime.now(UTC)
    baseline = runs.get("baseline") or []
    tasks = {r.get("task_id") for r in baseline}
    run.n_runs = (
        max(1, len(baseline) // max(1, len(tasks)))
        if baseline
        else max(1, int(report.get("n_runs") or 1))
    )
    run.report = {"report": report, "summary": summarize_records(runs)}
    run.metrics.clear()
    session.flush()
    for key, field in REPORT_FIELDS.items():
        run.metrics.append(MetricResult(key=key, value=_clamp(report.get(field))))
    run.violations.clear()
    for cond, records in runs.items():
        for r in records:
            for v in r.get("violations") or []:
                sev = v.get("severity") if v.get("severity") in SEVERITIES else "medium"
                run.violations.append(Violation(
                    task_id=f"{r.get('task_id')} (run {r.get('run_index')}, {cond})"[:60],
                    rule=str(v.get("constraint", "unknown"))[:60], severity=sev,
                    detail=str(v.get("detail", ""))[:500]))  # fmt: skip
    session.flush()
    return run


def import_results_dir(
    session: Session, folder: Path, name: str, label: str
) -> EvalRun:
    """Load a finished `agentrel run` output folder (report.json + <condition>.jsonl)."""
    report = json.loads((folder / "report.json").read_text("utf-8"))
    runs: dict[str, list[dict[str, Any]]] = {}
    for cond in CONDITIONS:
        f = folder / f"{cond}.jsonl"
        if f.is_file():
            runs[cond] = [
                json.loads(line)
                for line in f.read_text("utf-8").splitlines()
                if line.strip()
            ]
    if session.scalar(select(EvalRun).where(EvalRun.name == name)) is not None:
        raise ValueError(f"a run named {name!r} already exists")
    return save_run(session, name=name, label=label, report=report, runs=runs)


Evaluator = Callable[
    [int, Callable[[], None]], tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]
]


def agentrel_evaluator(model: str) -> Evaluator:
    """The real thing: agentrel's store protocol on a local model."""

    def evaluate(
        n_runs: int, tick: Callable[[], None]
    ) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        from agentrel.llm.ollama import ollama_chat
        from agentrel.pipeline import RunConfig, evaluate_store

        base = ollama_chat(model)

        def chat(messages: Any, tools: Any) -> Any:
            reply = base(messages, tools)
            tick()
            return reply

        config = RunConfig(name="console", model=f"ollama/{model}", environment="store",
                           n_runs=n_runs, fault_probability=0.2, seed=int(time.time()) % 1000)  # fmt: skip
        report, runs = evaluate_store(config, chat=chat)
        return report.model_dump(), {
            k: [r.model_dump() for r in v] for k, v in runs.items()
        }

    return evaluate


class Lab:
    """Runs one evaluation at a time in the background and reports progress."""

    CALLS_PER_TASK = (
        3.5  # model calls per scenario run, including the confidence question
    )

    def __init__(
        self, factory: sessionmaker[Session], evaluator: Evaluator, label: str
    ) -> None:
        self.factory, self.evaluator, self.label = factory, evaluator, label
        self._lock = threading.Lock()
        self.active: dict[str, Any] | None = None

    def start(self, n_runs: int) -> int:
        with self._lock:
            if self.active is not None:
                raise RuntimeError("An evaluation is already running")
            with session_scope(self.factory) as s:
                stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
                run = EvalRun(
                    name=f"Console run {stamp}",
                    assistant_label=self.label,
                    n_runs=n_runs,
                    status="running",
                )
                s.add(run)
                s.flush()
                run_id = run.id
            expected = (8 * n_runs * 3 + 8) * self.CALLS_PER_TASK
            self.active = {
                "run_id": run_id,
                "calls": 0,
                "expected": expected,
                "started": time.monotonic(),
            }
        threading.Thread(
            target=self._work, args=(run_id, n_runs), daemon=True, name="kavrelle-lab"
        ).start()
        return run_id

    def progress(self) -> dict[str, Any] | None:
        a = self.active
        if a is None:
            return None
        return {"run_id": a["run_id"], "fraction": min(0.97, a["calls"] / a["expected"]),
                "model_calls": a["calls"], "elapsed_s": round(time.monotonic() - a["started"])}  # fmt: skip

    def _tick(self) -> None:
        if self.active is not None:
            self.active["calls"] += 1

    def _work(self, run_id: int, n_runs: int) -> None:
        try:
            report, runs = self.evaluator(n_runs, self._tick)
            with session_scope(self.factory) as s:
                run = s.get(EvalRun, run_id)
                assert run is not None
                save_run(
                    s,
                    name=run.name,
                    label=run.assistant_label,
                    report=report,
                    runs=runs,
                    run=run,
                )
        except Exception:
            log.exception("evaluation %s failed", run_id)
            with session_scope(self.factory) as s:
                run = s.get(EvalRun, run_id)
                if run is not None:
                    run.status, run.finished_at = "failed", datetime.now(UTC)
        finally:
            with self._lock:
                self.active = None
