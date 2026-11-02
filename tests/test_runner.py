from collections.abc import Callable
from pathlib import Path

import pytest

from agentrel.core.runner import (
    AgentOutput,
    Task,
    load_records,
    run_agent,
    save_records,
)
from agentrel.llm.mock import MockLLM

TASKS = [
    Task(id="t1", prompt="2 + 2?", correct_answer="4"),
    Task(id="t2", prompt="Capital of France?", correct_answer="Paris"),
]


def mock_agent(accuracy: float, seed: int = 0) -> Callable[[Task], AgentOutput]:
    """Test-only wrapper: MockLLM needs the answer to fake one."""
    llm = MockLLM(accuracy=accuracy, seed=seed)

    def agent(task: Task) -> AgentOutput:
        r = llm.answer(task.prompt, task.correct_answer)
        return AgentOutput(text=r.text, confidence=r.confidence)

    return agent


def test_record_count() -> None:
    records = run_agent(mock_agent(0.8), TASKS, n_runs=5)
    assert len(records) == len(TASKS) * 5


def test_run_indexes_per_task() -> None:
    records = run_agent(mock_agent(0.8), TASKS, n_runs=3)
    for task in TASKS:
        idx = [r.run_index for r in records if r.task_id == task.id]
        assert idx == [0, 1, 2]


def test_runner_judges_all_correct() -> None:
    records = run_agent(mock_agent(1.0), TASKS, n_runs=4)
    assert all(r.is_correct for r in records)


def test_runner_judges_all_wrong() -> None:
    records = run_agent(mock_agent(0.0), TASKS, n_runs=4)
    assert not any(r.is_correct for r in records)


def test_correctness_ignores_surrounding_whitespace() -> None:
    def agent(task: Task) -> AgentOutput:
        return AgentOutput(text=f"  {task.correct_answer}\n")

    records = run_agent(agent, TASKS, n_runs=1)
    assert all(r.is_correct for r in records)


def test_confidence_is_recorded() -> None:
    records = run_agent(mock_agent(0.8), TASKS, n_runs=2)
    for r in records:
        assert r.confidence is not None
        assert 0.0 <= r.confidence <= 1.0


def test_agent_without_confidence() -> None:
    def agent(task: Task) -> AgentOutput:
        return AgentOutput(text=task.correct_answer)

    records = run_agent(agent, TASKS, n_runs=1)
    assert all(r.confidence is None for r in records)


def test_agent_crash_is_recorded_not_raised() -> None:
    calls = {"n": 0}

    def flaky_agent(task: Task) -> AgentOutput:
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            raise RuntimeError("tool timeout")
        return AgentOutput(text=task.correct_answer, confidence=0.9)

    records = run_agent(flaky_agent, TASKS, n_runs=2)
    assert len(records) == 4
    failed = [r for r in records if r.error is not None]
    assert len(failed) == 2
    for r in failed:
        assert r.error is not None and "tool timeout" in r.error
        assert r.output_text is None
        assert r.is_correct is False


@pytest.mark.parametrize("bad", [0, -1])
def test_invalid_n_runs_raises(bad: int) -> None:
    with pytest.raises(ValueError):
        run_agent(mock_agent(0.8), TASKS, n_runs=bad)


def test_empty_tasks_returns_empty() -> None:
    assert run_agent(mock_agent(0.8), [], n_runs=3) == []


def test_duration_non_negative() -> None:
    records = run_agent(mock_agent(0.8), TASKS, n_runs=2)
    assert all(r.duration_s >= 0 for r in records)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    records = run_agent(mock_agent(0.8), TASKS, n_runs=3)
    path = tmp_path / "runs.jsonl"
    save_records(records, path)
    assert load_records(path) == records