"""The decision-backend interface."""

from __future__ import annotations

from typing import Protocol

from ..types import LEVELS, Assessment, RequestContext


class Backend(Protocol):
    name: str

    def assess(self, ctx: RequestContext) -> Assessment:
        """Answer the questions about one access request. Never grants anything."""
        ...


class BackendError(RuntimeError):
    """The backend could not answer (network error, timeout, bad response)."""


def clamp(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, v))


def normalise(probs: list[float]) -> list[float]:
    total = sum(probs)
    if total <= 0:
        return [1.0 / len(probs)] * len(probs)
    return [p / total for p in probs]


def level_confidence(level_probs: dict[str, float]) -> float:
    """Confidence of a choice = top probability minus the runner-up (0..1)."""
    ordered = sorted(level_probs.values(), reverse=True)
    top = ordered[0] if ordered else 0.0
    second = ordered[1] if len(ordered) > 1 else 0.0
    return max(0.0, top - second)


def one_hot_levels(level: str) -> dict[str, float]:
    return {level_name: (1.0 if level_name == level else 0.0) for level_name in LEVELS}
