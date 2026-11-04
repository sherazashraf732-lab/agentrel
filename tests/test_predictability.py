import pytest

from agentrel.core.runner import AgentOutput, RunRecord, Task, run_agent
from agentrel.llm.mock import MockLLM
from agentrel.metrics.predictability import (
    auroc_predictability,
    brier_predictability,
    calibration_predictability,
)


def recs(pairs: list[tuple[float | None, bool]]) -> list[RunRecord]:
    return [
        RunRecord(
            task_id=f"t{i}",
            run_index=0,
            output_text="x",
            confidence=c,
            is_correct=ok,
            duration_s=1.0,
        )
        for i, (c, ok) in enumerate(pairs)
    ]


# ---------- Brier ----------


def test_brier_perfect() -> None:
    assert brier_predictability(recs([(1.0, True), (0.0, False)])) == 1.0


def test_brier_worst() -> None:
    assert brier_predictability(recs([(0.0, True), (1.0, False)])) == 0.0


def test_brier_always_unsure() -> None:
    score = brier_predictability(recs([(0.5, True), (0.5, False)]))
    assert score == pytest.approx(0.75)


def test_brier_skips_runs_without_confidence() -> None:
    assert brier_predictability(recs([(1.0, True), (None, False)])) == 1.0


def test_no_confidence_at_all_raises() -> None:
    with pytest.raises(ValueError):
        brier_predictability(recs([(None, True), (None, False)]))


# ---------- Calibration ----------


def test_calibration_well_calibrated() -> None:
    records = recs([(0.8, True)] * 8 + [(0.8, False)] * 2)
    assert calibration_predictability(records) == pytest.approx(1.0)


def test_calibration_overconfident() -> None:
    records = recs([(0.9, True)] * 5 + [(0.9, False)] * 5)
    assert calibration_predictability(records) == pytest.approx(0.6)


def test_calibration_two_bins_weighted() -> None:
    # bin 0.25: all wrong -> gap 0.25 ; bin 0.95: all right -> gap 0.05
    records = recs([(0.25, False)] * 5 + [(0.95, True)] * 5)
    assert calibration_predictability(records) == pytest.approx(0.85)


def test_calibration_confidence_one_does_not_crash() -> None:
    assert calibration_predictability(recs([(1.0, True)])) == pytest.approx(1.0)


def test_calibration_invalid_bins_raises() -> None:
    with pytest.raises(ValueError):
        calibration_predictability(recs([(0.5, True)]), n_bins=0)


# ---------- AUROC ----------


def test_auroc_perfect_separation() -> None:
    records = recs([(0.9, True), (0.8, True), (0.2, False), (0.1, False)])
    assert auroc_predictability(records) == 1.0


def test_auroc_reversed() -> None:
    records = recs([(0.1, True), (0.2, True), (0.8, False), (0.9, False)])
    assert auroc_predictability(records) == 0.0


def test_auroc_partial() -> None:
    # pairs: .9>.7 yes, .9>.2 yes, .6>.7 no, .6>.2 yes -> 3/4
    records = recs([(0.9, True), (0.6, True), (0.7, False), (0.2, False)])
    assert auroc_predictability(records) == pytest.approx(0.75)


def test_auroc_ties_count_zero_like_paper() -> None:
    records = recs([(0.5, True), (0.5, False)])
    assert auroc_predictability(records) == 0.0


def test_auroc_undefined_when_all_correct() -> None:
    assert auroc_predictability(recs([(0.9, True), (0.8, True)])) is None


def test_auroc_undefined_when_all_wrong() -> None:
    assert auroc_predictability(recs([(0.9, False), (0.8, False)])) is None


# ---------- integration ----------


def test_mock_agent_is_informative_but_miscalibrated() -> None:
    llm = MockLLM(accuracy=0.5, seed=0)

    def agent(task: Task) -> AgentOutput:
        r = llm.answer(task.prompt, task.correct_answer)
        return AgentOutput(text=r.text, confidence=r.confidence)

    tasks = [Task(id=f"t{i}", prompt="q", correct_answer="a") for i in range(20)]
    records = run_agent(agent, tasks, n_runs=25)

    auroc = auroc_predictability(records)
    assert auroc is not None and auroc > 0.7  # theory says about 0.81
    assert 0.0 <= brier_predictability(records) <= 1.0
    assert 0.0 <= calibration_predictability(records) < 1.0
