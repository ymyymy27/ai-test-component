"""修复后行为取证（C-15 未覆盖部分 + C-16 + D-10）：把已复现反例变成真实拒绝。

与 `docs/validation/p1-source-completion-20261012/source-provider-counterexamples.py` 的关系：
那份脚本断言的是**修复前**的行为（默认链必须报 SnapshotError、语法错误仍 verified），
因此修复后它会失败——这是预期，不是新缺陷；本脚本断言修复后的正确行为。

范围与边界（如实登记，不冒充真实验收）：
- D-10 与 C-16 规则层用纯输入事实 + 真实临时文件；
- C-15 的真实默认链（真实核心/UOW/固定字节/物化/默认解析器与 provider）由
  `tests/unit/test_default_start_source_chain.py` 覆盖，本脚本只登记其入口与结论，
  不重复声称。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from aitest.application.execution.source_checks import (  # noqa: E402
    SourceCheckRequest,
    SourceModuleFact,
    SourceProbeObservation,
    SourceSyntaxErrorFact,
    SourceVerificationService,
)
from aitest.application.review.execution_decision_adapter import (  # noqa: E402
    AssertionOutcome,
    AssertionOutcomeFact,
    DecisionContextFacts,
    build_decision_facts,
)
from aitest.contracts.execution_facts import ExecutionFacts  # noqa: E402
from aitest.domain.execution.runs import FailureClass, PlanRevisionRef  # noqa: E402
from aitest.domain.execution.sources import SourceCheckType  # noqa: E402
from aitest.domain.review.reports import evaluate_review  # noqa: E402

FIXTURE = ROOT / "docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures/success.json"


def d10() -> list[dict[str, object]]:
    """D-10：三组已复现的过度接受，现在必须是拒绝/降级。"""

    original = json.loads(FIXTURE.read_text(encoding="utf-8"))
    outcome = AssertionOutcomeFact(
        case_id="case-1",
        step_id="step-1",
        attempt_id="attempt-1",
        assertion_ref="assertion-1",
        basis_revision=1,
        outcome=AssertionOutcome.PASSED,
        evidence_refs=("evidence-1",),
        case_assertions_complete=True,
        necessary_assertion=True,
        basis_confirmed=True,
        verification_valid=True,
        dependencies_valid=True,
    )
    context = DecisionContextFacts(template_required_case_ids=frozenset({"case-1"}))
    results: list[dict[str, object]] = []
    for label in (
        "control",
        "required_evidence_conflict",
        "source_unverified",
        "attempt_not_current",
    ):
        raw = copy.deepcopy(original)
        if label == "required_evidence_conflict":
            raw["steps"][0]["evidence_requirement_ids"] = ["required-stderr"]
            raw["evidence_refs"][1]["integrity"] = "conflict"
        elif label == "source_unverified":
            extra = copy.deepcopy(raw["source_verifications"][0])
            extra["verification_id"] = "source-verify-unverified"
            extra["state"] = "unverified"
            raw["source_verifications"].append(extra)
        elif label == "attempt_not_current":
            raw["attempts"][0]["is_current"] = False
        decision_facts = build_decision_facts(
            ExecutionFacts.model_validate(raw),
            assertion_outcomes=(outcome,),
            context=context,
        )
        decision = evaluate_review(decision_facts)
        results.append(
            {
                "case": label,
                "executed": sorted(decision_facts.coverage.executed),
                "verified": sorted(decision_facts.coverage.verified),
                "required_evidence_valid": decision_facts.required_evidence_valid,
                "source_identity_state": decision_facts.source_identity_state.value,
                "business_outcome": decision.business_outcome.value,
                "evidence_grade": (
                    decision.evidence_grade.value if decision.evidence_grade else None
                ),
            }
        )
    assert results[0]["business_outcome"] == "passed", results[0]
    assert results[0]["evidence_grade"] == "A", results[0]
    for item in results[1:]:
        assert item["business_outcome"] != "passed", item
        assert item["evidence_grade"] != "A", item
    assert results[1]["required_evidence_valid"] is False, results[1]
    assert results[2]["source_identity_state"] == "unverified", results[2]
    assert results[3]["executed"] == [], results[3]
    return results


def _request(workdir: Path) -> SourceCheckRequest:
    return SourceCheckRequest(
        verification_id="verify-audit",
        check_result_id="check-audit",
        project_id="project-audit",
        attempt_id="attempt-audit",
        plan_revision_ref=PlanRevisionRef(
            revision_id="plan-audit", revision_no=1, digest="sha256:plan-audit"
        ),
        expected_source_content_digest="sha256:source-audit",
        materialized_snapshot_ref=str(workdir),
        check_type=SourceCheckType.LOAD,
        scope=str(workdir),
        source_snapshot_ref="snap-audit",
        environment_ref="environment-audit@1",
        rules_revision="rules-audit@1",
        adapter_version="python-checks/1.0",
        observed_source_digest="sha256:source-audit",
    )


def c16(fixture_root: Path) -> dict[str, object]:
    """C-16：语法错误与逐模块失败不再是 verified。"""

    service = SourceVerificationService()
    workdir = fixture_root / "materialized"
    workdir.mkdir(parents=True, exist_ok=True)
    entry = workdir / "entry.py"
    entry.write_text("def broken(:\n", encoding="utf-8")

    syntax = service.verify(
        _request(workdir),
        SourceProbeObservation(
            observed_source_digest="sha256:source-audit",
            observed_entry_ref=str(entry),
            observed_interpreter_ref=sys.executable,
            observed_python_file_count=1,
            observed_syntax_errors=(
                SourceSyntaxErrorFact("entry.py", "SyntaxError: invalid syntax (line 1)"),
            ),
        ),
    )
    syntax_outcome = service.check(
        _request(workdir),
        SourceProbeObservation(
            observed_source_digest="sha256:source-audit",
            observed_entry_ref=str(entry),
            observed_interpreter_ref=sys.executable,
            observed_python_file_count=1,
            observed_syntax_errors=(
                SourceSyntaxErrorFact("entry.py", "SyntaxError: invalid syntax (line 1)"),
            ),
        ),
    )

    good = workdir / "good"
    good.mkdir(exist_ok=True)
    (good / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    good_file = str((good / "__init__.py").resolve())
    modules = service.verify(
        _request(workdir),
        SourceProbeObservation(
            observed_source_digest="sha256:source-audit",
            observed_entry_ref=str(entry),
            observed_import_ref=good_file,
            observed_interpreter_ref=sys.executable,
            observed_modules=(
                SourceModuleFact(
                    module="good",
                    expected_digest="a" * 64,
                    loaded_file=good_file,
                    observed_digest="a" * 64,
                ),
                SourceModuleFact(
                    module="bad",
                    expected_digest="b" * 64,
                    loaded_file=None,
                    observed_digest=None,
                    load_error="SyntaxError: invalid syntax",
                ),
            ),
        ),
    )

    probe = service.verify(
        _request(workdir),
        SourceProbeObservation(
            observed_source_digest="sha256:source-audit",
            observed_entry_ref=str(entry),
            observed_interpreter_ref=sys.executable,
            probe_error="探针进程退出码 1: boom",
        ),
    )

    assert syntax.state.value == "mismatch", syntax.state
    assert "source_syntax_error:entry.py" in syntax.gap_ids, syntax.gap_ids
    assert modules.state.value == "unverified", modules.state
    assert any(gap.startswith("module[bad]") for gap in modules.gap_ids), modules.gap_ids
    assert probe.state.value == "blocked", probe.state
    assert probe.gap_ids == ("environment_unreachable",), probe.gap_ids
    assert syntax_outcome.check_result.failure_class is FailureClass.SOURCE_ERROR
    return {
        "syntax_state": syntax.state.value,
        "syntax_gaps": list(syntax.gap_ids),
        "syntax_failure_class": syntax_outcome.check_result.failure_class.value,
        "module_state": modules.state.value,
        "module_gaps": list(modules.gap_ids),
        "probe_state": probe.state.value,
        "probe_gaps": list(probe.gap_ids),
    }


def main() -> None:
    fixture_root = ROOT / ".runtime" / "fix-verification"
    fixture_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "scope": (
            "post-fix behavior: D-10 pure adapter inputs, C-16 rule-level facts with real "
            "files; C-15 real default chain is covered by "
            "tests/unit/test_default_start_source_chain.py and is not re-claimed here"
        ),
        "d10": d10(),
        "c16": c16(fixture_root),
    }
    target = Path(__file__).with_name("fixed-behaviors.json")
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(target), "d10_cases": len(payload["d10"])}, ensure_ascii=True))


if __name__ == "__main__":
    main()
