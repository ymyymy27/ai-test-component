"""Unit tests for the independent file verifier."""

from __future__ import annotations

import hashlib
from pathlib import Path

from aitest.contracts.verification import VerificationState
from aitest.infrastructure.adapters.execution.verification import (
    IndependentFileVerifier,
)


def test_verify_matches(tmp_path: Path) -> None:
    content = b"business object bytes"
    path = tmp_path / "object.bin"
    path.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    fact = IndependentFileVerifier().verify(path, expected)

    assert fact.state is VerificationState.VERIFIED
    assert fact.verified is True
    assert fact.sha256 == expected
    assert fact.size == len(content)
    assert fact.method == "fresh_file_read"


def test_mismatch_detected(tmp_path: Path) -> None:
    path = tmp_path / "object.bin"
    path.write_bytes(b"actual")
    fact = IndependentFileVerifier().verify(path, hashlib.sha256(b"expected").hexdigest())
    assert fact.state is VerificationState.MISMATCH
    assert fact.sha256 == hashlib.sha256(b"actual").hexdigest()


def test_missing_reported(tmp_path: Path) -> None:
    fact = IndependentFileVerifier().verify(tmp_path / "nope", "x")
    assert fact.state is VerificationState.MISSING
    assert not fact.verified


def test_large_file_blocked_hash(tmp_path: Path) -> None:
    # 超过一个块大小，验证跨块流式哈希正确
    content = b"x" * (70 * 1024)
    path = tmp_path / "big.bin"
    path.write_bytes(content)
    fact = IndependentFileVerifier().verify(
        path, hashlib.sha256(content).hexdigest()
    )
    assert fact.state is VerificationState.VERIFIED
    assert fact.size == len(content)
