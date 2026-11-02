import pytest

from agentrel.core.runner import AgentOutput, Task, run_agent
from agentrel.llm.mock import MockLLM
from agentrel.perturb.faults import inject_faults
from agentrel.report import ReliabilityReport, build_report, format_report

TASKS = [Task(id=f"t{i}", prompt=f"q{i}", correct_answer="a") for i in range(10)]


def perfect_agent(task: Task) -> AgentOutput:
    return AgentOutput(text=task.correct_answer, confidence=1.0)


def no_confidence_agent(task: Task) -> AgentOutput:
    return AgentOutput(text=task.correct_answer)


MOCK_LLM = MockLLM(accuracy=0.7, seed=0)


def mock_agent(task: Task) -> AgentOutput:
    r = MOCK_LLM.answer(task.prompt, task.correct_answer)
    return AgentOutput(text=r.text, confidence=r.confidence)


def test_perfect_agent_scores() -> None:
    report = build_report("perfect", run_agent(perfect_agent, TASKS, n_runs=3))
    assert report.accuracy == 1.0
    assert report.consistency_outcome == 1.0
    assert report.predictability == 1.0
    assert report.predictability_auroc is None  # no failures to separate
    assert report.robustness is None


def test_overall_averages_available_dimensions() -> None:
    report = build_report("perfect", run_agent(perfect_agent, TASKS, n_runs=3))
    assert report.predictability is not None
    expected = (report.consistency + report.predictability) / 2
    assert report.overall == pytest.approx(expected)


def test_no_confidence_skips_predictability() -> None:
    report = build_report("plain", run_agent(no_confidence_agent, TASKS, n_runs=2))
    assert report.predictability is None
    assert any("predictability not measured" in n for n in report.notes)


def test_robustness_included_when_perturbed_runs_given() -> None:
    def faulty_agent(task: Task) -> AgentOutput:
        return perfect_agent(task)

    flaky = inject_faults(faulty_agent, p_fault=0.5, seed=1)
    baseline = run_agent(perfect_agent, TASKS, n_runs=4)
    fault_runs = run_agent(flaky, TASKS, n_runs=4)
    report = build_report("x", baseline, fault_runs=fault_runs, prompt_runs=baseline)
    assert report.robustness_prompt == 1.0
    assert report.robustness_fault is not None and report.robustness_fault < 1.0
    assert report.robustness is not None


def test_mock_report_values_in_range() -> None:
    report = build_report("mock", run_agent(mock_agent, TASKS, n_runs=5))
    for value in (report.consistency, report.predictability, report.overall):
        assert value is not None and 0.0 <= value <= 1.0


def test_format_report_contains_key_lines() -> None:
    report = build_report("mock", run_agent(mock_agent, TASKS, n_runs=5))
    text = format_report(report)
    assert "Agent Reliability Report: mock" in text
    assert "Overall reliability" in text
    assert "n/a" in text  # robustness not measured


def test_report_json_roundtrip() -> None:
    report = build_report("mock", run_agent(mock_agent, TASKS, n_runs=5))
    again = ReliabilityReport.model_validate_json(report.model_dump_json())
    assert again == report
