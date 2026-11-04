"""Fault injection and recovery helpers.

`inject_faults` wraps any callable (an LLM client, a tool) so it randomly
raises `InjectedFault`, simulating timeouts and error responses. Wrap the
*tools or model calls an agent uses*, not the agent itself: a robust agent
should survive faults in its dependencies (paper: p_fault = 0.2).

`with_retries` is one reliability-improving technique we can measure.
"""

from __future__ import annotations

import random
from collections.abc import Callable


class InjectedFault(RuntimeError):
    """A simulated infrastructure failure (timeout, 5xx, malformed response)."""


def inject_faults[**P, R](
    fn: Callable[P, R], p_fault: float, seed: int = 0
) -> Callable[P, R]:
    """Return a wrapper that raises InjectedFault with probability `p_fault`."""
    if not 0.0 <= p_fault <= 1.0:
        raise ValueError(f"p_fault must be between 0 and 1, got {p_fault}")
    rng = random.Random(seed)

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if rng.random() < p_fault:
            raise InjectedFault("injected fault: simulated timeout")
        return fn(*args, **kwargs)

    return wrapper


def with_retries[**P, R](fn: Callable[P, R], max_retries: int = 2) -> Callable[P, R]:
    """Return a wrapper that retries `fn` up to `max_retries` times on any error."""
    if max_retries < 0:
        raise ValueError(f"max_retries must be >= 0, got {max_retries}")

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        for attempt in range(max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception:  # retry on any dependency failure, re-raise at the end
                if attempt == max_retries:
                    raise
        raise AssertionError("unreachable")

    return wrapper
