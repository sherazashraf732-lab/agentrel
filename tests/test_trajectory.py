import math

import pytest

from agentrel.core.runner import RunRecord
from agentrel.metrics.consistency import resource_consistency
from agentrel.metrics.trajectory import (
    jensen_shannon,
    normalized_levenshtein,
    trajectory_distribution_consistency,
    trajectory_sequence_consistency,
)


def run(
    task: str,
    actions: list[str],
    duration: float = 1.0,
    calls: int | None = None,
    tokens: int | None = None,
) -> RunRecord:
    return RunRecord(
        task_id=task,
        run_index=0,
        output_text="x",
        confidence=None,
        is_correct=True,
        duration_s=duration,
        actions=actions,
        api_calls=calls,
        tokens=tokens,
    )


# ---------- distances ----------

def test_jsd_identical_is_zero() -> None:
    assert jensen_shannon(["a", "b"], ["b", "a"]) == pytest.approx(0.0)


def test_jsd_disjoint_is_one() -> None:
    assert jensen_shannon(["a"], ["b"]) == pytest.approx(1.0)


def test_jsd_partial_overlap_known_value() -> None:
    # p = (1, 0), q = (0.5, 0.5): JSD = 1.5 - 0.75 * log2(3) ~ 0.311
    expected = 1.5 - 0.75 * math.log2(3)
    assert jensen_shannon(["a"], ["a", "b"]) == pytest.approx(expected)


def test_jsd_empty_cases() -> None:
    assert jensen_shannon([], []) == 0.0
    assert jensen_shannon([], ["a"]) == 1.0


def test_levenshtein_examples() -> None:
    assert normalized_levenshtein(["a", "b", "c"], ["a", "b", "c"]) == 0.0
    assert normalized_levenshtein(["a", "b"], ["b", "a"]) == pytest.approx(1.0)
    assert normalized_levenshtein(["a", "b", "c"], ["a", "c"]) == pytest.approx(1 / 3)
    assert normalized_levenshtein([], []) == 0.0


# ---------- trajectory consistency ----------

def test_same_tools_different_order_what_but_not_when() -> None:
    # the paper's "what but not when" pattern
    records = [run("t", ["get", "refund"]), run("t", ["refund", "get"])]
    assert trajectory_distribution_consistency(records) == pytest.approx(1.0)
    assert trajectory_sequence_consistency(records) == pytest.approx(0.0)


def test_identical_trajectories_score_one() -> None:
    records = [run("t", ["get", "refund"])] * 3
    assert trajectory_distribution_consistency(records) == pytest.approx(1.0)
    assert trajectory_sequence_consistency(records) == pytest.approx(1.0)


def test_averaged_over_tasks() -> None:
    records = [run("a", ["x"]), run("a", ["x"]), run("b", ["x"]), run("b", ["y"])]
    assert trajectory_sequence_consistency(records) == pytest.approx(0.5)


def test_none_when_agent_reports_no_actions() -> None:
    records = [run("t", []), run("t", [])]
    assert trajectory_distribution_consistency(records) is None
    assert trajectory_sequence_consistency(records) is None


def test_none_when_single_run_per_task() -> None:
    assert trajectory_sequence_consistency([run("a", ["x"]), run("b", ["y"])]) is None


# ---------- multi-resource consistency ----------

def test_resource_uses_calls_and_tokens_when_reported() -> None:
    # duration constant (CV 0), calls constant (CV 0), tokens 100 vs 300 (CV 0.5)
    records = [run("t", ["x"], 1.0, 2, 100), run("t", ["x"], 1.0, 2, 300)]
    assert resource_consistency(records) == pytest.approx(math.exp(-0.5 / 3), rel=1e-6)


def test_resource_ignores_calls_if_some_runs_lack_them() -> None:
    records = [run("t", ["x"], 1.0, 2, None), run("t", ["x"], 1.0, None, None)]
    assert resource_consistency(records) == pytest.approx(1.0)
