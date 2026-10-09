"""Plain data types shared across access-desk."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Level = Literal["none", "read", "write", "admin"]
LEVELS: tuple[Level, ...] = ("none", "read", "write", "admin")
LEVEL_RANK: dict[str, int] = {name: i for i, name in enumerate(LEVELS)}

Action = Literal["auto_approve", "escalate", "deny"]

# Sensitivity of a system, lowest to highest.
SENSITIVITY: tuple[str, ...] = ("low", "medium", "high", "critical")


@dataclass(frozen=True)
class AccessRequest:
    """What an employee asks for."""

    requester: str  # person id in the directory
    system: str  # system id in the directory
    level: Level  # access level requested
    justification: str = ""
    duration_days: float = 0.0  # 0 means "no time limit stated"
    ticket: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RequestContext:
    """The request plus facts the service looked up in the company directory."""

    request: AccessRequest
    requester_role: str = ""
    requester_team: str = ""
    employment: str = "employee"
    manager: str = ""
    system_owner: str = ""
    system_sensitivity: str = "medium"
    current_level: Level = "none"
    system_description: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["request"] = self.request.to_dict()
        return d


@dataclass
class Assessment:
    """Typed answers about one access request, from any backend. Probabilities are in [0, 1]."""

    needed_level: Level
    level_probs: dict[str, float]
    justification_specific: float
    fits_role: float
    over_asking: float
    touches_prod_pii: float
    time_limited: float
    risk_probs: list[float]
    backend: str
    confidence: float = 0.0  # confidence of the needed-level choice
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False

    @property
    def risk_score(self) -> float:
        return sum(level * p for level, p in enumerate(self.risk_probs))

    @property
    def critical(self) -> float:
        return self.risk_probs[3] if len(self.risk_probs) > 3 else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["risk_score"] = round(self.risk_score, 4)
        return d


@dataclass
class Decision:
    """What the policy decides, before any person is asked."""

    action: Action
    grant_level: Level  # the level to grant if approved (may be below requested)
    expires_days: float  # time-box for an auto-approved or recommended grant
    reasons: list[str] = field(default_factory=list)
    recommendation: str = ""  # for escalations: what the reviewer is advised to do
    rule: str = "model"


@dataclass
class Verdict:
    """The final outcome of one triage."""

    request_id: str
    action: Action
    outcome: str  # auto_approved | approved | rejected | escalated | denied
    granted: bool
    grant_level: Level
    expires_at: str | None
    reasons: list[str]
    recommendation: str
    rule: str
    approved_by: str | None = None
    approval_channel: str | None = None
    fallback: bool = False
    latency_ms: float = 0.0
    assessment: Assessment | None = None
    grant_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["assessment"] = self.assessment.to_dict() if self.assessment else None
        return d


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")
