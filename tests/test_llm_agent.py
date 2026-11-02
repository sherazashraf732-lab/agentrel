import pytest

from agentrel.core.runner import Task, run_agent
from agentrel.llm.agent import (
    Messages,
    make_llm_agent,
    parse_confidence,
    strip_thinking,
)

TASK = Task(id="t1", prompt="What is 2 + 2?", correct_answer="SECRET-4")


class FakeModel:
    """Records every message it receives and returns scripted replies."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.seen: list[Messages] = []

    def __call__(self, model: str, messages: Messages) -> str:
        self.seen.append(messages)
        return self.replies[(len(self.seen) - 1) % len(self.replies)]


def test_correct_answer_is_never_sent_to_model() -> None:
    fake = FakeModel(["4", "90"])
    make_llm_agent("fake", complete=fake)(TASK)
    sent = " ".join(m["content"] for msgs in fake.seen for m in msgs)
    assert "SECRET-4" not in sent
    assert "What is 2 + 2?" in sent


def test_answer_and_confidence_returned() -> None:
    out = make_llm_agent("fake", complete=FakeModel(["4", "85"]))(TASK)
    assert out.text == "4"
    assert out.confidence == pytest.approx(0.85)


def test_confidence_can_be_disabled() -> None:
    fake = FakeModel(["4"])
    out = make_llm_agent("fake", complete=fake, ask_confidence=False)(TASK)
    assert out.confidence is None
    assert len(fake.seen) == 1


def test_thinking_is_stripped_from_answer() -> None:
    fake = FakeModel(["<think>2+2 is 4</think>\n4", "<think>sure</think> 99"])
    out = make_llm_agent("fake", complete=fake)(TASK)
    assert out.text == "4"
    assert out.confidence == pytest.approx(0.99)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("80", 0.8),
        ("Confidence: 75%", 0.75),
        ("0.6", 0.6),
        ("150", 1.0),
        ("no idea", None),
        ("On a scale of 0 to 100, I'd say 90", 0.9),
        ("I'm 0% confident", 0.0),
        ("Step 1 done. I am 70% sure", 0.7),
        ("95 out of 100", 0.95),
    ],
)
def test_parse_confidence(reply: str, expected: float | None) -> None:
    result = parse_confidence(reply)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_strip_thinking_multiline() -> None:
    assert strip_thinking("<think>\na\nb\n</think>\n  Paris ") == "Paris"


def test_model_crash_is_recorded_by_runner() -> None:
    def broken(model: str, messages: Messages) -> str:
        raise ConnectionError("ollama not running")

    records = run_agent(make_llm_agent("fake", complete=broken), [TASK], n_runs=2)
    assert all(r.error and "ollama not running" in r.error for r in records)
