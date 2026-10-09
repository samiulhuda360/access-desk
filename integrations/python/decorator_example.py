"""A Python decorator that puts access-desk in front of any function that provisions access.

Wrap the function in your own IAM/ticketing code that actually grants access. The decorator triages the request
first; the wrapped function only runs when the desk auto-approves (or a reviewer approves). This is the generic
integration for any internal tool, not tied to any agent framework.

    from accessdesk import AccessRequest, Desk, Directory
    from decorator_example import gated_grant

    desk = Desk(backend, Directory.load("directory.yaml"))

    @gated_grant(desk)
    def provision(request: AccessRequest):
        # your real grant code; runs only if the desk approved
        okta_assign(request.requester, request.system, request.level)
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TypeVar

from accessdesk import AccessRequest, Desk
from accessdesk.types import Verdict

R = TypeVar("R")


class AccessDenied(PermissionError):
    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        super().__init__(f"access not granted ({verdict.outcome}): " + "; ".join(verdict.reasons))


def gated_grant(desk: Desk, *, ask_person: bool = True) -> Callable[[Callable[[AccessRequest], R]], Callable[[AccessRequest], Verdict | R]]:
    def wrap(fn: Callable[[AccessRequest], R]) -> Callable[[AccessRequest], Verdict | R]:
        @functools.wraps(fn)
        def inner(request: AccessRequest) -> Verdict | R:
            verdict = desk.triage(request, ask_person=ask_person)
            if not verdict.granted:
                raise AccessDenied(verdict)
            fn(request)  # the wrapped provisioning runs only after approval
            return verdict

        return inner

    return wrap


if __name__ == "__main__":
    import os

    from accessdesk import Directory
    from accessdesk.backends import make_backend

    data = os.path.join(os.path.dirname(__file__), "..", "..", "data", "directory.yaml")
    desk = Desk(make_backend("rules"), Directory.load(data))

    @gated_grant(desk, ask_person=False)
    def provision(request: AccessRequest) -> None:
        print(f"  [provision] would grant {request.level} on {request.system} to {request.requester}")

    try:
        provision(AccessRequest("evan_eng", "wiki", "read", "read the runbook, TICK-1", 7))
    except AccessDenied as exc:
        print("denied:", exc)
    try:
        provision(AccessRequest("sam_sales", "payments-gateway", "admin", "need it", 0))
    except AccessDenied as exc:
        print("denied:", exc)
