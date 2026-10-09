# Security

access-desk sits in the path of access decisions, so it is built to fail safe and to keep sensitive data out of
third-party services.

## What is sent to the Jev API

For each request, access-desk sends one JSON payload to `https://api.typesafe.ai/v1/systemone` containing:

- the request: requester id, system id, requested access level, duration and ticket reference;
- context looked up in your directory: the requester's role, team and employment type, the system's sensitivity
  and description, and the access the requester already holds;
- the **justification text**, after secret redaction (see below);
- the fixed set of typed questions.

It does **not** send your policy thresholds, your audit log, your grants, or any credential. The directory fields
sent are the ones listed above and nothing else. If you run the `rules` backend, nothing leaves the machine at
all.

## Secret redaction

Justifications are free text, and people sometimes paste a token or a password into them. Before any text is sent
to a model (Jev or the LLM baseline) **or written to the audit log**, it passes through `accessdesk/redact.py`,
which masks:

- private key blocks (`-----BEGIN ... PRIVATE KEY-----`);
- `Authorization: Bearer/Basic/Token ...` headers;
- assignments to secret-like names (`*TOKEN`, `*SECRET`, `*PASSWORD`, `*API_KEY`, `*CREDENTIALS`, ...);
- `--password`, `--token`, `--secret` style flags;
- `user:password@host` in URLs;
- common token shapes (`sk-...`, `ghp_...`, `xoxb-...`, `AKIA...`, `AIza...`).

Redaction is conservative: it removes things that look like credentials and leaves ordinary text intact. It is a
safety net, not a reason to put secrets in a justification.

## Keys and configuration

- `TYPESAFE_API_KEY` (Jev) and `AI_API_KEY` (the LLM baseline) are read from the environment only. They are never
  logged, echoed, written to a file, or sent anywhere except their own provider's API.
- `ACCESSDESK_API_KEY` protects the service's own endpoints. `/v1/requests`, `/metrics` and `/v1/dashboard`
  require it; `/healthz` is open for load balancers. Keys are compared with a constant-time check.
- The service refuses to start without `ACCESSDESK_API_KEY` unless `ACCESSDESK_ALLOW_NO_AUTH=1` is set, which is
  only for a local demo.

## Fail-safe behaviour

- If Jev is unreachable or slower than `ACCESSDESK_TIMEOUT_S`, the desk falls back to the rules baseline and
  routes the request to a person. It never auto-approves on the fallback path. This is tested
  (`tests/test_desk_failsafe.py`).
- If no approval channel is configured, an escalation results in no grant (fail closed).
- Approval requests time out to **deny** (`approval.timeout_s`).
- Approval links are single-use, carry an unguessable token, and a GET only shows a confirmation form, so a chat
  client's link preview cannot approve anything.

## Grants are simulated

access-desk never calls a real identity provider. Auto-approved and approved grants are written to
`grants.jsonl` through a pluggable adapter (`accessdesk/grants.py`). The default `SimulatedAdapter` provisions
nothing. To connect Okta, AWS IAM, Google Workspace or an internal RBAC service, implement `GrantAdapter.grant`
and `GrantAdapter.revoke`; nothing else changes. This is tested (`tests/test_grants_and_safety.py`).

## Audit log

Every decision and every approval answer is appended to a JSONL audit log (`ACCESSDESK_AUDIT_LOG`). The log is
append-only (opened in append mode and flushed) and records the request, the context, the model's answers, the
policy result, who approved, the channel, and the latency. Justifications in the log are redacted.

## Reporting

This is a portfolio project. For a real deployment, add your own disclosure process here.
