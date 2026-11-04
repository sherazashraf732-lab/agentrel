from collections.abc import Callable

import pytest

from agentrel.core.runner import AgentOutput, RunRecord, Task, run_agent
from agentrel.metrics.robustness import accuracy, robustness_ratio
from agentrel.perturb.faults import InjectedFault, inject_faults, with_retries
from agentrel.perturb.prompts import paraphrased_tasks


def recs(outcomes: list[bool]) -> list[RunRecord]:
    return [
        RunRecord(
            task_id=f"t{i}",
            run_index=0,
            output_text="x",
            confidence=None,
            is_correct=ok,
            duration_s=1.0,
        )
        for i, ok in enumerate(outcomes)
    ]


# ---------- metrics ----------


def test_accuracy() -> None:
    assert accuracy(recs([True, True, False, False])) == 0.5


def test_accuracy_empty_raises() -> None:
    with pytest.raises(ValueError):
        accuracy([])


def test_ratio_no_degradation_is_one() -> None:
    assert robustness_ratio(recs([True, False]), recs([True, False])) == 1.0


def test_ratio_half_degradation() -> None:
    assert robustness_ratio(recs([True] * 4), recs([True, True, False, False])) == 0.5


def test_ratio_clamped_when_perturbed_is_better() -> None:
    assert robustness_ratio(recs([True, False]), recs([True, True])) == 1.0


def test_ratio_undefined_when_baseline_zero() -> None:
    assert robustness_ratio(recs([False, False]), recs([True])) is None


# ---------- fault injection ----------


def echo(x: str) -> str:
    return x


def test_no_faults_at_p_zero() -> None:
    f = inject_faults(echo, p_fault=0.0)
    assert all(f("a") == "a" for _ in range(100))


def test_always_faults_at_p_one() -> None:
    f = inject_faults(echo, p_fault=1.0)
    with pytest.raises(InjectedFault):
        f("a")


def test_fault_rate_close_to_p() -> None:
    f = inject_faults(echo, p_fault=0.2, seed=1)
    failures = 0
    for _ in range(2000):
        try:
            f("a")
        except InjectedFault:
            failures += 1
    assert abs(failures / 2000 - 0.2) < 0.03


def test_invalid_p_fault_raises() -> None:
    with pytest.raises(ValueError):
        inject_faults(echo, p_fault=1.5)


def test_retries_recover_from_faults() -> None:
    f = with_retries(inject_faults(echo, p_fault=0.5, seed=2), max_retries=5)
    successes = 0
    for _ in range(1000):
        try:
            successes += f("a") == "a"
        except InjectedFault:
            pass
    # without retries ~50% succeed; with 5 retries 1 - 0.5**6 ~ 98.4%
    assert successes / 1000 > 0.95


def test_retries_give_up_and_reraise() -> None:
    f = with_retries(inject_faults(echo, p_fault=1.0), max_retries=2)
    with pytest.raises(InjectedFault):
        f("a")


def test_invalid_retries_raises() -> None:
    with pytest.raises(ValueError):
        with_retries(echo, max_retries=-1)


# ---------- integration: retries improve fault robustness ----------


def make_agent(call: Callable[[str], str]) -> Callable[[Task], AgentOutput]:
    def agent(task: Task) -> AgentOutput:
        return AgentOutput(text=call(task.correct_answer))

    return agent


def test_retries_improve_fault_robustness() -> None:
    tasks = [Task(id=f"t{i}", prompt="q", correct_answer="a") for i in range(20)]
    baseline = run_agent(make_agent(echo), tasks, n_runs=5)
    faulty = run_agent(make_agent(inject_faults(echo, 0.2, seed=3)), tasks, n_runs=5)
    hardened = run_agent(
        make_agent(with_retries(inject_faults(echo, 0.2, seed=3), max_retries=3)),
        tasks,
        n_runs=5,
    )
    r_faulty = robustness_ratio(baseline, faulty)
    r_hardened = robustness_ratio(baseline, hardened)
    assert r_faulty is not None and r_hardened is not None
    assert r_faulty < 0.9
    assert r_hardened > r_faulty


# ---------- prompt paraphrases ----------


def test_paraphrased_tasks_keep_id_and_answer() -> None:
    tasks = [Task(id="t1", prompt="2+2?", correct_answer="4")]
    out = paraphrased_tasks(tasks, {"t1": ["What is two plus two?", "Add 2 and 2."]})
    assert [t.prompt for t in out] == ["What is two plus two?", "Add 2 and 2."]
    assert all(t.id == "t1" and t.correct_answer == "4" for t in out)


def test_tasks_without_paraphrases_are_skipped() -> None:
    tasks = [Task(id="t1", prompt="a", correct_answer="b")]
    assert paraphrased_tasks(tasks, {}) == []
