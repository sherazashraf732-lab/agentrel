"""A tool-calling agent loop, like the paper's tau-bench scaffold.

Each run gets a fresh Environment (its own copy of the data), so tool calls in
one run cannot leak into the next. Everything is recorded: every model reply,
every tool call and result (transcript), the action sequence (for trajectory
consistency) and model calls / tokens (for resource consistency).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from agentrel.core.runner import Agent, AgentOutput, Task
from agentrel.llm.agent import CONFIDENCE_PROMPT, parse_confidence
from agentrel.llm.ollama import ChatFn, Message, ToolSchema


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema of the arguments
    fn: Callable[..., Any]

    def schema(self) -> ToolSchema:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Environment(Protocol):
    """What the agent acts on. Grades success from its own final state."""

    def tools(self) -> list[Tool]: ...

    def success(self, final_answer: str) -> bool: ...


REPLY_MARKER = "Reply:"


def extract_reply(text: str) -> str:
    """Customer-facing part of a final message.

    Small local models often think out loud even with thinking disabled. The
    system prompt asks them to put the customer reply after 'Reply:'; we keep
    only what follows the last marker, so reasoning never reaches grading.
    """
    index = text.casefold().rfind(REPLY_MARKER.casefold())
    if index == -1:
        return text.strip()
    return text[index + len(REPLY_MARKER):].strip()


def call_tool(tools: dict[str, Tool], name: str, arguments: dict[str, Any]) -> str:
    """Run one tool. Errors are returned to the model as text, like a real API."""
    tool = tools.get(name)
    if tool is None:
        return f"ERROR: unknown tool '{name}'"
    try:
        result = tool.fn(**arguments)
    except Exception as exc:  # noqa: BLE001 - tool errors go back to the agent
        return f"ERROR: {type(exc).__name__}: {exc}"
    return result if isinstance(result, str) else json.dumps(result, default=str)


@dataclass
class Turn:
    final: str = ""
    actions: list[str] = field(default_factory=list)
    api_calls: int = 0
    tokens: int = 0
    transcript: list[Message] = field(default_factory=list)


def run_turn(
    chat: ChatFn, tools: dict[str, Tool], messages: list[Message], max_steps: int = 8
) -> Turn:
    """One agent turn: call the model, run its tools, repeat until it replies.

    `messages` is extended in place, so a caller can keep a multi-turn
    conversation going (the website chat does this).
    """
    schemas = [t.schema() for t in tools.values()]
    turn = Turn()
    for _ in range(max_steps):
        reply = chat(messages, schemas)
        turn.api_calls += 1
        turn.tokens += reply.tokens
        assistant: Message = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            assistant["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}}
                for c in reply.tool_calls
            ]
        messages.append(assistant)
        turn.transcript.append(assistant)
        if not reply.tool_calls:
            turn.final = extract_reply(reply.content)
            return turn
        for call in reply.tool_calls:
            turn.actions.append(call.name)
            result = call_tool(tools, call.name, call.arguments)
            tool_msg: Message = {"role": "tool", "tool_name": call.name, "content": result}
            messages.append(tool_msg)
            turn.transcript.append(tool_msg)
    turn.transcript.append({"role": "system", "content": f"stopped after {max_steps} steps"})
    return turn


def make_tool_agent(
    chat: ChatFn,
    make_env: Callable[[Task], Environment],
    system_prompt: str,
    max_steps: int = 8,
    ask_confidence: bool = True,
) -> Agent:
    """Build an agent that loops: model -> tool calls -> results -> model."""

    def agent(task: Task) -> AgentOutput:
        env = make_env(task)
        tools = {t.name: t for t in env.tools()}
        messages: list[Message] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task.prompt},
        ]
        transcript: list[Message] = [dict(m) for m in messages]
        turn = run_turn(chat, tools, messages, max_steps)
        transcript.extend(turn.transcript)
        final, actions = turn.final, turn.actions
        api_calls, tokens = turn.api_calls, turn.tokens

        confidence = None
        if ask_confidence:
            question = CONFIDENCE_PROMPT.format(question=task.prompt, answer=final)
            conf_reply = chat([{"role": "user", "content": question}], [])
            api_calls += 1
            tokens += conf_reply.tokens
            transcript.append({"role": "confidence", "content": conf_reply.content})
            confidence = parse_confidence(conf_reply.content)

        check = getattr(env, "violations", None)
        return AgentOutput(
            text=final,
            confidence=confidence,
            actions=actions,
            api_calls=api_calls,
            tokens=tokens,
            transcript=transcript,
            is_correct=env.success(final),
            violations=check() if callable(check) else None,
        )

    return agent
