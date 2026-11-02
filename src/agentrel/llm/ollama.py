"""Minimal client for Ollama's native /api/chat with tool calling.

We call Ollama directly (not through LiteLLM) because LiteLLM's tool path
currently pulls in an unrelated optional dependency, and the native API lets
us switch off qwen3's slow "thinking" mode (`think: false`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agentrel.llm.agent import strip_thinking

Message = dict[str, Any]
ToolSchema = dict[str, Any]


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatReply(BaseModel):
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tokens: int = 0


ChatFn = Callable[[list[Message], list[ToolSchema]], ChatReply]


def parse_ollama_response(data: dict[str, Any]) -> ChatReply:
    message = data.get("message", {})
    calls = [
        ToolCall(
            name=c["function"]["name"],
            arguments=c["function"].get("arguments") or {},
        )
        for c in message.get("tool_calls") or []
    ]
    tokens = int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0)
    return ChatReply(
        content=strip_thinking(message.get("content") or ""),
        tool_calls=calls,
        tokens=tokens,
    )


def ollama_chat(
    model: str,
    host: str = "http://localhost:11434",
    think: bool = False,
    timeout_s: float = 600.0,
) -> ChatFn:
    """Return a ChatFn bound to one local Ollama model (temperature 0)."""

    def chat(messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": think,
            "options": {"temperature": 0},
        }
        if tools:
            body["tools"] = tools
        response = httpx.post(f"{host}/api/chat", json=body, timeout=timeout_s)
        response.raise_for_status()
        return parse_ollama_response(response.json())

    return chat
