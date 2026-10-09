from __future__ import annotations

from pathlib import Path

import pytest

from accessdesk.directory import Directory
from accessdesk.types import Assessment, Level, RequestContext

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture
def directory() -> Directory:
    return Directory.load(DATA / "directory.yaml")


class FakeBackend:
    """A backend that returns a scripted assessment, or raises, so tests never touch the network."""

    name = "fake"

    def __init__(self, assessment: Assessment | None = None, *, raises: Exception | None = None, delay: float = 0.0) -> None:
        self._assessment = assessment
        self._raises = raises
        self._delay = delay
        self.calls = 0

    def assess(self, ctx: RequestContext) -> Assessment:
        import time

        self.calls += 1
        if self._delay:
            time.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        assert self._assessment is not None
        return self._assessment


def make_assessment(
    *,
    needed_level: Level = "read",
    justification_specific: float = 0.95,
    fits_role: float = 0.95,
    over_asking: float = 0.05,
    touches_prod_pii: float = 0.05,
    time_limited: float = 0.95,
    risk_probs: list[float] | None = None,
    backend: str = "fake",
    confidence: float = 0.9,
) -> Assessment:
    probs = risk_probs or [0.9, 0.1, 0.0, 0.0]
    return Assessment(
        needed_level=needed_level,
        level_probs={
            "none": 0.0,
            "read": 1.0 if needed_level == "read" else 0.0,
            "write": 1.0 if needed_level == "write" else 0.0,
            "admin": 1.0 if needed_level == "admin" else 0.0,
        },
        justification_specific=justification_specific,
        fits_role=fits_role,
        over_asking=over_asking,
        touches_prod_pii=touches_prod_pii,
        time_limited=time_limited,
        risk_probs=probs,
        backend=backend,
        confidence=confidence,
    )
