"""Rules baseline: a role-to-system access matrix, the kind of static policy many IT teams start with.

No model and no network. It is also the fail-safe fallback when Jev is unreachable: the gate then takes its
assessment but routes everything to a person (the fallback can never auto-approve on its own).
"""

from __future__ import annotations

import time

from ..types import LEVEL_RANK, LEVELS, SENSITIVITY, Assessment, Level, RequestContext
from .base import one_hot_levels

# How much standing access each broad role family is assumed to warrant per sensitivity tier.
# role keyword -> {sensitivity: max level that fits the role}
ROLE_MATRIX: dict[str, dict[str, str]] = {
    "admin": {"low": "admin", "medium": "admin", "high": "admin", "critical": "write"},
    "sre": {"low": "admin", "medium": "admin", "high": "write", "critical": "write"},
    "devops": {"low": "admin", "medium": "write", "high": "write", "critical": "read"},
    "engineer": {"low": "write", "medium": "write", "high": "read", "critical": "none"},
    "developer": {"low": "write", "medium": "write", "high": "read", "critical": "none"},
    "data": {"low": "write", "medium": "read", "high": "read", "critical": "none"},
    "analyst": {"low": "read", "medium": "read", "high": "read", "critical": "none"},
    "support": {"low": "read", "medium": "read", "high": "read", "critical": "none"},
    "sales": {"low": "read", "medium": "read", "high": "none", "critical": "none"},
    "marketing": {"low": "read", "medium": "none", "high": "none", "critical": "none"},
    "manager": {"low": "read", "medium": "read", "high": "read", "critical": "none"},
    "finance": {"low": "read", "medium": "read", "high": "read", "critical": "none"},
}
DEFAULT_ROW = {"low": "read", "medium": "read", "high": "none", "critical": "none"}


def _row(role: str) -> dict[str, str]:
    role = role.lower()
    for key, row in ROLE_MATRIX.items():
        if key in role:
            return row
    return DEFAULT_ROW


class RulesBackend:
    name = "rules"

    def assess(self, ctx: RequestContext) -> Assessment:
        start = time.perf_counter()
        r = ctx.request
        sens = ctx.system_sensitivity if ctx.system_sensitivity in SENSITIVITY else "high"
        allowed = _row(ctx.requester_role).get(sens, "none")
        allowed_rank = LEVEL_RANK[allowed]
        requested_rank = LEVEL_RANK.get(r.level, 1)

        fits = requested_rank <= allowed_rank
        # contractors and interns get a notch less than their role row
        if ctx.employment in {"contractor", "intern"}:
            allowed_rank = max(0, allowed_rank - 1)
            fits = requested_rank <= allowed_rank

        # A static matrix cannot read the justification or infer least privilege. It echoes the requested level,
        # assumes the stated reason is fine, and judges only by role, sensitivity, level and whether a duration
        # was typed. That blindness is exactly what the evaluation exposes.
        needed: Level = r.level
        over = requested_rank > allowed_rank
        prod_pii = sens in {"high", "critical"}
        time_ok = r.duration_days > 0

        risk = SENSITIVITY.index(sens)  # 0..3
        if not fits:
            risk = min(3, risk + 1)
        if r.level == "admin":
            risk = min(3, max(risk, 2))
        risk_probs = [0.0, 0.0, 0.0, 0.0]
        risk_probs[min(3, risk)] = 1.0

        return Assessment(
            needed_level=needed,
            level_probs=one_hot_levels(needed if needed in LEVELS else "read"),
            justification_specific=1.0,  # blind to text: assumes the reason is adequate
            fits_role=1.0 if fits else 0.0,
            over_asking=1.0 if over else 0.0,
            touches_prod_pii=1.0 if prod_pii else 0.0,
            time_limited=1.0 if time_ok else 0.0,
            risk_probs=risk_probs,
            backend="rules",
            confidence=1.0,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
