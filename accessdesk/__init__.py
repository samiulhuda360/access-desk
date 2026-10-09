"""access-desk: triage employee access requests. Auto-approve the safe ones, escalate the rest, never over-grant."""

from .desk import Desk
from .directory import Directory
from .grants import Grant, GrantStore
from .policy import Policy, Thresholds
from .types import AccessRequest, Assessment, Decision, RequestContext, Verdict

__all__ = [
    "AccessRequest",
    "Assessment",
    "Decision",
    "Desk",
    "Directory",
    "Grant",
    "GrantStore",
    "Policy",
    "RequestContext",
    "Thresholds",
    "Verdict",
]
__version__ = "1.0.0"
