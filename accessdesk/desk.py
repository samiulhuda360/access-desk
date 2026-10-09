"""The desk: enrich a request, assess it, apply the policy, grant or ask a person, log everything.

It never provisions access itself; grants go through the ``GrantStore`` adapter (simulated by default).
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from .audit import AuditLog
from .backends import Backend, RulesBackend, make_backend
from .directory import Directory
from .grants import GrantStore
from .metrics import Metrics
from .notify import ApprovalBroker, ApprovalRequest, DenyNotifier, Notifier, notifier_from_env
from .policy import Policy
from .redact import redact
from .types import AccessRequest, Assessment, Decision, RequestContext, Verdict

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="accessdesk")


class Desk:
    def __init__(
        self,
        backend: Backend | None,
        directory: Directory,
        policy: Policy | None = None,
        *,
        grants: GrantStore | None = None,
        notifier: Notifier | None = None,
        audit: AuditLog | None = None,
        metrics: Metrics | None = None,
        timeout_s: float = 3.0,
    ) -> None:
        """``backend=None`` means no model is configured: every request runs in fail-safe mode."""
        self.backend = backend
        self.directory = directory
        self.policy = policy or Policy()
        self.grants = grants or GrantStore("grants.jsonl")
        self.notifier: Notifier = notifier or DenyNotifier()
        self.audit = audit or AuditLog(None)
        self.metrics = metrics or Metrics()
        self.timeout_s = timeout_s
        self._fallback = RulesBackend()

    @classmethod
    def from_env(cls, *, broker: ApprovalBroker | None = None, metrics: Metrics | None = None) -> Desk:
        name = os.environ.get("ACCESSDESK_BACKEND") or ("jev" if os.environ.get("TYPESAFE_API_KEY") else "")
        timeout = float(os.environ.get("ACCESSDESK_TIMEOUT_S", "3"))
        backend = make_backend(name, timeout_s=timeout, cache_dir=os.environ.get("ACCESSDESK_CACHE_DIR")) if name else None
        directory = Directory.load(os.environ.get("ACCESSDESK_DIRECTORY") or _default("directory.yaml"))
        policy_path = os.environ.get("ACCESSDESK_POLICY")
        policy = Policy.load(policy_path) if policy_path else Policy()
        return cls(
            backend,
            directory,
            policy,
            grants=GrantStore(os.environ.get("ACCESSDESK_GRANTS") or "grants.jsonl"),
            notifier=notifier_from_env(broker),
            audit=AuditLog(os.environ.get("ACCESSDESK_AUDIT_LOG") or "access-audit.jsonl"),
            metrics=metrics,
            timeout_s=timeout,
        )

    # ------------------------------------------------------------------------------------------------------
    def evaluate(self, ctx: RequestContext) -> tuple[Decision, Assessment | None, bool, str]:
        """Return (decision, assessment, fell_back, backend_error) without asking anyone or granting anything."""
        pre = self.policy.pre_check(ctx)
        if pre is not None:
            return pre, None, False, ""
        error = ""
        if self.backend is not None:
            backend = self.backend
            try:
                assessment = _POOL.submit(backend.assess, ctx).result(timeout=self.timeout_s + 0.5)
                return self.policy.decide(assessment, ctx), assessment, False, ""
            except FutureTimeout:
                error = f"{backend.name} timed out after {self.timeout_s:.1f}s"
            except Exception as exc:  # noqa: BLE001 - any failure must fail closed, never open
                error = f"{backend.name} failed: {type(exc).__name__}: {exc}"
            self.metrics.backend_error(backend.name)
        else:
            error = "no model configured"
        # Fail-safe: take the rules assessment but never auto-approve; always route to a person.
        assessment = self._fallback.assess(ctx)
        decision = self.policy.decide(assessment, ctx)
        note = f"model unavailable ({error}); a person must decide"
        if decision.action == "auto_approve":
            decision = Decision("escalate", decision.grant_level, decision.expires_days, [note], recommendation="review manually", rule="fallback")
        else:
            decision = Decision(
                decision.action, decision.grant_level, decision.expires_days, [*decision.reasons, note], decision.recommendation, rule="fallback"
            )
        return decision, assessment, True, error

    def triage(self, request: AccessRequest, *, ask_person: bool = True) -> Verdict:
        start = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        ctx = self.directory.enrich(request)
        decision, assessment, fell_back, error = self.evaluate(ctx)
        decide_s = time.perf_counter() - start

        approved_by: str | None = None
        channel: str | None = None
        grant_id: str | None = None
        expires_at: str | None = None
        granted = False
        grant_level = decision.grant_level

        if decision.action == "auto_approve":
            grant = self.grants.create(request, decision.grant_level, decision.expires_days, approved_by="auto", source="auto")
            granted, grant_id, expires_at, outcome = True, grant.id, grant.expires_at, "auto_approved"
            approved_by = "auto"
        elif decision.action == "deny":
            outcome = "denied"
        elif ask_person:
            result = self.notifier.request(
                ApprovalRequest(request_id, ctx, decision.reasons, decision.recommendation, assessment), self.policy.approval_timeout_s
            )
            approved_by, channel = (result.by or None), result.channel
            self.audit.write(
                "approval", request_id=request_id, approved=result.approved, by=result.by or None, channel=result.channel, note=result.note or None
            )
            if result.approved:
                grant = self.grants.create(
                    request, decision.grant_level, decision.expires_days, approved_by=result.by or "reviewer", source=result.channel
                )
                granted, grant_id, expires_at, outcome = True, grant.id, grant.expires_at, "approved"
            else:
                outcome = "rejected"
        else:
            outcome = "escalated"

        verdict = Verdict(
            request_id=request_id,
            action=decision.action,
            outcome=outcome,
            granted=granted,
            grant_level=grant_level,
            expires_at=expires_at,
            reasons=decision.reasons,
            recommendation=decision.recommendation,
            rule=decision.rule,
            approved_by=approved_by,
            approval_channel=channel,
            fallback=fell_back,
            latency_ms=round(decide_s * 1000, 2),
            assessment=assessment,
            grant_id=grant_id,
        )
        self.metrics.record(outcome=outcome, action=decision.action, latency_s=decide_s, fallback=fell_back)
        ctx_logged = ctx.to_dict()
        ctx_logged["request"]["justification"] = redact(request.justification)
        self.audit.write(
            "decision",
            request_id=request_id,
            policy=self.policy.name,
            context=ctx_logged,
            backend=assessment.backend if assessment else None,
            answers=assessment.to_dict() if assessment else None,
            action=decision.action,
            rule=decision.rule,
            reasons=decision.reasons,
            recommendation=decision.recommendation,
            outcome=outcome,
            granted=granted,
            grant_level=grant_level,
            grant_id=grant_id,
            expires_at=expires_at,
            approved_by=approved_by,
            approval_channel=channel,
            fallback=fell_back,
            backend_error=error or None,
            latency_ms=verdict.latency_ms,
        )
        return verdict


def _default(name: str) -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / name)
