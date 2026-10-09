"""Approval requests: ask the system owner or manager whether an access request should be granted.

Every notifier fails closed: no answer before the timeout means "deny".

- ``console``: prompt in the terminal (interactive sessions only).
- ``telegram``: a bot message with Approve / Deny buttons; the service polls the Bot API for the button press.
- ``slack``, ``teams``, ``webhook``: a message with Approve / Deny links to the service, which records the click.
  These need the HTTP service running and reachable at ``ACCESSDESK_PUBLIC_URL``.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .types import Assessment, RequestContext


@dataclass
class ApprovalRequest:
    id: str
    ctx: RequestContext
    reasons: list[str]
    recommendation: str = ""
    assessment: Assessment | None = None
    approve_url: str = ""
    deny_url: str = ""

    def summary(self) -> str:
        r = self.ctx.request
        lines = [
            f"Access request from {r.requester} ({self.ctx.requester_role}, {self.ctx.requester_team}):",
            f"  wants {r.level.upper()} on {r.system} [{self.ctx.system_sensitivity}-sensitivity, owner {self.ctx.system_owner}]",
            f"  currently has: {self.ctx.current_level}   for: {r.duration_days or 'no'} days   ticket: {r.ticket or 'none'}",
            f"  justification: {r.justification or '(none given)'}",
            "Why it needs you: " + "; ".join(self.reasons),
        ]
        if self.recommendation:
            lines.append(f"Recommendation: {self.recommendation}")
        return "\n".join(lines)


@dataclass
class ApprovalResult:
    approved: bool
    by: str = ""
    channel: str = ""
    note: str = ""


class Notifier(Protocol):
    channel: str

    def request(self, req: ApprovalRequest, timeout_s: float) -> ApprovalResult: ...


# ---- broker for link-based approvals -----------------------------------------------------------------------


@dataclass
class _Pending:
    token: str
    event: threading.Event = field(default_factory=threading.Event)
    result: ApprovalResult | None = None


class ApprovalBroker:
    """Holds pending approvals until someone clicks a link served by the HTTP service."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def open(self, request_id: str) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._pending[request_id] = _Pending(token)
        return token

    def resolve(self, request_id: str, token: str, approved: bool, by: str, channel: str) -> bool:
        with self._lock:
            p = self._pending.get(request_id)
            if p is None or p.result is not None or not secrets.compare_digest(p.token, token):
                return False
            p.result = ApprovalResult(approved=approved, by=by or "unknown", channel=channel)
            p.event.set()
            return True

    def is_open(self, request_id: str, token: str) -> bool:
        p = self._pending.get(request_id)
        return p is not None and p.result is None and secrets.compare_digest(p.token, token)

    def wait(self, request_id: str, timeout_s: float, channel: str) -> ApprovalResult:
        p = self._pending.get(request_id)
        if p is None:
            return ApprovalResult(False, channel=channel, note="unknown request")
        p.event.wait(timeout_s)
        with self._lock:
            self._pending.pop(request_id, None)
        return p.result or ApprovalResult(False, channel=channel, note="no answer before timeout")


class _LinkNotifier:
    channel = "webhook"

    def __init__(self, url: str, broker: ApprovalBroker, public_url: str, client: httpx.Client | None = None) -> None:
        self.url = url
        self.broker = broker
        self.public_url = public_url.rstrip("/")
        self.client = client or httpx.Client(timeout=10)

    def body(self, req: ApprovalRequest) -> dict[str, Any]:
        return {"text": req.summary(), "approve_url": req.approve_url, "deny_url": req.deny_url, "request_id": req.id}

    def request(self, req: ApprovalRequest, timeout_s: float) -> ApprovalResult:
        token = self.broker.open(req.id)
        base = f"{self.public_url}/approvals/{req.id}?token={token}&channel={self.channel}"
        req.approve_url, req.deny_url = f"{base}&decision=approve", f"{base}&decision=deny"
        try:
            self.client.post(self.url, json=self.body(req)).raise_for_status()
        except httpx.HTTPError as exc:
            self.broker.resolve(req.id, token, False, "", self.channel)
            return ApprovalResult(False, channel=self.channel, note=f"could not send approval request: {type(exc).__name__}")
        return self.broker.wait(req.id, timeout_s, self.channel)


class WebhookNotifier(_LinkNotifier):
    channel = "webhook"


class SlackNotifier(_LinkNotifier):
    """Slack incoming webhook with link buttons."""

    channel = "slack"

    def body(self, req: ApprovalRequest) -> dict[str, Any]:
        return {
            "text": req.summary(),
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "```" + req.summary() + "```"}},
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve"}, "url": req.approve_url},
                        {"type": "button", "style": "danger", "text": {"type": "plain_text", "text": "Deny"}, "url": req.deny_url},
                    ],
                },
            ],
        }


