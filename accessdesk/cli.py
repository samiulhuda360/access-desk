"""Command line.

  accessdesk triage --requester dana_ops --system prod-orders-db --level read \\
      --justification "debug ticket INC-4821" --days 2
  accessdesk serve
  accessdesk expire [--watch]

Exit codes for ``triage``: 0 granted (auto or approved), 1 denied, 2 escalated/rejected.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .audit import AuditLog
from .backends import make_backend
from .desk import Desk
from .directory import Directory
from .expiry import run_once, watch
from .grants import GrantStore
from .notify import ConsoleNotifier, notifier_from_env
from .policy import Policy
from .types import AccessRequest, Verdict

DATA = Path(__file__).resolve().parent.parent / "data"
LABEL = {"auto_approve": "AUTO-APPROVE", "escalate": "ASK A PERSON", "deny": "DENY"}


def _bar(p: float, width: int = 18) -> str:
    n = round(p * width)
    return "#" * n + "." * (width - n)


def render(verdict: Verdict, req: AccessRequest) -> str:
    lines = [f"{req.requester} wants {req.level.upper()} on {req.system}" + (f" for {req.duration_days} days" if req.duration_days else ""), ""]
    a = verdict.assessment
    if a is not None:
        rows = [
            ("justification is specific and work-related", a.justification_specific, True),
            ("fits the requester's role", a.fits_role, True),
            ("asks for more than the task needs", a.over_asking, False),
            ("touches production or customer data", a.touches_prod_pii, False),
            ("time limit stated or appropriate", a.time_limited, True),
        ]
        for name, p, _ in rows:
            lines.append(f"  {name:<46} {_bar(p)} {p:4.2f}")
        lines.append(f"  {'access level actually needed':<46} {a.needed_level} ({a.level_probs.get(a.needed_level, 0):.2f})")
        lines.append(f"  {'risk (0 harmless .. 3 critical)':<46} {_bar(a.risk_score / 3)} {a.risk_score:4.2f}")
        lines.append(f"  backend: {a.backend}   decided in {verdict.latency_ms:.0f} ms" + ("   (fallback)" if verdict.fallback else ""))
        lines.append("")
    lines.append(f"  => {LABEL[verdict.action]}  (outcome: {verdict.outcome}; rule: {verdict.rule})")
    for r in verdict.reasons:
        lines.append(f"     - {r}")
    if verdict.recommendation:
        lines.append(f"     recommendation: {verdict.recommendation}")
    if verdict.granted:
        lines.append(f"     granted {verdict.grant_level} (grant {verdict.grant_id}, expires {verdict.expires_at or 'never'})")
        if verdict.approved_by and verdict.approved_by != "auto":
            lines.append(f"     approved by {verdict.approved_by} via {verdict.approval_channel}")
    return "\n".join(lines)


def _build_desk(args: argparse.Namespace) -> Desk:
    timeout = float(os.environ.get("ACCESSDESK_TIMEOUT_S", "3"))
    name = getattr(args, "backend", None) or os.environ.get("ACCESSDESK_BACKEND") or ("jev" if os.environ.get("TYPESAFE_API_KEY") else "")
    backend = (
        make_backend(name, timeout_s=timeout, cache_dir=getattr(args, "cache_dir", None) or os.environ.get("ACCESSDESK_CACHE_DIR")) if name else None
    )
    directory = Directory.load(getattr(args, "directory", None) or os.environ.get("ACCESSDESK_DIRECTORY") or DATA / "directory.yaml")
    policy_path = getattr(args, "policy", None) or os.environ.get("ACCESSDESK_POLICY")
    grants = GrantStore(getattr(args, "grants", None) or os.environ.get("ACCESSDESK_GRANTS") or "grants.jsonl")
    audit = AuditLog(getattr(args, "audit_log", None) or os.environ.get("ACCESSDESK_AUDIT_LOG") or None)
    notifier = ConsoleNotifier() if getattr(args, "console_approval", False) else notifier_from_env(None)
    return Desk(
        backend, directory, Policy.load(policy_path) if policy_path else Policy(), grants=grants, notifier=notifier, audit=audit, timeout_s=timeout
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="accessdesk", description="Triage employee access requests. It never provisions access itself.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("triage", help="assess one access request")
    t.add_argument("--requester", required=True)
    t.add_argument("--system", required=True)
    t.add_argument("--level", required=True, choices=["none", "read", "write", "admin"])
    t.add_argument("--justification", default="")
    t.add_argument("--days", type=float, default=0.0, dest="duration_days")
    t.add_argument("--ticket", default="")
    t.add_argument("--backend", choices=["jev", "llm", "rules"])
    t.add_argument("--policy")
    t.add_argument("--directory")
    t.add_argument("--grants")
    t.add_argument("--cache-dir", dest="cache_dir")
    t.add_argument("--audit-log", dest="audit_log")
    t.add_argument("--no-ask", action="store_true", help="never ask a person; escalations become 'escalated'")
    t.add_argument("--console-approval", action="store_true")
    t.add_argument("--json", action="store_true")

    s = sub.add_parser("serve", help="run the HTTP service and dashboard")
    s.add_argument("--host", default=os.environ.get("ACCESSDESK_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("ACCESSDESK_PORT", "8080")))

    e = sub.add_parser("expire", help="revoke grants whose time limit has passed")
    e.add_argument("--grants", default=os.environ.get("ACCESSDESK_GRANTS", "grants.jsonl"))
    e.add_argument("--audit-log", dest="audit_log", default=os.environ.get("ACCESSDESK_AUDIT_LOG", ""))
    e.add_argument("--watch", action="store_true")
    e.add_argument("--interval", type=float, default=60.0)

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .service import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    if args.cmd == "expire":
        grants = GrantStore(args.grants)
        audit = AuditLog(args.audit_log or None)
        if args.watch:
            watch(grants, audit, args.interval)
            return 0
        n = run_once(grants, audit)
        print(f"revoked {n} expired grant(s)")
        return 0

    desk = _build_desk(args)
    request = AccessRequest(
        requester=args.requester,
        system=args.system,
        level=args.level,
        justification=args.justification,
        duration_days=args.duration_days,
        ticket=args.ticket,
    )
    verdict = desk.triage(request, ask_person=not args.no_ask)
    print(json.dumps(verdict.to_dict(), indent=2) if args.json else render(verdict, request))
    if verdict.granted:
        return 0
    return 1 if verdict.action == "deny" else 2


if __name__ == "__main__":
    sys.exit(main())
