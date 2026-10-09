"""The desk must never fail open: when the model is unreachable it falls back to rules and asks a person."""

from __future__ import annotations

from pathlib import Path

from accessdesk.audit import AuditLog
from accessdesk.backends import BackendError
from accessdesk.desk import Desk
from accessdesk.directory import Directory
from accessdesk.grants import GrantStore
from accessdesk.notify import ApprovalResult
from accessdesk.policy import Policy
from accessdesk.types import AccessRequest
from tests.conftest import FakeBackend, make_assessment


class YesNotifier:
    channel = "test"

    def request(self, req, timeout_s):  # noqa: ANN001
        return ApprovalResult(True, by="tester", channel="test")


def _desk(backend, directory, tmp_path: Path, notifier=None) -> Desk:
    return Desk(
        backend,
        directory,
        Policy(),
        grants=GrantStore(tmp_path / "g.jsonl"),
        notifier=notifier or YesNotifier(),
        audit=AuditLog(tmp_path / "a.jsonl"),
        timeout_s=0.3,
    )


def test_backend_error_falls_back_and_asks(directory: Directory, tmp_path: Path) -> None:
    desk = _desk(FakeBackend(raises=BackendError("boom")), directory, tmp_path)
    v = desk.triage(AccessRequest("evan_eng", "wiki", "read", "ticket TICK-1", 7))
    assert v.fallback is True
    assert v.rule == "fallback"
    # even a would-be auto-approve is forced to a person
    assert v.action == "escalate"


def test_timeout_fails_closed(directory: Directory, tmp_path: Path) -> None:
    slow = FakeBackend(make_assessment(), delay=1.0)  # longer than timeout_s
    desk = _desk(slow, directory, tmp_path)
    v = desk.triage(AccessRequest("evan_eng", "wiki", "read", "ticket TICK-1", 7), ask_person=False)
    assert v.fallback is True
    assert v.granted is False  # no person said yes, so nothing is granted


def test_no_notifier_denies_on_escalation(directory: Directory, tmp_path: Path) -> None:
    # DenyNotifier default: an escalation with no approval channel results in no grant.
    desk = Desk(
        FakeBackend(make_assessment(touches_prod_pii=0.9, risk_probs=[0.1, 0.1, 0.4, 0.4])),
        directory,
        Policy(),
        grants=GrantStore(tmp_path / "g.jsonl"),
        audit=AuditLog(tmp_path / "a.jsonl"),
    )
    v = desk.triage(AccessRequest("dana_sre", "prod-orders-db", "read", "INC-1", 1))
    assert v.granted is False
    assert v.outcome == "rejected"


def test_approved_escalation_creates_grant(directory: Directory, tmp_path: Path) -> None:
    desk = _desk(FakeBackend(make_assessment(touches_prod_pii=0.9, risk_probs=[0.1, 0.3, 0.4, 0.2])), directory, tmp_path)
    v = desk.triage(AccessRequest("dana_sre", "prod-orders-db", "read", "INC-1", 1))
    assert v.granted is True
    assert v.approved_by == "tester"
    assert v.grant_id is not None
