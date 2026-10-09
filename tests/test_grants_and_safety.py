"""Grants are simulated and time-boxed; the default adapter provisions nothing (the core safety property)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from accessdesk.audit import AuditLog
from accessdesk.expiry import run_once
from accessdesk.grants import Grant, GrantStore, SimulatedAdapter
from accessdesk.types import AccessRequest


def test_simulated_adapter_provisions_nothing(tmp_path: Path) -> None:
    calls: list[str] = []

    class SpyAdapter(SimulatedAdapter):
        def grant(self, grant: Grant) -> None:
            calls.append("grant")  # record that we were asked, but do nothing real

    store = GrantStore(tmp_path / "g.jsonl", adapter=SpyAdapter())
    g = store.create(AccessRequest("evan_eng", "wiki", "read", "x", 7), "read", 7, "auto", "auto")
    # The adapter was invoked, but it is simulated: no external system is called.
    assert calls == ["grant"]
    assert g.status == "active"
    assert g.expires_at is not None


def test_default_adapter_is_simulated(tmp_path: Path) -> None:
    store = GrantStore(tmp_path / "g.jsonl")
    assert isinstance(store.adapter, SimulatedAdapter)
    assert store.adapter.name == "simulated"


def test_expiry_revokes_past_grants(tmp_path: Path) -> None:
    store = GrantStore(tmp_path / "g.jsonl")
    past = datetime.now(UTC) - timedelta(days=10)
    store.create(AccessRequest("evan_eng", "wiki", "read", "x", 1), "read", 1, "auto", "auto", now=past)
    store.create(AccessRequest("mia_eng", "wiki", "read", "y", 30), "read", 30, "auto", "auto")  # still valid
    audit = AuditLog(tmp_path / "a.jsonl")
    n = run_once(store, audit)
    assert n == 1
    assert len(store.active()) == 1


def test_expiry_is_idempotent(tmp_path: Path) -> None:
    store = GrantStore(tmp_path / "g.jsonl")
    past = datetime.now(UTC) - timedelta(days=5)
    store.create(AccessRequest("evan_eng", "wiki", "read", "x", 1), "read", 1, "auto", "auto", now=past)
    assert run_once(store) == 1
    assert run_once(store) == 0


def test_grants_reload_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "g.jsonl"
    store = GrantStore(path)
    store.create(AccessRequest("evan_eng", "wiki", "read", "x", 7), "read", 7, "auto", "auto")
    reloaded = GrantStore(path)
    assert len(reloaded.active()) == 1
