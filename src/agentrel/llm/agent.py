"""Turn any chat model (Ollama, Groq, OpenAI, ... via LiteLLM) into an agent.

Two calls per task, following the paper's post-hoc self-assessment protocol:
1. answer the question  2. rate its own confidence in that answer (0-100).

Anti-cheating rule: only `task.prompt` is ever sent to the model, never
`task.correct_answer`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agentrel.core.runner import Agent, AgentOutput, Task

Messages = list[dict[str, str]]
CompletionFn = Callable[[str, Messages], str]

ANSWER_SYSTEM = (
    "Answer the question. Reply with only the final answer: "
    "no explanation, no full sentence."
)
CONFIDENCE_PROMPT = (
    "Question: {question}\nProposed answer: {answer}\n\n"
    "How confident are you that the proposed answer is correct? "
    "Reply with a single number from 0 to 100."
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# scale descriptions like "0 to 100", "0-100", "out of 100" are not answers
_SCALE = re.compile(r"\b0\s*(?:-|to)\s*100\b|\bout of 100\b", re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks that reasoning models (e.g. qwen3) emit."""
    return _THINK.sub("", text).strip()


def parse_confidence(text: str) -> float | None:
    """Confidence as a 0-1 value: the last 'N%' wins, else the last number
    after removing scale phrases (models that think out loud state their
    verdict at the end). Values above 1 are read as percent."""
    cleaned = _SCALE.sub(" ", strip_thinking(text))
    percents = _PERCENT.findall(cleaned)
    if percents:
        value = float(percents[-1])
    else:
        numbers = _NUMBER.findall(cleaned)
        if not numbers:
            return None
        value = float(numbers[-1])
    if value > 1.0:
        value /= 100.0
    return min(max(value, 0.0), 1.0)


def litellm_completion(model: str, messages: Messages) -> str:
    """Real model call through LiteLLM, temperature 0 as in the paper."""
    import litellm

    response: Any = litellm.completion(model=model, messages=messages, temperature=0)
    return str(response.choices[0].message.content or "")


def make_llm_agent(
    model: str,
    complete: CompletionFn = litellm_completion,
    ask_confidence: bool = True,
) -> Agent:
    """Build an agent. `complete` is injectable so tests need no network."""

    def agent(task: Task) -> AgentOutput:
        answer = strip_thinking(
            complete(
                model,
                [
                    {"role": "system", "content": ANSWER_SYSTEM},
                    {"role": "user", "content": task.prompt},
                ],
            )
        )
        confidence = None
        if ask_confidence:
            reply = complete(
                model,
                [
                    {
                        "role": "user",
                        "content": CONFIDENCE_PROMPT.format(
                            question=task.prompt, answer=answer
                        ),
                    }
                ],
            )
            confidence = parse_confidence(reply)
        return AgentOutput(text=answer, confidence=confidence)

    return agent
