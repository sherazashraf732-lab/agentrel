"""Deterministic fake LLM for testing agentrel without network or cost."""

from __future__ import annotations

import random

from pydantic import BaseModel, ConfigDict, Field


class MockResponse(BaseModel):
    """One answer from MockLLM."""

    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    is_correct: bool


class MockLLM:
    """Fake model that answers correctly with a fixed probability.

    Same seed gives the same sequence of answers (reproducible).
    Confidence is deliberately miscalibrated so predictability
    metrics have something to detect.
    """

    def __init__(self, accuracy: float = 0.8, seed: int = 0) -> None:
        if not 0.0 <= accuracy <= 1.0:
            raise ValueError(f"accuracy must be between 0 and 1, got {accuracy}")
        self.accuracy = accuracy
        self.seed = seed
        self._rng = random.Random(seed)
        self._calls = 0

    def answer(self, prompt: str, correct_answer: str) -> MockResponse:
        """Return a correct answer with probability `accuracy`, else a wrong one.

        `prompt` is unused for now; it keeps the same interface as a real LLM.
        """
        self._calls += 1
        if self._rng.random() < self.accuracy:
            return MockResponse(
                text=correct_answer,
                confidence=self._rng.uniform(0.6, 1.0),
                is_correct=True,
            )
        return MockResponse(
            text=f"WRONG[{self._calls}]: not {correct_answer}",
            confidence=self._rng.uniform(0.3, 0.9),
            is_correct=False,
        )
