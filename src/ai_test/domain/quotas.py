"""Budget and evidence capacity policies."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    daily_ai_cny: int = 30
    monthly_ai_cny: int = 500
    evidence_bytes: int = 100 * 1024**3
    run_evidence_bytes: int = 200 * 1024**2
    attachment_bytes: int = 20 * 1024**2

