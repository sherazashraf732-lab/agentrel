import pytest
from pydantic import ValidationError

from agentrel.llm.mock import MockLLM, MockResponse

PROMPT = "What is 2 + 2?"
CORRECT = "4"


@pytest.mark.parametrize("bad_accuracy", [-0.1, 1.1, 2.0])
def test_invalid_accuracy_raises(bad_accuracy: float) -> None:
    with pytest.raises(ValueError):
        MockLLM(accuracy=bad_accuracy, seed=0)


def test_default_accuracy_is_point_eight() -> None:
    assert MockLLM(seed=0).accuracy == 0.8


def test_accuracy_one_is_always_correct() -> None:
    llm = MockLLM(accuracy=1.0, seed=0)
    assert all(llm.answer(PROMPT, CORRECT).is_correct for _ in range(200))


def test_accuracy_zero_is_always_wrong() -> None:
    llm = MockLLM(accuracy=0.0, seed=0)
    assert not any(llm.answer(PROMPT, CORRECT).is_correct for _ in range(200))


@pytest.mark.parametrize("accuracy", [0.6, 0.8, 0.95])
def test_observed_accuracy_close_to_setting(accuracy: float) -> None:
    llm = MockLLM(accuracy=accuracy, seed=42)
    n = 2000
    correct = sum(llm.answer(PROMPT, CORRECT).is_correct for _ in range(n))
    assert abs(correct / n - accuracy) < 0.04


def test_same_seed_gives_same_answers() -> None:
    a = MockLLM(accuracy=0.8, seed=7)
    b = MockLLM(accuracy=0.8, seed=7)
    for _ in range(50):
        assert a.answer(PROMPT, CORRECT) == b.answer(PROMPT, CORRECT)


def test_different_seeds_give_different_answers() -> None:
    a = MockLLM(accuracy=0.8, seed=1)
    b = MockLLM(accuracy=0.8, seed=2)
    run_a = [a.answer(PROMPT, CORRECT) for _ in range(50)]
    run_b = [b.answer(PROMPT, CORRECT) for _ in range(50)]
    assert run_a != run_b


def test_is_correct_matches_text() -> None:
    llm = MockLLM(accuracy=0.5, seed=3)
    for _ in range(200):
        r = llm.answer(PROMPT, CORRECT)
        assert r.is_correct == (r.text == CORRECT)


def test_confidence_in_valid_range() -> None:
    llm = MockLLM(accuracy=0.5, seed=3)
    for _ in range(200):
        assert 0.0 <= llm.answer(PROMPT, CORRECT).confidence <= 1.0


def test_wrong_answers_are_sometimes_overconfident() -> None:
    llm = MockLLM(accuracy=0.5, seed=3)
    responses = [llm.answer(PROMPT, CORRECT) for _ in range(500)]
    wrong = [r for r in responses if not r.is_correct]
    assert any(r.confidence > 0.5 for r in wrong)


def test_response_is_immutable() -> None:
    r = MockLLM(seed=0).answer(PROMPT, CORRECT)
    assert isinstance(r, MockResponse)
    with pytest.raises(ValidationError):
        r.text = "changed"
