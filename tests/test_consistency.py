import math

import pytest

from agentrel.core.runner import AgentOutput, RunRecord, Task, run_agent
from agentrel.llm.mock import MockLLM
from agentrel.metrics.consistency import outcome_consistency, resource_consistency


def runs(
    task_id: str, outcomes: list[bool], durations: list[float] | None = None
) -> list[RunRecord]:
    durations = durations or [1.0] * len(outcomes)
    return [
        RunRecord(
            task_id=task_id,
            run_index=i,
            output_text="x",
            confidence=None,
            is_correct=ok,
            duration_s=d,
        )
        for i, (ok, d) in enumerate(zip(outcomes, durations, strict=True))
    ]


# ---------- outcome consistency ----------


def test_always_correct_is_fully_consistent() -> None:
    assert outcome_consistency(runs("a", [True] * 5)) == 1.0


def test_always_wrong_is_also_fully_consistent() -> None:
    assert outcome_consistency(runs("a", [False] * 5)) == 1.0


def test_half_and_half_is_zero() -> None:
    assert outcome_consistency(runs("a", [True, True, False, False])) == 0.0


def test_four_of_five() -> None:
    score = outcome_consistency(runs("a", [True] * 4 + [False]))
    assert score == pytest.approx(0.36)


def test_average_across_tasks() -> None:
    records = runs("a", [True] * 4) + runs("b", [True, True, False, False])
    assert outcome_consistency(records) == pytest.approx(0.5)


def test_outcome_empty_raises() -> None:
    with pytest.raises(ValueError):
        outcome_consistency([])


# ---------- resource consistency ----------


def test_identical_durations_score_one() -> None:
    assert resource_consistency(
        runs("a", [True] * 3, [2.0, 2.0, 2.0])
    ) == pytest.approx(1.0)


def test_varying_durations_exact_value() -> None:
    # mean 2, population std 1 -> CV 0.5 -> exp(-0.5)
    score = resource_consistency(runs("a", [True, True], [1.0, 3.0]))
    assert score == pytest.approx(math.exp(-0.5), rel=1e-6)


def test_resource_averages_cv_across_tasks() -> None:
    records = runs("a", [True, True], [2.0, 2.0]) + runs("b", [True, True], [1.0, 3.0])
    assert resource_consistency(records) == pytest.approx(math.exp(-0.25), rel=1e-6)


def test_zero_durations_do_not_crash() -> None:
    assert resource_consistency(
        runs("a", [True] * 3, [0.0, 0.0, 0.0])
    ) == pytest.approx(1.0)


def test_resource_empty_raises() -> None:
    with pytest.raises(ValueError):
        resource_consistency([])


# ---------- integration with real runner ----------


def test_scores_in_range_with_mock_agent() -> None:
    llm = MockLLM(accuracy=0.8, seed=0)

    def agent(task: Task) -> AgentOutput:
        r = llm.answer(task.prompt, task.correct_answer)
        return AgentOutput(text=r.text, confidence=r.confidence)

    tasks = [Task(id=f"t{i}", prompt="q", correct_answer="a") for i in range(10)]
    records = run_agent(agent, tasks, n_runs=5)
    assert 0.0 <= outcome_consistency(records) <= 1.0
    assert 0.0 <= resource_consistency(records) <= 1.0


def test_resource_uses_only_successful_runs() -> None:
    """Appendix C.1: a failed run that stops early is not resource inconsistency."""
    records = runs("a", [True, True, False], [2.0, 2.0, 9.0])
    assert resource_consistency(records) == pytest.approx(1.0)


def test_resource_skips_tasks_with_fewer_than_two_successes() -> None:
    records = runs("a", [True, True], [1.0, 3.0]) + runs(
        "b", [True, False], [5.0, 50.0]
    )
    assert resource_consistency(records) == pytest.approx(math.exp(-0.5), rel=1e-6)


def test_resource_is_none_when_nothing_succeeded_twice() -> None:
    assert resource_consistency(runs("a", [False, False], [1.0, 2.0])) is None
