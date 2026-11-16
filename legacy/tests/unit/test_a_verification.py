"""Unit tests for the independent file verifier."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path

from aitest.contracts.verification import VerificationFact, VerificationState
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


# ----- A-10 边界：文件技术完整性不能替代同业务对象的独立业务核验 ----------
#
# 一期架构02 §13 与检查文档 A 包 3.2：A 的文件 hash 只证明“重新独立读取到
# 的字节与期望一致”。同业务对象的函数/API/数据库独立核验是另一条事实，
# 不允许由文件 VERIFIED 派生业务通过。


def test_verification_fact_carries_no_business_verdict_field() -> None:
    """合同结构上禁止把技术核验事实当成业务结论载体。"""
    field_names = {item.name for item in fields(VerificationFact)}
    assert field_names == {"state", "method", "sha256", "size", "detail"}
    assert "passed" not in field_names
    assert "business_result" not in field_names
    assert "evidence_grade" not in field_names


def test_equal_hashes_do_not_make_two_records_same_business_object(
    tmp_path: Path,
) -> None:
    """不同业务身份的两份记录字节相同：hash 相等不等于同一业务对象。"""
    content = json.dumps({"status": "open"}, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    issue_a = tmp_path / "issue-A.json"
    issue_b = tmp_path / "issue-B.json"
    issue_a.write_bytes(content)
    issue_b.write_bytes(content)

    verifier = IndependentFileVerifier()
    fact_a = verifier.verify(issue_a, digest)
    fact_b = verifier.verify(issue_b, digest)
    # 两个**不同业务对象**（issue-A / issue-B）都能通过字节核验；文件核验
    # 对业务身份一无所知，调用方不得据此判定二者等价或互相验证。
    assert fact_a.state is VerificationState.VERIFIED
    assert fact_b.state is VerificationState.VERIFIED
    assert fact_a.sha256 == fact_b.sha256
    assert issue_a != issue_b


def test_verified_file_bytes_cannot_substitute_independent_business_read(
    tmp_path: Path,
) -> None:
    """输入字节未变但业务状态独立变化时，文件 VERIFIED 与业务失败同时成立。"""

    # 被测产物：规则脚本字节与冻结摘要完全一致（技术完整性成立）。
    script_text = (
        "def discounted_price(cart):\n"
        "    # business expectation: members get 10% off; impl returns full price\n"
        "    return cart['price']\n"
    )
    script = script_text.encode("utf-8")
    script_path = tmp_path / "pricing_rules.py"
    script_path.write_bytes(script)
    digest = hashlib.sha256(script).hexdigest()

    technical_fact = IndependentFileVerifier().verify(script_path, digest)
    assert technical_fact.state is VerificationState.VERIFIED

    # 独立业务核验：通过与文件无关的执行/API 路径读取**同一业务对象**
    # （会员折扣规则）的实际业务结果，而不是复用文件字节。
    namespace: dict[str, object] = {}
    exec(  # noqa: S102 - 受控测试夹具，模拟独立函数/API 执行路径
        compile(script_path.read_bytes(), str(script_path), "exec"), namespace
    )
    discounted_price = namespace["discounted_price"]
    observed = discounted_price({"price": 100, "member": True})
    expected_business_value = 90

    # 两条事实必须各自独立陈述：技术 VERIFIED 不得把业务断言抬成通过，
    # 业务失败也不改变字节核验结论。
    assert technical_fact.verified is True
    assert observed != expected_business_value
    # A 侧事实没有任何字段可以被读成“业务已验证”。
    assert technical_fact.detail == ""

