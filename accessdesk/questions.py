"""The typed questions access-desk asks about every request, in one call.

The same wording is used for Jev (typed questions) and for the LLM baseline (a JSON prompt), so the two backends
are judged on identical questions.
"""

from __future__ import annotations

from typing import Any

from .redact import redact
from .types import RequestContext

LEVEL_CRITERIA: dict[str, str] = {
    "none": "No access is actually needed for the stated task.",
    "read": "Only reading or viewing is needed to do the stated task.",
    "write": "Creating or changing records is needed to do the stated task.",
    "admin": "Administrative control (managing other users, settings or the whole system) is needed.",
}

NOULS: dict[str, dict[str, str]] = {
    "justification_specific": {
        "instructions": "Is `justification` a specific, work-related reason that names a concrete task, ticket or deadline?",
        "true": "It names a real task, ticket, incident or deadline the access is for.",
        "false": "It is vague, generic, or gives no work reason (for example 'need it', 'for my job', 'urgent').",
    },
    "fits_role": {
        "instructions": "Would someone in `requester_role` on team `requester_team` normally need `requested_level` "
        "access to a `system_sensitivity`-sensitivity system like `system`?",
        "true": "This level of access to this kind of system is normal for the requester's role.",
        "false": "This access is unusual or excessive for the requester's role.",
    },
    "over_asking": {
        "instructions": "Does the request ask for more access than the stated task needs (more than least privilege)?",
        "true": "A lower access level, or a narrower scope, would be enough for the stated task.",
        "false": "The access asked for is the minimum the stated task needs.",
    },
    "touches_prod_pii": {
        "instructions": "Would granting this access reach production systems or customers' personal data?",
        "true": "The access touches production, or customer personal or financial data.",
        "false": "The access is limited to development, test, or non-personal data.",
    },
    "time_limited": {
        "instructions": "Is the access suitably time-boxed: either a sensible time limit is stated in `duration_days`, "
        "or the task is clearly short-lived?",
        "true": "A reasonable time limit is stated, or the task plainly ends soon.",
        "false": "No time limit is given and the access would be open-ended.",
    },
}

RISK_INSTRUCTIONS = "How much harm could result if this access were granted and it turned out to be wrong or misused?"
RISK_LEVELS: list[str] = [
    "Harmless: read access to low-sensitivity, non-production data; easy to undo.",
    "Low: routine access that fits the role, limited blast radius.",
    "High: write or broad access to sensitive or production systems, or access that does not fit the role.",
    "Critical: admin access to critical or customer-data systems, or a request that looks like misuse or social engineering.",
]

LEVEL_INSTRUCTIONS = "What access level does the stated task actually require?"


def build_state(ctx: RequestContext) -> dict[str, Any]:
    r = ctx.request
    return {
        "requester": r.requester,
        "requester_role": ctx.requester_role,
        "requester_team": ctx.requester_team,
        "employment": ctx.employment,
        "system": f"{r.system} ({ctx.system_description})" if ctx.system_description else r.system,
        "system_sensitivity": ctx.system_sensitivity,
        "requested_level": r.level,
        "current_access": ctx.current_level,
        "justification": redact(r.justification),
        "duration_days": r.duration_days or "not stated",
        "ticket": r.ticket or "none",
    }


def jev_questions() -> dict[str, Any]:
    questions: dict[str, Any] = {
        name: {"type": "noul", "instructions": q["instructions"], "criteria": {"true": q["true"], "false": q["false"]}} for name, q in NOULS.items()
    }
    questions["needed_level"] = {"type": "choice", "instructions": LEVEL_INSTRUCTIONS, "criteria": dict(LEVEL_CRITERIA)}
    questions["risk"] = {"type": "score", "instructions": RISK_INSTRUCTIONS, "criteria": RISK_LEVELS}
    return questions
