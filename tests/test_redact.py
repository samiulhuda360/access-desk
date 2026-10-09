from __future__ import annotations

from accessdesk.redact import redact

# Fixtures are deliberately low-entropy, obviously-fake strings so no secret scanner treats them as real.


def test_redacts_token_prefixes() -> None:
    assert "sk-NOTAREAL" not in redact("the key is sk-NOTAREALKEYNOTAREAL")
    assert redact("here is ghp_NOTAREALNOTAREALNOTAREALNOT").count("[REDACTED]") == 1


def test_redacts_password_assignment() -> None:
    assert "notarealpw" not in redact("PASSWORD=notarealpw")
    assert "[REDACTED]" in redact("DB_SECRET=not-a-real-secret-value")


def test_redacts_google_style_key() -> None:
    # Matches the AIza... shape but is all-placeholder, so it is not a real key.
    assert "[REDACTED]" in redact("api_key: AIzaXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")


def test_redacts_bearer_header() -> None:
    assert "placeholder" not in redact("Authorization: Bearer not-a-real-placeholder-token")


def test_keeps_ordinary_text() -> None:
    text = "need read access to prod-orders-db to debug ticket INC-4821"
    assert redact(text) == text
