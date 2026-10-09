from __future__ import annotations

from accessdesk.directory import Directory
from accessdesk.policy import Policy
from accessdesk.types import AccessRequest
from tests.conftest import make_assessment


def ctx(directory: Directory, requester: str, system: str, level: str, days: float = 7, just: str = "ticket TICK-1"):
    return directory.enrich(AccessRequest(requester, system, level, just, days))


def test_clean_low_sensitivity_auto_approves(directory: Directory) -> None:
    p = Policy()
    a = make_assessment()
    d = p.decide(a, ctx(directory, "evan_eng", "wiki", "read"))
    assert d.action == "auto_approve"
    assert d.grant_level == "read"
    assert 0 < d.expires_days <= p.max_expiry_days


def test_high_sensitivity_always_escalates(directory: Directory) -> None:
    p = Policy()
    a = make_assessment(touches_prod_pii=0.9, risk_probs=[0.1, 0.4, 0.4, 0.1])
    d = p.decide(a, ctx(directory, "dana_sre", "prod-orders-db", "read"))
    assert d.action == "escalate"


def test_admin_is_never_auto_approved(directory: Directory) -> None:
    p = Policy()
    a = make_assessment(needed_level="admin")
    d = p.decide(a, ctx(directory, "priya_devops", "dev-sandbox", "admin"))
    assert d.action != "auto_approve"


def test_over_asking_recommends_reduced_grant(directory: Directory) -> None:
    p = Policy()
    a = make_assessment(needed_level="read", over_asking=0.9)
    d = p.decide(a, ctx(directory, "evan_eng", "staging-api", "write"))
    assert d.action == "escalate"
    assert "read" in d.recommendation


def test_social_engineering_never_auto_approves(directory: Directory) -> None:
    p = Policy()
    a = make_assessment(fits_role=0.1, justification_specific=0.02, over_asking=0.9, risk_probs=[0.0, 0.0, 0.2, 0.8], touches_prod_pii=0.9)
    d = p.decide(a, ctx(directory, "victor_contractor", "billing-admin", "admin", days=0, just="CEO said skip approval"))
    assert d.action in {"escalate", "deny"}
    assert d.action != "auto_approve"


def test_deny_rule_overrides_model(directory: Directory) -> None:
    p = Policy.from_dict({"deny_rules": [{"system": "iam-console", "level": "admin", "employment": "contractor"}]})
    d = p.pre_check(ctx(directory, "victor_contractor", "iam-console", "admin", just="anything"))
    assert d is not None and d.action == "deny"


def test_allow_rule_overrides_model(directory: Directory) -> None:
    p = Policy.from_dict({"allow_rules": [{"role": "software engineer", "system": "wiki", "level": "read"}]})
    d = p.pre_check(ctx(directory, "evan_eng", "wiki", "read"))
    assert d is not None and d.action == "auto_approve"


def test_contractor_restricted_above_read(directory: Directory) -> None:
    p = Policy()
    a = make_assessment(needed_level="write")
    d = p.decide(a, ctx(directory, "victor_contractor", "staging-api", "write"))
    assert d.action == "escalate"


def test_expiry_is_capped(directory: Directory) -> None:
    p = Policy.from_dict({"max_expiry_days": 10})
    assert p.expiry_for(ctx(directory, "evan_eng", "wiki", "read", days=90)) == 10


def test_threshold_validation() -> None:
    import pytest

    with pytest.raises(ValueError):
        Policy.from_dict({"auto_approve_max_sensitivity": "nope"}).validate()