class TeamsNotifier(_LinkNotifier):
    """Teams incoming webhook (MessageCard) with link actions."""

    channel = "teams"

    def body(self, req: ApprovalRequest) -> dict[str, Any]:
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": "Command approval needed",
            "text": "<pre>" + req.summary() + "</pre>",
            "potentialAction": [
                {"@type": "OpenUri", "name": "Approve", "targets": [{"os": "default", "uri": req.approve_url}]},
                {"@type": "OpenUri", "name": "Deny", "targets": [{"os": "default", "uri": req.deny_url}]},
            ],
        }


class TelegramNotifier:
    """Telegram Bot API: inline Approve / Deny buttons, answers collected by long polling."""

    channel = "telegram"

    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None, api_base: str = "https://api.telegram.org") -> None:
        self._base = f"{api_base}/bot{bot_token}"
        self.chat_id = str(chat_id)
        self.client = client or httpx.Client(timeout=40)
        self._offset = 0

    def request(self, req: ApprovalRequest, timeout_s: float) -> ApprovalResult:
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": f"accessdesk:approve:{req.id}"},
                    {"text": "Deny", "callback_data": f"accessdesk:deny:{req.id}"},
                ]
            ]
        }
        try:
            self.client.post(
                f"{self._base}/sendMessage", json={"chat_id": self.chat_id, "text": req.summary(), "reply_markup": keyboard}
            ).raise_for_status()
        except httpx.HTTPError as exc:
            return ApprovalResult(False, channel=self.channel, note=f"could not send approval request: {type(exc).__name__}")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            wait = int(max(0, min(20, deadline - time.monotonic())))
            try:
                resp = self.client.get(
                    f"{self._base}/getUpdates", params={"offset": self._offset, "timeout": wait, "allowed_updates": '["callback_query"]'}
                )
                updates = resp.json().get("result", [])
            except (httpx.HTTPError, ValueError):
                time.sleep(1)
                continue
            for upd in updates:
                self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
                cq = upd.get("callback_query") or {}
                data = str(cq.get("data", ""))
                chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
                if chat != self.chat_id or not data.endswith(f":{req.id}"):
                    continue
                approved = data.startswith("accessdesk:approve:")
                who = (cq.get("from") or {}).get("username") or str((cq.get("from") or {}).get("id", "unknown"))
                with contextlib.suppress(httpx.HTTPError):
                    self.client.post(
                        f"{self._base}/answerCallbackQuery", json={"callback_query_id": cq.get("id"), "text": "Approved" if approved else "Denied"}
                    )
                return ApprovalResult(approved, by=str(who), channel=self.channel)
        return ApprovalResult(False, channel=self.channel, note="no answer before timeout")


class ConsoleNotifier:
    channel = "console"

    def request(self, req: ApprovalRequest, timeout_s: float) -> ApprovalResult:
        if not sys.stdin.isatty():
            return ApprovalResult(False, channel=self.channel, note="no interactive terminal")
        print(req.summary(), file=sys.stderr)
        answer = input("Allow this command? [y/N] ").strip().lower()
        return ApprovalResult(answer in {"y", "yes"}, by=os.environ.get("USER") or os.environ.get("USERNAME") or "console", channel=self.channel)


class DenyNotifier:
    """Used when no approval channel is configured: every 'ask' becomes a deny."""

    channel = "none"

    def request(self, req: ApprovalRequest, timeout_s: float) -> ApprovalResult:
        return ApprovalResult(False, channel=self.channel, note="no approval channel configured")


def notifier_from_env(broker: ApprovalBroker | None = None) -> Notifier:
    kind = os.environ.get("ACCESSDESK_NOTIFIER", "none").lower()
    public = os.environ.get("ACCESSDESK_PUBLIC_URL", "http://localhost:8080")
    if kind == "console":
        return ConsoleNotifier()
    if kind == "telegram":
        return TelegramNotifier(os.environ["ACCESSDESK_TELEGRAM_BOT_TOKEN"], os.environ["ACCESSDESK_TELEGRAM_CHAT_ID"])
    if kind in {"slack", "teams", "webhook"}:
        if broker is None:
            raise ValueError(f"the {kind} notifier needs the HTTP service (accessdesk serve) to receive approve/deny clicks")
        cls: type[_LinkNotifier] = {"slack": SlackNotifier, "teams": TeamsNotifier, "webhook": WebhookNotifier}[kind]
        url = os.environ[f"ACCESSDESK_{kind.upper()}_WEBHOOK_URL" if kind != "webhook" else "ACCESSDESK_WEBHOOK_URL"]
        return cls(url, broker, public)
    return DenyNotifier()
