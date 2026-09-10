from ai_test.domain.reports import AcceptanceSignature, has_two_distinct_signers


def test_acceptance_requires_distinct_signers() -> None:
    signatures = (
        AcceptanceSignature("report-1", 1, "abc123", "alice"),
        AcceptanceSignature("report-1", 1, "abc123", "bob"),
    )
    assert has_two_distinct_signers(signatures)
