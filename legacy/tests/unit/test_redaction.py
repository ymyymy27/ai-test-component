from aitest.infrastructure.adapters.execution.redaction import (
    StreamingRedactor,
    redact_bytes,
)


def test_exact_secret_is_redacted_across_chunks() -> None:
    redactor = StreamingRedactor(("super-secret-value",))
    assert redactor.feed(b"prefix super-secret-") == b""
    assert redactor.feed(b"value suffix\n") == b"prefix [REDACTED] suffix\n"


def test_key_value_and_authorization_values_are_redacted() -> None:
    output = redact_bytes(
        b"password=hunter2\nAuthorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz1234\n"
    )
    assert b"hunter2" not in output
    assert b"password=[REDACTED]" in output
    assert b"Authorization: Bearer [REDACTED]" in output


def test_common_token_shapes_are_redacted() -> None:
    output = redact_bytes(
        b"sk-abcdefghijklmnopqrstuvwx\neyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature\n"
    )
    assert b"sk-abcdefghijklmnopqrstuvwx" not in output
    assert b"eyJhbGciOiJIUzI1NiJ9" not in output
    assert output.count(b"[REDACTED]") == 2


def test_redaction_stats_count_replacements() -> None:
    redactor = StreamingRedactor(("secret-value",))
    output = redactor.feed(b"token=secret-value\n")
    output += redactor.finish()
    assert b"secret-value" not in output
    assert redactor.stats.replacement_count >= 1
