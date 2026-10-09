#!/usr/bin/env bash
# A pre-provision wrapper for shell scripts and CI steps that would otherwise grant access directly.
# It asks the access-desk service first and only continues when the desk says the access was granted.
#
#   ./request-access.sh dana_sre prod-orders-db read "debug INC-5190" 2 -- ./grant_in_okta.sh ...
#
# The part after "--" is the real provisioning command; it runs only on an approved verdict.
# Requires: curl, jq. Config via env: ACCESSDESK_URL (default http://localhost:8080), ACCESSDESK_API_KEY.
set -euo pipefail

URL="${ACCESSDESK_URL:-http://localhost:8080}"
requester="$1"; system="$2"; level="$3"; justification="${4:-}"; days="${5:-0}"; shift 5 || true
[ "${1:-}" = "--" ] && shift || true

payload=$(jq -n --arg r "$requester" --arg s "$system" --arg l "$level" --arg j "$justification" --argjson d "$days" \
  '{requester:$r, system:$s, level:$l, justification:$j, duration_days:$d, wait_for_approval:true}')

resp=$(curl -fsS -X POST "$URL/v1/requests" \
  -H "Content-Type: application/json" \
  ${ACCESSDESK_API_KEY:+-H "X-API-Key: $ACCESSDESK_API_KEY"} \
  -d "$payload")

granted=$(printf '%s' "$resp" | jq -r '.granted')
outcome=$(printf '%s' "$resp" | jq -r '.outcome')
echo "access-desk: $outcome" >&2
printf '%s' "$resp" | jq -r '.reasons[]? | "  - " + .' >&2

if [ "$granted" != "true" ]; then
  echo "access-desk: not granted; the wrapped command will NOT run." >&2
  exit 2
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi
