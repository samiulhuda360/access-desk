"""Policy: turn the model's answers into auto-approve, escalate or deny. All decisions are made here, in code.

The model never decides; it only supplies probabilities. The policy owns every threshold, the sensitivity caps,
and the time-boxing. A request is auto-approved only when every condition is met; anything else goes to a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .types import LEVEL_RANK, LEVELS, SENSITIVITY, Assessment, Decision, Level, RequestContext


@dataclass(frozen=True)
class Thresholds:
    auto_approve_risk_below: float = 1.2  # risk_score (0..3) must be under this
    max_critical: float = 0.15  # probability of top risk level
    min_fits_role: float = 0.75
    min_justification: float = 0.6
    max_over_asking: float = 0.4
    min_time_limited: float = 0.5
    min_confidence: float = 0.4


@dataclass
class Policy:
    name: str = "default"
    thresholds: Thresholds = field(default_factory=Thresholds)
    # Systems at or above this sensitivity always go to a person, never auto-approved.
    auto_approve_max_sensitivity: str = "medium"
    # Access at or above this level always goes to a person (admin is never auto-granted by default).
    auto_approve_max_level: Level = "write"
    default_expiry_days: float = 7.0
    max_expiry_days: float = 30.0
    # Contractors/interns never auto-approved above this level.
    restricted_employment_max_level: Level = "read"
    restricted_employment: tuple[str, ...] = ("contractor", "intern")
    # Explicit overrides of the model. Each entry is {role?, team?, system?, sensitivity?, employment?, level?}.
    deny_rules: list[dict[str, str]] = field(default_factory=list)
    allow_rules: list[dict[str, str]] = field(default_factory=list)
    approval_timeout_s: float = 300.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        t = data.get("thresholds") or {}
        base = Thresholds()
        thresholds = Thresholds(
            auto_approve_risk_below=float(t.get("auto_approve_risk_below", base.auto_approve_risk_below)),
            max_critical=float(t.get("max_critical", base.max_critical)),
            min_fits_role=float(t.get("min_fits_role", base.min_fits_role)),
            min_justification=float(t.get("min_justification", base.min_justification)),
            max_over_asking=float(t.get("max_over_asking", base.max_over_asking)),
            min_time_limited=float(t.get("min_time_limited", base.min_time_limited)),
            min_confidence=float(t.get("min_confidence", base.min_confidence)),
        )
        policy = cls(
            name=str(data.get("name", "default")),
            thresholds=thresholds,
            auto_approve_max_sensitivity=str(data.get("auto_approve_max_sensitivity", "medium")),
            auto_approve_max_level=_as_level(data.get("auto_approve_max_level", "write")),
            default_expiry_days=float(data.get("default_expiry_days", 7)),
            max_expiry_days=float(data.get("max_expiry_days", 30)),
            restricted_employment_max_level=_as_level(data.get("restricted_employment_max_level", "read")),
            restricted_employment=tuple(data.get("restricted_employment", ("contractor", "intern"))),
            deny_rules=list(data.get("deny_rules") or []),
            allow_rules=list(data.get("allow_rules") or []),
            approval_timeout_s=float((data.get("approval") or {}).get("timeout_s", 300)),
        )
        policy.validate()
        return policy

    @classmethod
    def load(cls, path: str | Path) -> Policy:
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    def validate(self) -> None:
        if self.auto_approve_max_sensitivity not in SENSITIVITY:
            raise ValueError(f"auto_approve_max_sensitivity must be one of {SENSITIVITY}")
        if not self.max_expiry_days >= 0 and self.default_expiry_days <= self.max_expiry_days:
            raise ValueError("need 0 <= default_expiry_days <= max_expiry_days")

    def with_thresholds(self, **kw: float) -> Policy:
        p = replace(self)
        p.thresholds = replace(self.thresholds, **kw)
        return p

    # ------------------------------------------------------------------------------------------------------
    def _match(self, rule: dict[str, str], ctx: RequestContext) -> bool:
        r = ctx.request
        fields = {
            "role": ctx.requester_role,
            "team": ctx.requester_team,
            "employment": ctx.employment,
            "system": r.system,
            "sensitivity": ctx.system_sensitivity,
            "level": r.level,
            "requester": r.requester,
        }
        return all(str(fields.get(k, "")).lower() == str(v).lower() for k, v in rule.items())

    def pre_check(self, ctx: RequestContext) -> Decision | None:
        for rule in self.deny_rules:
            if self._match(rule, ctx):
                return Decision("deny", "none", 0.0, [f"denied by policy rule {rule}"], recommendation="deny", rule="deny_rule")
        for rule in self.allow_rules:
            if self._match(rule, ctx):
                exp = min(ctx.request.duration_days or self.default_expiry_days, self.max_expiry_days)
                return Decision("auto_approve", ctx.request.level, exp, [f"allowed by policy rule {rule}"], rule="allow_rule")
        return None

    def expiry_for(self, ctx: RequestContext) -> float:
        requested = ctx.request.duration_days
        base = requested if requested and requested > 0 else self.default_expiry_days
        return min(base, self.max_expiry_days)

    def decide(self, a: Assessment, ctx: RequestContext) -> Decision:
        t = self.thresholds
        r = ctx.request
        requested_rank = LEVEL_RANK.get(r.level, 1)
        needed_rank = LEVEL_RANK.get(a.needed_level, requested_rank)
        sens_rank = SENSITIVITY.index(ctx.system_sensitivity) if ctx.system_sensitivity in SENSITIVITY else 2
        cap_rank = SENSITIVITY.index(self.auto_approve_max_sensitivity)
        expiry = self.expiry_for(ctx)

        reasons: list[str] = []

        # 1. Hard gates: these always go to a person, whatever the probabilities say.
        high_risk = a.critical >= t.max_critical or a.risk_score >= 2.0
        if high_risk:
            reasons.append(f"high risk (risk {a.risk_score:.2f}, critical p={a.critical:.2f})")
        if sens_rank > cap_rank:
            reasons.append(f"{ctx.system_sensitivity}-sensitivity system is above the auto-approve limit")
        if requested_rank > LEVEL_RANK[self.auto_approve_max_level]:
            reasons.append(f"{r.level} access is never auto-approved")
        if ctx.employment in self.restricted_employment and requested_rank > LEVEL_RANK[self.restricted_employment_max_level]:
            reasons.append(f"{ctx.employment}s are not auto-approved above {self.restricted_employment_max_level}")
        if a.touches_prod_pii >= 0.6:
            reasons.append(f"touches production or customer data (p={a.touches_prod_pii:.2f})")

        # 2. Model-confidence gates for auto-approval.
        if a.fits_role < t.min_fits_role:
            reasons.append(f"may not fit the requester's role (p={a.fits_role:.2f})")
        if a.justification_specific < t.min_justification:
            reasons.append(f"justification is not specific enough (p={a.justification_specific:.2f})")
        if a.over_asking > t.max_over_asking:
            reasons.append(f"asks for more than the task needs (p={a.over_asking:.2f})")
        if a.risk_score >= t.auto_approve_risk_below:
            reasons.append(f"risk above the auto-approve line (risk {a.risk_score:.2f})")
        if a.confidence < t.min_confidence:
            reasons.append(f"the model is not confident about the level needed (confidence {a.confidence:.2f})")
        if requested_rank > needed_rank:
            reasons.append(f"task needs only {a.needed_level}, request asks for {r.level}")

        if not reasons:
            return Decision("auto_approve", r.level, expiry, ["fits the role, specific justification, low risk, time-boxed"], rule="model")

        # Everything else escalates with a recommendation for the reviewer.
        recommendation, rec_level = self._recommend(a, ctx, requested_rank, needed_rank)
        return Decision("escalate", rec_level, expiry, reasons, recommendation=recommendation, rule="model")

    def _recommend(self, a: Assessment, ctx: RequestContext, requested_rank: int, needed_rank: int) -> tuple[str, Level]:
        r = ctx.request
        # Clearly unjustified or off-role: recommend denial.
        if a.fits_role < 0.3 and a.justification_specific < 0.4:
            return "deny: does not fit the role and the justification is weak", "none"
        if a.critical >= 0.5:
            return "deny: looks like misuse or a request that should not be granted", "none"
        # Over-asking but otherwise reasonable: recommend a reduced grant.
        if requested_rank > needed_rank and a.fits_role >= 0.5:
            reduced = LEVELS[min(needed_rank, LEVEL_RANK[self.auto_approve_max_level])]
            return f"grant {reduced} instead of {r.level} (least privilege)", reduced
        return f"review and, if correct, grant {r.level} time-boxed", r.level


def _as_level(value: object) -> Level:
    s = str(value)
    return s if s in LEVELS else "write"
