"""步骤 5.3：12 题跨类型埋雷基准考题库多轮定量与一致性验证。"""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.benchmark_suite import evaluate_benchmark_suite


def test_12_case_cross_stack_benchmark_recall_fp_and_consistency(tmp_path: Path) -> None:
    round1 = evaluate_benchmark_suite(tmp_path / "round1")
    round2 = evaluate_benchmark_suite(tmp_path / "round2")

    assert round1.total == 12
    assert round1.planted_bugs == 8
    assert round1.clean_cases == 4

    types = {o.project_type for o in round1.outcomes}
    assert types == {
        "CLI 工具",
        "Python 库",
        "纯 API 服务",
        "单文件 Web",
        "前后端分离全栈",
    }

    # 全部 12 题达到预期：漏测率 0%、误报率 0%
    assert round1.recall_rate == 1.0
    assert round1.false_positive_rate == 0.0
    assert all(o.passed_expectation for o in round1.outcomes), [
        o for o in round1.outcomes if not o.passed_expectation
    ]

    # 第二轮与第一轮在检测结果和信号上 100% 一致
    matches = sum(
        1
        for o1, o2 in zip(round1.outcomes, round2.outcomes, strict=True)
        if o1.case_id == o2.case_id
        and o1.detected_bug == o2.detected_bug
        and o1.passed_expectation == o2.passed_expectation
    )
    consistency_rate = matches / round1.total
    assert consistency_rate == 1.0
