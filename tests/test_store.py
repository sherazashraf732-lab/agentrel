import random

import pytest

from agentrel.agents.tool_agent import call_tool, make_tool_agent
from agentrel.core.runner import Task
from agentrel.envs.store import (
    POLICY,
    SCENARIOS,
    Scenario,
    StoreEnv,
    store_env_factory,
    store_tasks,
)
from agentrel.llm.ollama import ChatReply, Message, ToolCall, ToolSchema
from agentrel.pipeline import RunConfig, evaluate_store

CANCEL = next(s for s in SCENARIOS if s.id == "cancel-pending")


def tools_of(env: StoreEnv) -> dict:
    return {t.name: t for t in env.tools()}


# ---------- tools ----------

def test_verify_customer() -> None:
    env = StoreEnv(CANCEL)
    tools = tools_of(env)
    assert "C1" in call_tool(tools, "verify_customer", {"email": "ali@example.com", "zip": "54000"})
    assert env.verified == "C1"
    assert "ERROR" in call_tool(tools, "verify_customer", {"email": "x@y.z", "zip": "1"})


def test_cancel_changes_state_and_logs() -> None:
    env = StoreEnv(CANCEL)
    call_tool(tools_of(env), "cancel_order", {"order_id": "A101"})
    assert env.orders["A101"]["status"] == "cancelled"
    assert env.log[-1] == {"tool": "cancel_order", "args": {"order_id": "A101"}, "verified": None}


def test_tools_do_not_enforce_policy_like_tau_bench() -> None:
    env = StoreEnv(CANCEL)
    call_tool(tools_of(env), "cancel_order", {"order_id": "A100"})  # shipped!
    assert env.orders["A100"]["status"] == "cancelled"


def test_each_env_is_a_fresh_copy() -> None:
    a, b = StoreEnv(CANCEL), StoreEnv(CANCEL)
    call_tool(tools_of(a), "cancel_order", {"order_id": "A101"})
    assert b.orders["A101"]["status"] == "pending"


# ---------- grading ----------

def test_success_requires_exact_final_state() -> None:
    env = StoreEnv(CANCEL)
    assert not env.success("done")
    call_tool(tools_of(env), "cancel_order", {"order_id": "A101"})
    assert env.success("done")
    call_tool(tools_of(env), "refund_order", {"order_id": "A102", "amount": 5})
    assert not env.success("done")  # extra, unrequested change


def test_success_requires_facts_in_reply() -> None:
    env = StoreEnv(next(s for s in SCENARIOS if s.id == "status"))
    assert env.success("Your order A100 has SHIPPED.")
    assert not env.success("It is on its way.")


# ---------- perturbations ----------

def test_environment_perturbation_renames_and_reformats() -> None:
    env = StoreEnv(CANCEL, perturbed=True)
    tools = tools_of(env)
    schema = tools["get_order"].parameters
    assert list(schema["properties"]) == ["orderId"]
    view = call_tool(tools, "get_order", {"orderId": "A100"})
    assert '"order_state": "shipped"' in view
    assert "01 Oct 2026" in view
    assert "ERROR" in call_tool(tools, "get_order", {"order_id": "A100"})


def test_tool_faults() -> None:
    env = StoreEnv(CANCEL, fault_probability=1.0)
    assert "503" in call_tool(tools_of(env), "get_order", {"order_id": "A100"})
    calm = StoreEnv(CANCEL, fault_probability=0.0)
    assert "ERROR" not in call_tool(tools_of(calm), "get_order", {"order_id": "A100"})


def test_fault_rate_roughly_p() -> None:
    env = StoreEnv(CANCEL, fault_probability=0.2, rng=random.Random(5))
    tools = tools_of(env)
    errors = sum("503" in call_tool(tools, "get_order", {"order_id": "A100"}) for _ in range(1000))
    assert abs(errors / 1000 - 0.2) < 0.04


def test_factory_gives_fresh_env_per_run() -> None:
    make = store_env_factory()
    task = store_tasks()[0]
    assert make(task) is not make(task)


# ---------- agent in the store ----------

class Script:
    def __init__(self, replies: list[ChatReply]) -> None:
        self.replies = list(replies)

    def __call__(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        return self.replies.pop(0)


def cancel_script(param: str = "order_id") -> Script:
    return Script([
        ChatReply(content="", tool_calls=[ToolCall(name="verify_customer",
                  arguments={"email": "ali@example.com", "zip": "54000"})]),
        ChatReply(content="", tool_calls=[ToolCall(name="cancel_order", arguments={param: "A101"})]),
        ChatReply(content="Your order A101 is cancelled."),
        ChatReply(content="95"),
    ])


def test_scripted_agent_completes_cancel() -> None:
    agent = make_tool_agent(cancel_script(), store_env_factory(), POLICY)
    out = agent(Task(id="cancel-pending", prompt=CANCEL.prompt, correct_answer=""))
    assert out.is_correct is True
    assert out.actions == ["verify_customer", "cancel_order"]


def test_same_script_breaks_under_environment_perturbation() -> None:
    agent = make_tool_agent(cancel_script(), store_env_factory(perturbed=True), POLICY)
    out = agent(Task(id="cancel-pending", prompt=CANCEL.prompt, correct_answer=""))
    assert out.is_correct is False


def test_scenarios_have_unique_ids_and_paraphrases() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert all(s.paraphrases for s in SCENARIOS)


def test_evaluate_store_with_do_nothing_model() -> None:
    def lazy(messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        return ChatReply(content="Sorry, I can't help with that.", tokens=10)

    cfg = RunConfig(name="lazy", model="ollama/x", environment="store", n_runs=2,
                    fault_probability=0.2)
    report, runs = evaluate_store(cfg, chat=lazy)
    assert set(runs) == {"baseline", "environment", "fault", "prompt"}
    # doing nothing is correct only where the right answer is to change nothing
    # and say nothing specific: cancel-shipped, other-customers-order, unverified-cancel
    assert report.accuracy == pytest.approx(3 / 8)
    assert report.robustness_env == pytest.approx(1.0)


def test_scenario_model_defaults() -> None:
    s = Scenario(id="x", prompt="p")
    assert s.expected_changes == {} and s.answer_contains == [] and s.paraphrases == []
