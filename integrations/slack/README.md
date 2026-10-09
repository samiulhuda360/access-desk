# Slack slash-command integration

access-desk accepts a Slack slash command so employees can request access without leaving chat.

## Set up

1. Create a Slack app and add a slash command, for example `/access`.
2. Point its Request URL at your running service: `https://access-desk.your-company.internal/slack/command`.
3. (Optional) set `ACCESSDESK_SLACK_VERIFICATION_TOKEN` on the service to the app's verification token. In
   production, verify Slack's signing secret at your ingress or reverse proxy as well.

## Use

```
/access <system> <read|write|admin> [Nd] [justification]
```

Examples:

```
/access prod-orders-db read 2d debug the stuck checkout, INC-4821
/access wiki read reading the on-call runbook
/access payments-gateway admin 1d INC-5300 gateway is down
```

The command returns immediately with the decision:

- **Auto-approved** — a time-boxed grant is recorded and the employee can proceed.
- **Sent for approval** — the system owner or the employee's manager is notified through the configured channel
  (Slack, Teams, Telegram or a webhook) with Approve / Deny buttons.
- **Denied** — the request breaks a policy rule.

Every decision, and every approval click, is written to the audit log.

## Notes

- The requester is taken from the Slack username; map Slack users to directory ids at your ingress if they differ.
- The field names and the parser live in `accessdesk/service.py` (`/slack/command`). Teams works the same way
  through an outgoing webhook that posts the same form fields.
