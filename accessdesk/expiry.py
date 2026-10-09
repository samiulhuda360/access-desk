"""The expiry job: revoke access grants whose time limit has passed.

Run it on a timer (cron, a Kubernetes CronJob, or ``accessdesk expire --watch``). It is safe to run repeatedly.
"""

from __future__ import annotations

import time

from .audit import AuditLog
from .grants import GrantStore


def run_once(grants: GrantStore, audit: AuditLog | None = None) -> int:
    revoked = grants.revoke_expired()
    for g in revoked:
        if audit is not None:
            audit.write("expiry_revoke", grant_id=g.id, requester=g.requester, system=g.system, level=g.level, expired_at=g.expires_at)
    return len(revoked)


def watch(grants: GrantStore, audit: AuditLog | None = None, interval_s: float = 60.0) -> None:  # pragma: no cover - loop
    while True:
        run_once(grants, audit)
        time.sleep(interval_s)
