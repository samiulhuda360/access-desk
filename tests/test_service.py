from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from accessdesk.audit import AuditLog
from accessdesk.desk import Desk
from accessdesk.directory import Directory
from accessdesk.grants import GrantStore
from accessdesk.metrics import Metrics
from accessdesk.policy import Policy
from accessdesk.service import create_app
from tests.conftest import FakeBackend, make_assessment

DATA = Path(__file__).resolve().parent.parent / "data"


def build_client(tmp_path: Path, assessment=None, key="test-key-not-real"):
    metrics = Metrics()
    desk = Desk(
        FakeBackend(assessment or make_assessment()),
        Directory.load(DATA / "directory.yaml"),
        Policy(),
        grants=GrantStore(tmp_path / "g.jsonl"),
        audit=AuditLog(tmp_path / "a.jsonl"),
        metrics=metrics,
    )
    return TestClient(create_app(desk, api_key=key))


def test_requires_api_key(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    r = client.post("/v1/requests", json={"requester": "evan_eng", "system": "wiki", "level": "read"})
    assert r.status_code == 401


def test_auto_approve_flow(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    r = client.post(
        "/v1/requests",
        headers={"X-API-Key": "test-key-not-real"},
        json={
            "requester": "evan_eng",
            "system": "wiki",
            "level": "read",
            "justification": "ticket TICK-1",
            "duration_days": 7,
            "wait_for_approval": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "auto_approved"
    assert body["granted"] is True


def test_metrics_endpoint(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    client.post(
        "/v1/requests",
        headers={"X-API-Key": "test-key-not-real"},
        json={"requester": "evan_eng", "system": "wiki", "level": "read", "justification": "TICK-1", "duration_days": 7, "wait_for_approval": False},
    )
    r = client.get("/metrics", headers={"X-API-Key": "test-key-not-real"})
    assert r.status_code == 200
    assert "accessdesk_decisions_total" in r.text
    assert "accessdesk_triage_latency_seconds_bucket" in r.text


def test_healthz_open(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    assert client.get("/healthz").json()["ok"] is True


def test_slack_command_parses(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    r = client.post("/slack/command", data={"text": "wiki read 7d read the runbook TICK-1", "user_name": "evan_eng"})
    assert r.status_code == 200
    assert "Auto-approved" in r.json()["text"]


def test_dashboard_needs_key(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    assert client.get("/v1/dashboard").status_code == 401
    r = client.get("/v1/dashboard", headers={"X-API-Key": "test-key-not-real"})
    assert r.status_code == 200
    assert "counts" in r.json()
