"""HTTP service: request intake, Slack slash-command, approval links, dashboard, /metrics, /healthz."""

from __future__ import annotations

import hmac
import html
import os
import re
from importlib import resources
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .desk import Desk
from .metrics import Metrics
from .notify import ApprovalBroker
from .types import AccessRequest, Level


class TriageRequest(BaseModel):
    requester: str = Field(min_length=1, max_length=120)
    system: str = Field(min_length=1, max_length=120)
    level: Level = "read"
    justification: str = Field(default="", max_length=4000)
    duration_days: float = Field(default=0.0, ge=0, le=3650)
    ticket: str = Field(default="", max_length=120)
    wait_for_approval: bool = True


_SLACK = re.compile(
    r"(?P<system>\S+)\s+(?P<level>none|read|write|admin)(?:\s+(?P<days>\d+)d)?(?:\s+(?P<just>.*))?",
    re.I,
)


def create_app(desk: Desk | None = None, *, api_key: str | None = None, broker: ApprovalBroker | None = None) -> FastAPI:
    key = api_key if api_key is not None else os.environ.get("ACCESSDESK_API_KEY", "")
    if not key and os.environ.get("ACCESSDESK_ALLOW_NO_AUTH") != "1":
        raise RuntimeError("Set ACCESSDESK_API_KEY (or ACCESSDESK_ALLOW_NO_AUTH=1 for a local demo only).")
    broker = broker or ApprovalBroker()
    metrics = Metrics()
    desk = desk or Desk.from_env(broker=broker, metrics=metrics)
    app = FastAPI(title="access-desk", version="1.0.0", docs_url="/docs")

    def require_key(
        x_api_key: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not key:
            return
        given = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
        if not given or not hmac.compare_digest(given.encode(), key.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong API key")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "backend": desk.backend.name if desk.backend else None, "policy": desk.policy.name}

    @app.post("/v1/requests", dependencies=[Depends(require_key)])
    def submit(req: TriageRequest) -> dict[str, Any]:
        request = AccessRequest(
            requester=req.requester,
            system=req.system,
            level=req.level,
            justification=req.justification,
            duration_days=req.duration_days,
            ticket=req.ticket,
        )
        return desk.triage(request, ask_person=req.wait_for_approval).to_dict()

    @app.post("/slack/command")
    def slack_command(text: str = Form(""), user_name: str = Form(""), user_id: str = Form(""), token: str = Form("")) -> JSONResponse:
        # Slack verifies with a signing secret in production; a shared verification token is checked here if set.
        want = os.environ.get("ACCESSDESK_SLACK_VERIFICATION_TOKEN", "")
        if want and not hmac.compare_digest(token.encode(), want.encode()):
            raise HTTPException(status_code=401, detail="bad Slack token")
        m = _SLACK.match(text.strip())
        if not m:
            return JSONResponse({"response_type": "ephemeral", "text": "Usage: /access <system> <read|write|admin> [Nd] [justification]"})
        request = AccessRequest(
            requester=user_name or user_id or "slack-user",
            system=m.group("system"),
            level=m.group("level").lower(),  # type: ignore[arg-type]
            justification=(m.group("just") or "").strip(),
            duration_days=float(m.group("days")) if m.group("days") else 0.0,
        )
        verdict = desk.triage(request, ask_person=False)  # Slack returns immediately; escalations are notified out of band
        msg = {
            "auto_approve": f"Auto-approved: {request.level} on {request.system} (expires {verdict.expires_at}).",
            "escalate": f"Sent to {desk.directory.enrich(request).system_owner or 'the system owner'} for approval.",
            "deny": "Denied by policy.",
        }[verdict.action]
        return JSONResponse({"response_type": "ephemeral", "text": msg + " " + "; ".join(verdict.reasons[:2])})

    @app.get("/metrics", dependencies=[Depends(require_key)], response_class=PlainTextResponse)
    def metrics_endpoint() -> str:
        return desk.metrics.render()

    @app.get("/v1/dashboard", dependencies=[Depends(require_key)])
    def dashboard_data() -> dict[str, Any]:
        rows = desk.audit.read()
        decisions = [r for r in rows if r.get("event") == "decision"]
        counts: dict[str, int] = {}
        for r in decisions:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        recent = [
            {
                "request_id": r["request_id"],
                "requester": r["context"]["request"]["requester"],
                "system": r["context"]["request"]["system"],
                "level": r["context"]["request"]["level"],
                "outcome": r["outcome"],
                "action": r["action"],
                "recommendation": r.get("recommendation", ""),
                "ts": r["ts"],
            }
            for r in decisions[-40:][::-1]
        ]
        return {"counts": counts, "grants": desk.grants.snapshot(), "recent": recent}

    @app.get("/approvals/{request_id}", response_class=HTMLResponse)
    def approval_page(request_id: str, token: str = Query(...), decision: str = Query("approve"), channel: str = Query("webhook")) -> str:
        if not broker.is_open(request_id, token):
            return _page("This request is closed", "<p>It was already answered or it timed out.</p>")
        verb = "Approve" if decision == "approve" else "Deny"
        form = (
            f'<form method="post"><input type="hidden" name="token" value="{html.escape(token)}">'
            f'<input type="hidden" name="decision" value="{html.escape(decision)}">'
            f'<input type="hidden" name="channel" value="{html.escape(channel)}">'
            f'<label>Your name <input name="by" required></label> <button>{verb}</button></form>'
        )
        return _page(f"{verb} access request {html.escape(request_id)}?", form)

    @app.post("/approvals/{request_id}", response_class=HTMLResponse)
    def approval_submit(
        request_id: str, token: str = Form(...), decision: str = Form(...), by: str = Form(""), channel: str = Form("webhook")
    ) -> str:
        ok = broker.resolve(request_id, token, decision == "approve", by.strip()[:80], channel)
        desk.audit.write("approval_click", request_id=request_id, decision=decision, by=by.strip()[:80], channel=channel, accepted=ok)
        if not ok:
            return _page("This request is closed", "<p>It was already answered or it timed out.</p>")
        return _page("Recorded", f"<p>{'Approved' if decision == 'approve' else 'Denied'}. You can close this tab.</p>")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return resources.files("accessdesk").joinpath("static/index.html").read_text(encoding="utf-8")

    app.state.desk = desk
    app.state.broker = broker
    app.state.metrics = metrics
    return app


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{title}</title><style>body{{font:16px system-ui;margin:40px;color:#1d2327;background:#f3f1ea}}"
        "button{padding:8px 16px;background:#24584f;color:#fff;border:0;border-radius:4px}</style>"
        f"<h1>{title}</h1>{body}"
    )


__all__ = ["create_app"]
