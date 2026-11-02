from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agentrel.cli import app
from agentrel.llm.agent import Messages
from agentrel.pipeline import RunConfig, TaskSpec, evaluate, load_config, save_results

EXAMPLES = Path(__file__).parent.parent / "examples"


def config(**overrides: object) -> RunConfig:
    base: dict[str, object] = {
        "name": "t",
        "model": "mock:0.8",
        "n_runs": 3,
        "tasks": [
            TaskSpec(id="a", prompt="2+2?", answer="4", paraphrases=["Two plus two?"]),
            TaskSpec(id="b", prompt="Capital of France?", answer="Paris"),
        ],
    }
    base.update(overrides)
    return RunConfig.model_validate(base)


def test_example_configs_are_valid() -> None:
    for path in EXAMPLES.glob("*.yaml"):
        assert load_config(path).tasks


def test_config_rejects_empty_tasks() -> None:
    with pytest.raises(ValidationError):
        config(tasks=[])


def test_evaluate_mock_produces_all_run_kinds() -> None:
    report, runs = evaluate(config(fault_probability=0.3))
    assert set(runs) == {"baseline", "fault", "prompt"}
    assert len(runs["baseline"]) == 2 * 3
    assert len(runs["prompt"]) == 1  # one paraphrase, one run
    assert report.robustness_fault is not None


def test_evaluate_without_perturbations() -> None:
    report, runs = evaluate(config(tasks=[TaskSpec(id="a", prompt="q", answer="x")]))
    assert set(runs) == {"baseline"}
    assert report.robustness is None


def test_real_model_path_with_fake_completion() -> None:
    def fake(model: str, messages: Messages) -> str:
        if "confident" in messages[-1]["content"]:
            return "90"
        return "4" if "2+2" in messages[-1]["content"] else "Paris"

    report, _runs = evaluate(config(model="ollama/fake"), complete=fake)
    assert report.accuracy == 1.0
    assert report.predictability_brier == pytest.approx(1 - 0.1**2)


def test_retries_wrap_faulty_model_calls() -> None:
    calls = {"n": 0}

    def fake(model: str, messages: Messages) -> str:
        calls["n"] += 1
        return "90" if "confident" in messages[-1]["content"] else "4"

    cfg = config(model="ollama/fake", fault_probability=0.5, max_retries=4)
    report, _runs = evaluate(cfg, complete=fake)
    assert report.robustness_fault is not None and report.robustness_fault > 0.8


def test_save_results_writes_files(tmp_path: Path) -> None:
    report, runs = evaluate(config(fault_probability=0.2))
    save_results(report, runs, tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {
        "baseline.jsonl",
        "fault.jsonl",
        "prompt.jsonl",
        "report.json",
    }


def test_cli_run_mock(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    out = tmp_path / "out"
    cfg.write_text(
        "name: cli\nmodel: 'mock:0.9'\nn_runs: 2\n"
        f"output_dir: '{out.as_posix()}'\n"
        "tasks:\n  - {id: a, prompt: q, answer: x}\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["run", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "Agent Reliability Report: cli" in result.output
    assert (out / "report.json").exists()


def test_cli_min_overall_gate_fails(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "name: gate\nmodel: 'mock:0.5'\nn_runs: 3\n"
        f"output_dir: '{(tmp_path / 'o').as_posix()}'\n"
        "tasks:\n  - {id: a, prompt: q, answer: x}\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["run", str(cfg), "--min-overall", "0.99"])
    assert result.exit_code == 1
