from aitest.contracts.views import CoverageDTO
from aitest.domain.review.reports import Coverage


def coverage_dto(value: Coverage) -> CoverageDTO:
    return CoverageDTO(
        selected_applicable_count=len(value.selected),
        required_applicable_count=len(value.required),
        executed_count=len(value.executed),
        reused_count=len(value.reused),
        verified_count=len(value.verified),
        passed_count=len(value.passed),
        failed_count=len(value.failed),
        unverified_count=len(value.unverified),
        required_verified_count=len(value.verified & value.required),
    )
