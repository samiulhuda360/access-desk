"""Grants: a simulated access-granting back end plus the adapter interface a real one would implement.

Nothing here calls a real identity provider. Grants are written to a JSONL file. To connect a real system
(Okta, AWS IAM, Google Workspace, an internal RBAC service), implement ``GrantAdapter.grant`` and
``GrantAdapter.revoke`` against that provider's API and pass it to the ``GrantStore``. The rest of access-desk
does not change.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .types import AccessRequest, Level, iso


@dataclass
class Grant:
    id: str
    requester: str
    system: str
    level: Level
    granted_at: str
    expires_at: str | None
    approved_by: str
    source: str  # "auto" or an approval channel
    status: str = "active"  # active | expired | revoked
    revoked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GrantAdapter(Protocol):
    """Where a real IAM/Okta/AWS integration plugs in."""

    def grant(self, grant: Grant) -> None:
        """Provision the access in the backing system. The simulated adapter only records it."""
        ...

    def revoke(self, grant: Grant) -> None:
        """Remove the access in the backing system."""
        ...


class SimulatedAdapter:
    """The default adapter: logs what a real provider would be told to do. Provisions nothing."""

    name = "simulated"

    def grant(self, grant: Grant) -> None:  # noqa: D401 - adapter stub
        # A real adapter would here call, e.g., okta.assign_app(user, app, level) or
        # iam.attach_policy(role_arn, policy). access-desk intentionally does neither.
        return None

    def revoke(self, grant: Grant) -> None:
        return None


class GrantStore:
    """Holds grants as append-style JSONL and keeps an in-memory view for the dashboard and the expiry job."""

    def __init__(self, path: str | Path, adapter: GrantAdapter | None = None) -> None:
        self.path = Path(path)
        self.adapter = adapter or SimulatedAdapter()
        self._lock = threading.Lock()
        self._grants: dict[str, Grant] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    g = Grant(**json.loads(line))
                    self._grants[g.id] = g

    def _append(self, grant: Grant) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(grant.to_dict(), ensure_ascii=False) + "\n")

    def create(
        self, request: AccessRequest, level: Level, expires_days: float, approved_by: str, source: str, *, now: datetime | None = None
    ) -> Grant:
        now = now or datetime.now(UTC)
        expires = iso(now + timedelta(days=expires_days)) if expires_days and expires_days > 0 else None
        grant = Grant(
            id="g-" + uuid.uuid4().hex[:12],
            requester=request.requester,
            system=request.system,
            level=level,
            granted_at=iso(now),
            expires_at=expires,
            approved_by=approved_by,
            source=source,
        )
        with self._lock:
            self.adapter.grant(grant)
            self._grants[grant.id] = grant
            self._append(grant)
        return grant

    def active(self) -> list[Grant]:
        return [g for g in self._grants.values() if g.status == "active"]

    def expiring_within(self, days: float, *, now: datetime | None = None) -> list[Grant]:
        now = now or datetime.now(UTC)
        horizon = now + timedelta(days=days)
        out = []
        for g in self.active():
            if g.expires_at and now <= datetime.fromisoformat(g.expires_at) <= horizon:
                out.append(g)
        return out

    def revoke_expired(self, *, now: datetime | None = None) -> list[Grant]:
        """Revoke every active grant whose expiry has passed. Returns the grants revoked."""
        now = now or datetime.now(UTC)
        revoked: list[Grant] = []
        with self._lock:
            for g in list(self._grants.values()):
                if g.status == "active" and g.expires_at and datetime.fromisoformat(g.expires_at) <= now:
                    self.adapter.revoke(g)
                    g.status = "expired"
                    g.revoked_at = iso(now)
                    self._append(g)  # append the state change (the JSONL is a log, last line wins)
                    revoked.append(g)
        return revoked

    def snapshot(self) -> dict[str, Any]:
        active = self.active()
        return {
            "active": len(active),
            "expiring_soon": len(self.expiring_within(3)),
            "by_source": _counts(g.source for g in active),
            "by_level": _counts(g.level for g in active),
        }


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out
