"""Remove secrets from a command before it leaves the machine or reaches the audit log.

Only values that look like credentials are replaced. Encoded blobs that are not assigned to a secret-like
name are kept, because the model needs to see them to spot hidden execution.
"""

from __future__ import annotations

import re

MASK = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), MASK),
    # Authorization headers: "Authorization: Bearer xyz", "Authorization: Basic xyz"
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic|token)\s+)[^\s'\"]+"), r"\1" + MASK),
    # NAME=value where NAME looks secret (TOKEN, SECRET, PASSWORD, API_KEY, ...)
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIALS?)[A-Z0-9_]*\s*[=:]\s*)(['\"]?)[^\s'\"]+\2"
        ),
        r"\1\2" + MASK + r"\2",
    ),
    # --password value, --token=value, -p'value' style flags
    (re.compile(r"(?i)(--(?:password|passwd|token|api-key|secret|client-secret)[= ]\s*)(['\"]?)[^\s'\"]+\2"), r"\1\2" + MASK + r"\2"),
    # user:password@host in URLs
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)"), r"\1" + MASK + r"\2"),
    # Well-known token prefixes
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), MASK),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}\b"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), MASK),
]


def redact(text: str) -> str:
    """Return ``text`` with credential-looking values replaced by ``[REDACTED]``."""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
