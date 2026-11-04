from typing import Any

import pytest

from agentrel.agents.tool_agent import Tool, call_tool, make_tool_agent
from agentrel.core.runner import Task, run_agent
from agentrel.llm.ollama import (
    ChatReply,
    Message,
    ToolCall,
    ToolSchema,
    parse_ollama_response,
)
from agentrel.report import build_report

TASK = Task(
    id="t1", prompt="What is the status of order A100?", correct_answer="shipped"
)


class OrderEnv:
    def __init__(self) -> None:
        self.orders = {"A100": "shipped"}

    def get_order(self, order_id: str) -> dict[str, str]:
        return {"order_id": order_id, "status": self.orders[order_id]}

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_order",
                description="Look up an order",
                parameters={
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
                fn=self.get_order,
            )
        ]

    def success(self, final_answer: str) -> bool:
        return "shipped" in final_answer.lower()


class ScriptedChat:
    """Plays back replies; records what the model was sent."""

    def __init__(self, replies: list[ChatReply]) -> None:
        self.replies = list(replies)
        self.seen: list[list[Message]] = []

    def __call__(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def script(final: str = "Order A100 has shipped.", conf: str = "90") -> ScriptedChat:
    return ScriptedChat(
        [
            ChatReply(
                content="",
                tool_calls=[ToolCall(name="get_order", arguments={"order_id": "A100"})],
                tokens=50,
            ),
            ChatReply(content=final, tokens=30),
            ChatReply(content=conf, tokens=5),
        ]
    )


def agent_with(chat: ScriptedChat) -> Any:
    return make_tool_agent(
        chat, lambda task: OrderEnv(), system_prompt="You are a store agent."
    )


def test_agent_calls_tool_and_answers() -> None:
    out = agent_with(script())(TASK)
    assert out.text == "Order A100 has shipped."
    assert out.actions == ["get_order"]
    assert out.is_correct is True
    assert out.confidence == pytest.approx(0.9)
    assert out.api_calls == 3
    assert out.tokens == 85


def test_tool_result_is_sent_back_to_model() -> None:
    chat = script()
    agent_with(chat)(TASK)
    second_call = chat.seen[1]
    assert second_call[-1]["role"] == "tool"
    assert '"status": "shipped"' in second_call[-1]["content"]


def test_correct_answer_never_sent() -> None:
    chat = script()
    agent_with(chat)(Task(id="t", prompt=TASK.prompt, correct_answer="SECRET"))
    sent = str(chat.seen)
    assert "SECRET" not in sent


def test_transcript_has_every_message() -> None:
    out = agent_with(script())(TASK)
    roles = [m["role"] for m in out.transcript]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "confidence"]


def test_environment_grades_success_not_text_match() -> None:
    out = agent_with(script(final="It is on its way, status: SHIPPED"))(TASK)
    assert out.is_correct is True


def test_wrong_final_answer_fails() -> None:
    out = agent_with(script(final="It was cancelled."))(TASK)
    assert out.is_correct is False


def test_stops_after_max_steps() -> None:
    looping = ScriptedChat(
        [
            ChatReply(
                content="",
                tool_calls=[ToolCall(name="get_order", arguments={"order_id": "A100"})],
            )
        ]
        * 3
        + [ChatReply(content="50")]
    )
    agent = make_tool_agent(looping, lambda t: OrderEnv(), "sys", max_steps=3)
    out = agent(TASK)
    assert out.actions == ["get_order"] * 3
    assert out.text == ""
    assert out.is_correct is False


def test_call_tool_errors_become_text() -> None:
    tools = {t.name: t for t in OrderEnv().tools()}
    assert call_tool(tools, "nope", {}).startswith("ERROR: unknown tool")
    assert "KeyError" in call_tool(tools, "get_order", {"order_id": "ZZZ"})
    assert "TypeError" in call_tool(tools, "get_order", {"wrong": 1})


def test_records_and_report_include_trajectory() -> None:
    def fresh_agent(task: Task) -> Any:
        return agent_with(script())(task)

    records = run_agent(fresh_agent, [TASK], n_runs=3)
    assert all(r.actions == ["get_order"] and r.transcript for r in records)
    report = build_report("tool-agent", records)
    assert report.consistency_trajectory_sequence == pytest.approx(1.0)
    assert not any("trajectory" in n for n in report.notes)


def test_parse_ollama_response() -> None:
    data = {
        "message": {
            "content": "<think>hmm</think>",
            "tool_calls": [
                {"function": {"name": "get_order", "arguments": {"order_id": "A1"}}}
            ],
        },
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    reply = parse_ollama_response(data)
    assert reply.content == ""
    assert reply.tool_calls == [
        ToolCall(name="get_order", arguments={"order_id": "A1"})
    ]
    assert reply.tokens == 15


def test_extract_reply_drops_reasoning() -> None:
    from agentrel.agents.tool_agent import extract_reply

    text = "Okay, the order was shipped? No, cancelled. Let me answer.\nReply: Your order A101 is cancelled."
    assert extract_reply(text) == "Your order A101 is cancelled."
    assert extract_reply("No marker here.") == "No marker here."
    assert extract_reply("reply: lowercase works") == "lowercase works"


def test_reasoning_cannot_fake_success() -> None:
    # the word "shipped" only appears in the reasoning, not in the reply
    out = agent_with(
        script(final="I think it shipped... not sure.\nReply: I can't find that order.")
    )(TASK)
    assert out.text == "I can't find that order."
    assert out.is_correct is False
