"""Report and acceptance signature models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AcceptanceSignature:
    report_id: str
    content_revision: int
    content_hash: str
    user_id: str


def has_two_distinct_signers(signatures: tuple[AcceptanceSignature, ...]) -> bool:
    if not signatures:
        return False
    key = (signatures[0].report_id, signatures[0].content_revision, signatures[0].content_hash)
    users = {
        signature.user_id
        for signature in signatures
        if (signature.report_id, signature.content_revision, signature.content_hash) == key
    }
    return len(users) >= 2

