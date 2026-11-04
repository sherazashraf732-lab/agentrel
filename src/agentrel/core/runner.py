"""Run an agent on tasks multiple times and record every run."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Task(BaseModel):
    """One question for the agent.

    Note: the runner passes the whole Task to the agent. Real-model adapters
    must forward only `prompt`, never `correct_answer`.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    prompt: str
    correct_answer: str


class AgentOutput(BaseModel):
    """What an agent returns for one task."""

    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # Optional extras for tool-using agents (all default to "not reported").
    actions: list[str] = Field(default_factory=list)  # tool names, in call order
    api_calls: int | None = None  # model calls made
    tokens: int | None = None  # tokens used
    transcript: list[dict[str, Any]] = Field(default_factory=list)  # every message
    # Set when the agent's environment grades success itself (e.g. final
    # database state); otherwise the runner compares `text` to the answer.
    is_correct: bool | None = None
    # Safety violations found by the environment: None = not checked,
    # [] = checked and clean. Each: {"constraint", "severity", "detail"}.
    violations: list[dict[str, str]] | None = None


class RunRecord(BaseModel):
    """Everything recorded about one run of one task."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    run_index: int
    output_text: str | None
    confidence: float | None
    is_correct: bool
    duration_s: float
    error: str | None = None
    actions: list[str] = Field(default_factory=list)
    api_calls: int | None = None
    tokens: int | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    violations: list[dict[str, str]] | None = None


Agent = Callable[[Task], AgentOutput]


def _normalize(text: str) -> str:
    return text.strip().rstrip(".!").strip().casefold()


def _is_correct(output: str, expected: str) -> bool:
    """Match ignoring case, surrounding whitespace and a trailing '.' or '!'."""
    return _normalize(output) == _normalize(expected)


def run_agent(agent: Agent, tasks: Sequence[Task], n_runs: int = 5) -> list[RunRecord]:
    """Run `agent` on every task `n_runs` times. Agent crashes are recorded."""
    if n_runs < 1:
        raise ValueError(f"n_runs must be at least 1, got {n_runs}")

    records: list[RunRecord] = []
    for task in tasks:
        for run_index in range(n_runs):
            start = time.perf_counter()
            try:
                output = agent(task)
            except Exception as exc:  # noqa: BLE001 - agent failures are data, not crashes
                records.append(
                    RunRecord(
                        task_id=task.id,
                        run_index=run_index,
                        output_text=None,
                        confidence=None,
                        is_correct=False,
                        duration_s=time.perf_counter() - start,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            is_correct = (
                output.is_correct
                if output.is_correct is not None
                else _is_correct(output.text, task.correct_answer)
            )
            records.append(
                RunRecord(
                    task_id=task.id,
                    run_index=run_index,
                    output_text=output.text,
                    confidence=output.confidence,
                    is_correct=is_correct,
                    duration_s=time.perf_counter() - start,
                    actions=output.actions,
                    api_calls=output.api_calls,
                    tokens=output.tokens,
                    transcript=output.transcript,
                    violations=output.violations,
                )
            )
    return records


def save_records(records: Sequence[RunRecord], path: Path) -> None:
    """Write one JSON object per line (JSONL)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")


def load_records(path: Path) -> list[RunRecord]:
    """Read records written by `save_records`."""
    with path.open(encoding="utf-8") as f:
        return [RunRecord.model_validate_json(line) for line in f if line.strip()]
