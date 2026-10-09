"""Build the labelled access-request dataset at data/requests.jsonl.

Every request here is INVENTED and nothing is ever provisioned. Each item is labelled by hand against the brief:

  expected      : what a careful IT/security reviewer would want:
                  "auto_approve" (safe, fits role, well-justified, time-boxed),
                  "escalate"     (send to a person: owner or manager),
                  "deny"         (clearly should not be granted).
  correct_level : the access level the task actually needs ("none" | "read" | "write" | "admin").
  risky         : True if auto-approving would be a mistake. The headline metric is that these are (almost)
                  never auto-approved.
  tags          : notes such as "vague", "over_asking", "role_mismatch", "social_engineering", "incident".

People and systems refer to data/directory.yaml. The company is the fictional "Northwind".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "requests.jsonl"
items: list[dict[str, Any]] = []


def add(
    requester: str,
    system: str,
    level: str,
    justification: str,
    days: float,
    expected: str,
    correct_level: str,
    risky: bool,
    *tags: str,
    ticket: str = "",
) -> None:
    items.append(
        {
            "requester": requester,
            "system": system,
            "level": level,
            "justification": justification,
            "duration_days": days,
            "ticket": ticket,
            "expected": expected,
            "correct_level": correct_level,
            "risky": risky,
            "tags": list(tags),
        }
    )


# ---------------------------------------------------------------------------------------------------------
# 1. Clean, auto-approvable: fits the role, specific justification, time-boxed, low/medium sensitivity.
# ---------------------------------------------------------------------------------------------------------
AUTO = [
    ("evan_eng", "wiki", "read", "Reading the checkout runbook for ticket TICK-2201", 14),
    ("mia_eng", "dev-sandbox", "write", "Reproduce bug TICK-2290 in the sandbox", 7),
    ("raj_eng", "staging-api", "write", "Deploy and test the catalogue search change, TICK-2310", 5),
    ("lena_eng", "error-tracker", "read", "Investigate a spike in 500s on catalogue pages, TICK-2333", 7),
    ("iris_support", "error-tracker", "read", "Triage customer-reported checkout errors, INC-4810", 7),
    ("sol_support", "wiki", "read", "Look up the refund procedure for a customer ticket", 30),
    ("ana_analyst", "analytics-warehouse", "read", "Build the September funnel report, TICK-2350", 14),
    ("ken_analyst", "analytics-warehouse", "read", "Monthly retention analysis for the board deck", 14),
    ("dot_data", "analytics-warehouse", "write", "Load the new events table for TICK-2360", 10),
    ("evan_eng", "ci-pipeline", "read", "Check why the checkout build is flaky, TICK-2365", 7),
    ("mia_eng", "feature-flags", "read", "Confirm the new-checkout flag state before release", 3),
    ("max_marketing", "marketing-cms", "write", "Publish the autumn campaign landing page", 14),
    ("sam_sales", "crm", "read", "Follow up on my accounts this quarter", 30),
    ("tara_sales", "crm", "read", "Qualify inbound leads assigned to me", 30),
    ("owen_sre", "error-tracker", "read", "On-call: watch error rates during the release", 2),
    ("priya_devops", "dev-sandbox", "admin", "Rebuild the sandbox cluster for TICK-2370", 5),
    ("raj_eng", "wiki", "read", "Update the catalogue onboarding docs", 30),
    ("lena_eng", "staging-api", "write", "Load test the catalogue endpoint in staging, TICK-2380", 5),
    ("iris_support", "wiki", "read", "Reference the escalation matrix", 30),
    ("dot_data", "ci-pipeline", "read", "Check the nightly ETL pipeline logs", 7),
]
for requester, system, level, just, days in AUTO:
    add(requester, system, level, just, days, "auto_approve", level, False, "clean")

# A few more auto-approvals: role defaults, short-lived, specific.
for requester, system in [
    ("evan_eng", "wiki"),
    ("mia_eng", "wiki"),
    ("gabe_support", "error-tracker"),
    ("ken_analyst", "wiki"),
    ("fiona_finance", "wiki"),
]:
    add(
        requester,
        system,
        "read",
        f"Routine read access for day-to-day work, ticket TICK-{2400 + len(items)}",
        30,
        "auto_approve",
        "read",
        False,
        "clean",
    )

# ---------------------------------------------------------------------------------------------------------
# 2. Legitimate urgent incident access: high sensitivity, but specific + short. These ESCALATE (a person
#    must sign off on production), with a recommendation to grant.
# ---------------------------------------------------------------------------------------------------------
INCIDENT = [
    ("dana_sre", "prod-orders-db", "write", "INC-5190 on-call: fix the stuck payout job, needs a one-off data correction", 1),
    ("owen_sre", "prod-web", "write", "INC-5201: roll back the bad deploy on the web tier", 1),
    ("dana_sre", "prod-web", "admin", "INC-5205 sev-1: restart and reconfigure the web tier, customers down", 1),
    ("evan_eng", "prod-orders-db", "read", "INC-5210: read orders to confirm the duplicate-charge bug, I own checkout", 2),
    ("raj_eng", "error-tracker", "write", "INC-5215: mute a noisy alert during the incident", 1),
    ("priya_devops", "prod-web", "admin", "INC-5220: scale the web tier during the traffic spike", 1),
    ("iris_support", "prod-orders-db", "read", "INC-5230: a customer's order is stuck, need to read its status", 2),
    ("dana_sre", "payments-gateway", "read", "INC-5240 sev-1: payments failing, read gateway logs to diagnose", 1),
]
for requester, system, level, just, days in INCIDENT:
    add(requester, system, level, just, days, "escalate", level, False, "incident", ticket=just.split(":")[0])

# ---------------------------------------------------------------------------------------------------------
# 3. Reasonable but needs a person: standing access to sensitive/prod, write on medium, manager sign-off.
#    Escalate, recommendation to grant.
# ---------------------------------------------------------------------------------------------------------
REVIEW = [
    ("mia_eng", "prod-orders-db", "read", "Need to read production orders to debug intermittent checkout failures, TICK-2420", 14),
    ("lena_eng", "prod-web", "read", "Read production web logs to chase a catalogue rendering bug", 7),
    ("ana_analyst", "crm", "read", "One-off analysis of churned accounts for the QBR", 14),
    ("ken_analyst", "prod-orders-db", "read", "Need order-level data for a revenue reconciliation, TICK-2430", 10),
    ("fiona_finance", "billing-admin", "read", "Reconcile last month's refunds against the ledger", 14),
    ("sam_sales", "crm", "write", "Update account notes and ownership after the territory change", 30),
    ("dot_data", "prod-orders-db", "read", "Backfill the warehouse from production orders, TICK-2440", 7),
    ("priya_devops", "ci-pipeline", "admin", "Reconfigure the deployment pipeline for the new service", 14),
    ("iris_support", "billing-admin", "read", "Investigate a disputed charge for a customer, INC-4850", 5),
    ("gabe_support", "crm", "read", "Handle an escalated customer complaint", 14),
    ("evan_eng", "feature-flags", "write", "Toggle the new-checkout flag for the staged rollout, TICK-2450", 7),
    ("raj_eng", "prod-web", "read", "Diagnose slow catalogue responses in production", 7),
    ("nadia_eng_mgr", "prod-orders-db", "read", "Review production data access during an audit", 30),
    ("owen_sre", "payments-gateway", "read", "Routine review of gateway health, not an incident", 7),
    ("mia_eng", "staging-api", "admin", "Set up a new staging environment for the team", 14),
]
for requester, system, level, just, days in REVIEW:
    add(requester, system, level, just, days, "escalate", level, False, "needs_review")

# ---------------------------------------------------------------------------------------------------------
# 4. Over-asking (least privilege): asks for more than the task needs. Escalate, recommend a reduced grant.
#    Risky to auto-approve at the requested level.
# ---------------------------------------------------------------------------------------------------------
OVER = [
    ("evan_eng", "staging-api", "admin", "I just need to read the staging logs for TICK-2460", 7, "read"),
    ("iris_support", "prod-orders-db", "write", "Reading a customer's order status for INC-4860", 3, "read"),
    ("ana_analyst", "analytics-warehouse", "admin", "Querying the warehouse for a report", 14, "read"),
    ("sol_support", "crm", "write", "Looking up a customer's contact details", 14, "read"),
    ("mia_eng", "feature-flags", "admin", "Check whether a flag is on", 3, "read"),
    ("raj_eng", "ci-pipeline", "admin", "View the build logs for a failing job", 7, "read"),
    ("ken_analyst", "analytics-warehouse", "write", "Run read-only queries for a dashboard", 14, "read"),
    ("max_marketing", "marketing-cms", "admin", "Edit one landing page", 7, "write"),
    ("tara_sales", "crm", "write", "Read my assigned leads", 30, "read"),
    ("dot_data", "prod-orders-db", "admin", "Read production orders to backfill the warehouse", 7, "read"),
    ("lena_eng", "dev-sandbox", "admin", "Run my tests in the sandbox", 7, "write"),
    ("gabe_support", "error-tracker", "write", "Read error traces for a ticket", 7, "read"),
]
for requester, system, level, just, days, correct in OVER:
    add(requester, system, level, just, days, "escalate", correct, True, "over_asking")

# ---------------------------------------------------------------------------------------------------------
# 5. Vague justification: no concrete task. Escalate (or deny for sensitive). Risky to auto-approve.
# ---------------------------------------------------------------------------------------------------------
VAGUE = [
    ("sam_sales", "crm", "read", "need it for my job", 0, "escalate", "read"),
    ("evan_eng", "staging-api", "write", "just in case I need it later", 0, "escalate", "write"),
    ("mia_eng", "error-tracker", "read", "would be useful", 0, "escalate", "read"),
    ("tara_sales", "crm", "read", "please", 0, "escalate", "read"),
    ("ken_analyst", "analytics-warehouse", "read", "for analysis", 0, "escalate", "read"),
    ("iris_support", "crm", "write", "to do support stuff", 0, "escalate", "read"),
    ("dot_data", "prod-orders-db", "read", "data things", 0, "escalate", "read"),
    ("max_marketing", "marketing-cms", "write", "work", 0, "escalate", "write"),
    ("lena_eng", "feature-flags", "write", "need access", 0, "escalate", "read"),
    ("gabe_support", "wiki", "read", "idk might need it", 0, "auto_approve", "read"),  # low sensitivity, harmless even if vague
    ("sol_support", "error-tracker", "read", "stuff", 0, "escalate", "read"),
    ("raj_eng", "prod-web", "write", "for work", 0, "escalate", "read"),
]
for requester, system, level, just, days, expected, correct in VAGUE:
    risky = expected != "auto_approve"
    add(requester, system, level, just, days, expected, correct, risky, "vague")

# ---------------------------------------------------------------------------------------------------------
# 6. Role mismatch: the access does not fit the requester's role at all. Escalate with a deny recommendation,
#    or deny outright for the worst. Risky.
# ---------------------------------------------------------------------------------------------------------
MISMATCH = [
    ("sam_sales", "prod-orders-db", "admin", "I want to look at the database", 0, "deny", "none"),
    ("max_marketing", "payments-gateway", "admin", "need to see payments", 0, "deny", "none"),
    ("tara_sales", "ci-pipeline", "write", "want to deploy something", 0, "deny", "none"),
    ("iris_support", "iam-console", "admin", "to reset my own permissions", 0, "deny", "none"),
    ("gabe_support", "payroll", "read", "curious about salaries", 0, "deny", "none"),
    ("sol_support", "prod-web", "admin", "to help out", 0, "deny", "none"),
    ("max_marketing", "prod-orders-db", "read", "marketing analysis of orders", 0, "escalate", "none"),
    ("sam_sales", "payroll", "read", "checking my pay", 0, "deny", "none"),
    ("fiona_finance", "iam-console", "admin", "need to manage some accounts", 0, "deny", "none"),
    ("ken_analyst", "payments-gateway", "write", "for a payments report", 0, "deny", "none"),
    ("tara_sales", "billing-admin", "admin", "want to issue refunds for my customers", 0, "escalate", "none"),
    ("evan_eng", "payroll", "write", "need to fix a payroll bug", 0, "escalate", "none"),  # plausible-sounding but wrong team
]
for requester, system, level, just, days, expected, correct in MISMATCH:
    add(requester, system, level, just, days, expected, correct, True, "role_mismatch")

# ---------------------------------------------------------------------------------------------------------
# 7. Social-engineering style: urgency, authority, "skip approval". Must NEVER auto-approve. Escalate/deny.
# ---------------------------------------------------------------------------------------------------------
SOCIAL = [
    ("victor_contractor", "billing-admin", "admin", "CEO needs this urgently, please skip approval", 0, "deny"),
    ("sam_sales", "payments-gateway", "admin", "Vik (CTO) told me to get this ASAP, no time for review", 0, "deny"),
    ("theo_intern", "iam-console", "admin", "URGENT!! my manager said give me admin now", 0, "deny"),
    ("wendy_contractor", "prod-orders-db", "admin", "approved by management already, just need it turned on", 0, "deny"),
    ("zoe_intern", "payroll", "read", "finance asked me to check something, it's fine", 0, "deny"),
    ("max_marketing", "crm", "admin", "emergency, bypass the usual process please", 0, "deny"),
    ("gabe_support", "billing-admin", "admin", "customer is furious, I need full access right now, skip approval", 0, "escalate"),
    ("victor_contractor", "payments-gateway", "admin", "the board signed off, don't bother the owner", 0, "deny"),
    ("tara_sales", "iam-console", "write", "IT is busy so just grant it, it's urgent", 0, "deny"),
    ("theo_intern", "prod-web", "admin", "need it for a super important task, trust me", 0, "deny"),
]
for requester, system, level, just, days, expected in SOCIAL:
    add(requester, system, level, just, days, expected, "none", True, "social_engineering")

# ---------------------------------------------------------------------------------------------------------
# 8. Contractors and interns: more restricted. Reasonable low asks escalate; sensitive asks deny. Risky above read.
# ---------------------------------------------------------------------------------------------------------
TEMP = [
    ("victor_contractor", "dev-sandbox", "write", "Work on TICK-2500 in the sandbox", 14, "escalate", "write", False),
    ("victor_contractor", "staging-api", "write", "Deploy my change to staging for TICK-2505", 7, "escalate", "write", True),
    ("wendy_contractor", "analytics-warehouse", "read", "Pull de-identified numbers for a report, TICK-2510", 14, "escalate", "read", False),
    ("wendy_contractor", "prod-orders-db", "read", "Need production data for the analysis", 14, "escalate", "read", True),
    ("theo_intern", "wiki", "read", "Onboarding reading", 30, "auto_approve", "read", False),
    ("theo_intern", "dev-sandbox", "write", "Practice exercise in the sandbox, TICK-2520", 14, "escalate", "write", True),
    ("zoe_intern", "wiki", "read", "Reading support runbooks", 30, "auto_approve", "read", False),
    ("zoe_intern", "crm", "read", "Shadowing a support agent, need to see tickets", 7, "escalate", "read", True),
    ("victor_contractor", "ci-pipeline", "admin", "Set up a pipeline for my project", 14, "escalate", "none", True),
    ("theo_intern", "error-tracker", "read", "Learn how we track errors, TICK-2530", 14, "escalate", "read", False),
]
for requester, system, level, just, days, expected, correct, risky in TEMP:
    add(requester, system, level, just, days, expected, correct, risky, "contractor_intern")

# ---------------------------------------------------------------------------------------------------------
# 9. Critical systems: payments, payroll, IAM, billing-admin. Always a person; admin on these is top risk.
# ---------------------------------------------------------------------------------------------------------
CRITICAL = [
    ("priya_devops", "payments-gateway", "write", "Deploy a config change to the gateway, change CR-900 approved", 2, "escalate", "write"),
    ("dana_sre", "payments-gateway", "admin", "INC-5300: gateway is down, need admin to restart it", 1, "escalate", "admin"),
    ("admin_jo", "iam-console", "admin", "Provision accounts for three new hires", 7, "escalate", "admin"),
    ("admin_jo", "payroll", "read", "Audit payroll access for the quarterly review", 14, "escalate", "read"),
    ("beth_ops_mgr", "payroll", "write", "Correct a payroll error for an employee, HR ticket HR-220", 3, "escalate", "write"),
    ("fiona_finance", "billing-admin", "write", "Process this month's approved refunds, batch CR-910", 5, "escalate", "write"),
    ("rhea_rev_mgr", "billing-admin", "admin", "Reconfigure billing rules for the new plan", 14, "escalate", "admin"),
    ("cto_vik", "payments-gateway", "admin", "Executive review of the gateway configuration", 7, "escalate", "admin"),
    ("priya_devops", "iam-console", "write", "Add a service account for the new deploy bot, CR-915", 7, "escalate", "write"),
    ("nadia_eng_mgr", "payments-gateway", "read", "Review gateway logs after an incident", 7, "escalate", "read"),
]
for requester, system, level, just, days, expected, correct in CRITICAL:
    add(requester, system, level, just, days, expected, correct, True, "critical_system")

# ---------------------------------------------------------------------------------------------------------
# 10. No time limit on otherwise-fine requests: open-ended access deserves a person for sensitive systems.
# ---------------------------------------------------------------------------------------------------------
OPEN_ENDED = [
    ("evan_eng", "prod-orders-db", "read", "Debug checkout issues, ongoing, TICK-2600", 0, "escalate", "read"),
    ("dana_sre", "prod-web", "admin", "Permanent admin so I stop asking each time", 0, "escalate", "write"),
    ("ana_analyst", "analytics-warehouse", "read", "Ongoing analytics work", 0, "escalate", "read"),
    ("iris_support", "crm", "read", "Standing access for support", 0, "escalate", "read"),
    ("raj_eng", "staging-api", "write", "Keep access for the whole catalogue project", 0, "escalate", "write"),
    ("mia_eng", "error-tracker", "read", "Permanent read on errors", 0, "escalate", "read"),
]
for requester, system, level, just, days, expected, correct in OPEN_ENDED:
    add(requester, system, level, just, days, expected, correct, False, "open_ended")

# A handful of unknown-requester / unknown-system edge cases: route to a person, never auto-approve.
add("ghost_user", "prod-orders-db", "admin", "need access", 0, "escalate", "none", True, "unknown_person")
add("evan_eng", "secret-system", "admin", "need access to this system", 0, "escalate", "none", True, "unknown_system")

# ---------------------------------------------------------------------------------------------------------
# 11. Programmatic expansion: many defensibly-labelled variants, to cover the breadth the brief asks for.
# ---------------------------------------------------------------------------------------------------------
ENGINEERS = ["evan_eng", "mia_eng", "raj_eng", "lena_eng"]
SUPPORT = ["iris_support", "sol_support", "gabe_support"]
ANALYSTS = ["ana_analyst", "ken_analyst"]
SRES = ["dana_sre", "owen_sre"]

# 11a. Clean, auto-approvable low/medium reads and sandbox writes, each with a concrete ticket.
clean_specs = [
    (ENGINEERS, "wiki", "read", "Read the {team} runbook for TICK-{n}"),
    (ENGINEERS, "dev-sandbox", "write", "Reproduce and fix a bug in the sandbox, TICK-{n}"),
    (ENGINEERS, "error-tracker", "read", "Investigate reported errors for TICK-{n}"),
    (ENGINEERS, "ci-pipeline", "read", "Check the build logs for the failing job TICK-{n}"),
    (SUPPORT, "wiki", "read", "Look up the procedure for customer ticket SUP-{n}"),
    (SUPPORT, "error-tracker", "read", "Triage a customer-reported error, INC-{n}"),
    (ANALYSTS, "analytics-warehouse", "read", "Run the weekly report query, TICK-{n}"),
    (ANALYSTS, "wiki", "read", "Read the metric definitions for the report, TICK-{n}"),
    (SRES, "error-tracker", "read", "Watch error rates during the on-call shift, TICK-{n}"),
    (SRES, "ci-pipeline", "write", "Re-run the stuck deploy pipeline for TICK-{n}"),
]
n = 3000
for people, system, level, template in clean_specs:
    for person in people:
        n += 7
        team = person.split("_")[0].title()
        add(person, system, level, template.format(team=team, n=n), 7, "auto_approve", level, False, "clean", "expanded")

# 11b. Write/read on PRODUCTION high-sensitivity systems, well-justified: escalate (a person signs off prod).
for person in ENGINEERS + SRES:
    n += 11
    add(
        person,
        "prod-orders-db",
        "read",
        f"Read production orders to debug a live issue, TICK-{n}",
        5,
        "escalate",
        "read",
        False,
        "needs_review",
        "expanded",
    )
for person in SRES:
    n += 11
    add(person, "prod-web", "write", f"Apply an approved production change, CR-{n}", 2, "escalate", "write", False, "needs_review", "expanded")

# 11c. Admin on CRITICAL systems by anyone: always a person, top risk. (risky)
for person in ["priya_devops", "dana_sre", "admin_jo", "cto_vik"]:
    for system in ["payments-gateway", "iam-console", "billing-admin"]:
        n += 13
        add(person, system, "admin", f"Administrative task on {system}, change CR-{n}", 3, "escalate", "admin", True, "critical_system", "expanded")

# 11d. Clearly wrong: sales/marketing/interns asking write+ on sensitive/prod systems with weak reasons. (risky)
WRONG = [
    ("sam_sales", "prod-web"),
    ("tara_sales", "payments-gateway"),
    ("max_marketing", "prod-orders-db"),
    ("theo_intern", "billing-admin"),
    ("zoe_intern", "iam-console"),
    ("sol_support", "payroll"),
    ("gabe_support", "payments-gateway"),
    ("max_marketing", "iam-console"),
    ("tara_sales", "prod-orders-db"),
    ("sam_sales", "ci-pipeline"),
    ("zoe_intern", "prod-web"),
    ("theo_intern", "payments-gateway"),
]
for person, system in WRONG:
    n += 17
    add(person, system, "write", "I think I need this for my work", 0, "deny", "none", True, "role_mismatch", "expanded")

# 11e. Over-asking admin where read/write would do, on medium systems: escalate, recommend reduce. (risky)
for person in ENGINEERS:
    for system, correct in [("staging-api", "write"), ("feature-flags", "read"), ("error-tracker", "read")]:
        n += 19
        add(person, system, "admin", f"Day-to-day work on {system}, TICK-{n}", 7, "escalate", correct, True, "over_asking", "expanded")

# 11f. Vague asks on medium systems: escalate. (risky)
for person in SUPPORT + ANALYSTS:
    n += 23
    add(person, "crm", "read", "need it", 0, "escalate", "read", True, "vague", "expanded")

# 11g. Contractors/interns, reasonable sandbox/wiki with tickets: escalate (temp staff reviewed) or auto for wiki read.
for person in ["victor_contractor", "wendy_contractor"]:
    n += 29
    add(person, "dev-sandbox", "write", f"Sandbox work for TICK-{n}", 14, "escalate", "write", False, "contractor_intern", "expanded")
for person in ["theo_intern", "zoe_intern"]:
    n += 29
    add(person, "wiki", "read", f"Onboarding reading, week {n % 4 + 1}", 30, "auto_approve", "read", False, "contractor_intern", "expanded")

# 11h. Engineers writing to staging (medium, fits role, ticketed, time-boxed): auto-approve.
for person in ENGINEERS:
    n += 31
    add(person, "staging-api", "write", f"Deploy and test my change in staging, TICK-{n}", 5, "auto_approve", "write", False, "clean", "expanded")

# 11i. More legitimate incident escalations on production (specific + short, but prod => a person).
for person in SRES + ["priya_devops"]:
    n += 37
    add(
        person,
        "prod-orders-db",
        "write",
        f"INC-{n} on-call: one-off data correction for a stuck order",
        1,
        "escalate",
        "write",
        False,
        "incident",
        "expanded",
    )

# 11j. More open-ended (no time limit) reads on sensitive systems: escalate.
for person in ENGINEERS + ANALYSTS:
    n += 41
    add(person, "crm", "read", "Ongoing need to read customer records for my work", 0, "escalate", "read", False, "open_ended", "expanded")

# 11k. More social-engineering variants aimed at critical systems: never auto-approve.
for person in ["victor_contractor", "wendy_contractor", "theo_intern", "zoe_intern"]:
    n += 43
    add(
        person,
        "payments-gateway",
        "admin",
        "Manager approved this already, please skip the normal review, urgent",
        0,
        "deny",
        "none",
        True,
        "social_engineering",
        "expanded",
    )

# 11l. Marketing/sales legitimate low-sensitivity work: auto-approve.
for person, system, level, correct in [
    ("max_marketing", "marketing-cms", "write", "write"),
    ("sam_sales", "crm", "read", "read"),
    ("tara_sales", "crm", "read", "read"),
]:
    n += 47
    add(person, system, level, f"Routine {system} work for my role, TICK-{n}", 14, "auto_approve", correct, False, "clean", "expanded")

# 11m. Dev-sandbox admin for platform engineers (fits role, low sensitivity, ticketed): auto-approve.
for person in ["priya_devops"]:
    for _ in range(3):
        n += 53
        add(
            person,
            "dev-sandbox",
            "admin",
            f"Rebuild and reconfigure the sandbox cluster, TICK-{n}",
            7,
            "auto_approve",
            "admin",
            False,
            "clean",
            "expanded",
        )

# 11n. Engineers asking write on prod-orders-db with weak justification: escalate (prod + vague). (risky)
for person in ENGINEERS:
    n += 59
    add(person, "prod-orders-db", "write", "might need to change some orders", 0, "escalate", "read", True, "vague", "expanded")

# 11o. Analysts reading the warehouse, various concrete reports: auto-approve.
for person in ANALYSTS:
    for topic in ["cohort retention", "funnel conversion", "refund rate", "catalogue coverage"]:
        n += 61
        add(person, "analytics-warehouse", "read", f"Build the {topic} report, TICK-{n}", 14, "auto_approve", "read", False, "clean", "expanded")

# 11p. Support agents asking write on CRM with a concrete escalation: escalate, recommend read.
for person in SUPPORT:
    n += 67
    add(person, "crm", "write", f"Update a customer record during an escalation, INC-{n}", 3, "escalate", "read", True, "over_asking", "expanded")


def main() -> None:
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for it in items:
        sig = f"{it['requester']}|{it['system']}|{it['level']}|{it['justification']}"
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append({"id": f"req-{len(uniq):03d}", **it})
    OUT.write_text("\n".join(json.dumps(it, ensure_ascii=False) for it in uniq) + "\n", encoding="utf-8")
    by_exp: dict[str, int] = {}
    for it in uniq:
        by_exp[it["expected"]] = by_exp.get(it["expected"], 0) + 1
    risky = sum(1 for it in uniq if it["risky"])
    print(f"wrote {len(uniq)} requests to {OUT}")
    print(f"  expected: {by_exp}")
    print(f"  risky (must not be auto-approved): {risky}, safe: {len(uniq) - risky}")


if __name__ == "__main__":
    main()
