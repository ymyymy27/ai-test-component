"""规范 JSON/摘要的唯一口径。"""

from __future__ import annotations

import pytest

from aitest.domain.canonical import canonical_digest, canonical_json_text


def test_canonical_json_is_ordered_compact_and_non_ascii_preserving() -> None:
    assert canonical_json_text({"b": 1, "a": "中文"}) == '{"a":"中文","b":1}'
    assert canonical_json_text({"a": [2, 1]}) == '{"a":[2,1]}'


def test_canonical_digest_rejects_non_json_values() -> None:
    assert canonical_digest({"a": 1}) == canonical_digest({"a": 1})
    with pytest.raises(ValueError, match="not canonical JSON"):
        canonical_digest({"a": object()})
    with pytest.raises(ValueError, match="not canonical JSON"):
        canonical_digest({"a": float("nan")})
