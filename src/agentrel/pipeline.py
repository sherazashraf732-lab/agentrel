"""Config file -> runs -> report. The CLI is a thin layer over this module."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from agentrel.core.runner import (
    Agent,
    AgentOutput,
    RunRecord,
    Task,
    run_agent,
    save_records,
)
from agentrel.llm.agent import CompletionFn, litellm_completion, make_llm_agent
from agentrel.llm.mock import MockLLM
from agentrel.perturb.faults import inject_faults, with_retries
from agentrel.perturb.prompts import paraphrased_tasks
from agentrel.report import ReliabilityReport, build_report


class TaskSpec(BaseModel):
    id: str
    prompt: str
    answer: str
    paraphrases: list[str] = Field(default_factory=list)


class RunConfig(BaseModel):
    name: str
    model: str  # e.g. "ollama/qwen3:4b", "groq/llama-3.1-8b-instant", "mock:0.8"
    n_runs: int = Field(default=5, ge=1)
    fault_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    max_retries: int = Field(default=0, ge=0)
    seed: int = 0
    output_dir: str = "agentrel-results"
    tasks: list[TaskSpec] = Field(min_length=1)


def load_config(path: Path) -> RunConfig:
    with path.open(encoding="utf-8") as f:
        return RunConfig.model_validate(yaml.safe_load(f))


def _mock_agent(model: str, seed: int, p_fault: float | None) -> Agent:
    """Simulator for demos and CI: 'mock' or 'mock:<accuracy>'.

    Unlike real agents it reads task.correct_answer, because it fakes answers.
    """
    _, _, acc = model.partition(":")
    llm = MockLLM(accuracy=float(acc) if acc else 0.8, seed=seed)

    def agent(task: Task) -> AgentOutput:
        r = llm.answer(task.prompt, task.correct_answer)
        return AgentOutput(text=r.text, confidence=r.confidence)

    return inject_faults(agent, p_fault, seed) if p_fault else agent


def build_agent(
    config: RunConfig,
    p_fault: float | None = None,
    complete: CompletionFn = litellm_completion,
) -> Agent:
    """Faults are injected into the model call; retries (if any) wrap that call."""
    if config.model.startswith("mock"):
        return _mock_agent(config.model, config.seed, p_fault)
    call = complete
    if p_fault:
        call = inject_faults(call, p_fault, config.seed)
    if config.max_retries:
        call = with_retries(call, config.max_retries)
    return make_llm_agent(config.model, complete=call)


def evaluate(
    config: RunConfig, complete: CompletionFn = litellm_completion
) -> tuple[ReliabilityReport, dict[str, list[RunRecord]]]:
    tasks = [Task(id=t.id, prompt=t.prompt, correct_answer=t.answer) for t in config.tasks]
    runs: dict[str, list[RunRecord]] = {
        "baseline": run_agent(build_agent(config, complete=complete), tasks, config.n_runs)
    }
    if config.fault_probability:
        faulty = build_agent(config, config.fault_probability, complete)
        runs["fault"] = run_agent(faulty, tasks, config.n_runs)
    variants = paraphrased_tasks(tasks, {t.id: t.paraphrases for t in config.tasks})
    if variants:
        runs["prompt"] = run_agent(build_agent(config, complete=complete), variants, 1)

    report = build_report(
        config.name,
        runs["baseline"],
        fault_runs=runs.get("fault"),
        prompt_runs=runs.get("prompt"),
    )
    return report, runs


def save_results(
    report: ReliabilityReport, runs: dict[str, list[RunRecord]], output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for kind, records in runs.items():
        save_records(records, output_dir / f"{kind}.jsonl")
    (output_dir / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
