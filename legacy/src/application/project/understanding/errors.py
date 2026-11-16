"""项目理解错误类型。"""

from __future__ import annotations


class ProjectUnderstandingError(ValueError):
    """The scan or its saved record cannot be verified as the same source."""

    code = "B_UNDERSTANDING_UNVERIFIED"
