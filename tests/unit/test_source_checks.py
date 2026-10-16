import sys
from pathlib import Path

from aitest.application.execution.source_checks import (
    SourceCheckRequest,
    SourceModuleFact,
    SourceProbeObservation,
    SourceSyntaxErrorFact,
    SourceVerificationService,
)
from aitest.domain.execution.runs import FailureClass, PlanRevisionRef
from aitest.domain.execution.sources import (
    SourceCheckType,
    SourceVerificationState,
)

_FILE_DIGEST = "d" * 64


def _request(materialized_root: Path) -> SourceCheckRequest:
    return SourceCheckRequest(
        verification_id="verify-1",
        check_result_id="check-1",
        project_id="project-1",
        attempt_id="attempt-1",
        plan_revision_ref=PlanRevisionRef(
            revision_id="plan-1",
            revision_no=1,
            digest="sha256:plan-1",
        ),
        expected_source_content_digest="sha256:source-1",
        materialized_snapshot_ref=str(materialized_root),
        check_type=SourceCheckType.LOAD,
        scope="module",
        source_snapshot_ref="snapshot-1",
        environment_ref="environment-1",
        rules_revision="rules-1",
        adapter_version="python-checks/1.0",
        observed_source_digest="sha256:source-1",
    )


def test_source_verification_accepts_only_bytes_inside_materialized_root(
    tmp_path: Path,
) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    entry = materialized / "module.py"
    entry.write_text("value = 1\n", encoding="utf-8")
    service = SourceVerificationService()

    verification = service.verify(
        _request(materialized),
        SourceProbeObservation(
            observed_source_digest="sha256:source-1",
            observed_entry_ref=str(entry),
            observed_import_ref=str(entry),
            observed_interpreter_ref=sys.executable,
        ),
    )

    assert verification.state is SourceVerificationState.VERIFIED
    assert verification.gap_ids == ()


def test_source_verification_rejects_original_repository_load(
    tmp_path: Path,
) -> None:
    materialized = tmp_path / "materialized"
    original = tmp_path / "original"
    materialized.mkdir()
    original.mkdir()
    original_entry = original / "module.py"
    original_entry.write_text("value = 1\n", encoding="utf-8")

    verification = SourceVerificationService().verify(
        _request(materialized),
        SourceProbeObservation(
            observed_source_digest="sha256:source-1",
            observed_entry_ref=str(original_entry),
            observed_import_ref=str(original_entry),
        ),
    )

    assert verification.state is SourceVerificationState.MISMATCH
    assert "entry_outside_materialized_source" in verification.gap_ids
    assert "import_outside_materialized_source" in verification.gap_ids


def test_source_check_preserves_environment_blocked_fact(tmp_path: Path) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    outcome = SourceVerificationService().check(
        _request(materialized),
        SourceProbeObservation(
            observed_source_digest="sha256:source-1",
            failure_class=FailureClass.ENVIRONMENT_UNREACHABLE,
        ),
    )

    assert outcome.verification.state is SourceVerificationState.BLOCKED
    assert outcome.check_result.failure_class is FailureClass.ENVIRONMENT_UNREACHABLE


# ------------------------------------------------------ C-16：语法与逐模块
# 反例来自 docs/validation/p1-source-completion-20261012/source-provider-counterexamples.py：
# 语法错误、导入失败的多模块源码此前仍被判 verified 且无缺口。


def _verified_observation(materialized: Path, **overrides: object) -> SourceProbeObservation:
    entry = materialized / "entry.py"
    entry.write_text("value = 1\n", encoding="utf-8")
    values: dict[str, object] = {
        "observed_source_digest": "sha256:source-1",
        "observed_entry_ref": str(entry),
        "observed_import_ref": str(entry),
        "observed_interpreter_ref": sys.executable,
    }
    values.update(overrides)
    return SourceProbeObservation(**values)  # type: ignore[arg-type]


def test_syntax_errors_are_a_mismatch_not_a_verified_source(tmp_path: Path) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    broken = materialized / "entry.py"
    broken.write_text("def broken(:\n", encoding="utf-8")

    verification = SourceVerificationService().verify(
        _request(materialized),
        SourceProbeObservation(
            observed_source_digest="sha256:source-1",
            observed_entry_ref=str(broken),
            observed_interpreter_ref=sys.executable,
            observed_python_file_count=1,
            observed_syntax_errors=(
                SourceSyntaxErrorFact("entry.py", "SyntaxError: invalid syntax (line 1)"),
            ),
        ),
    )

    assert verification.state is SourceVerificationState.MISMATCH
    assert "source_syntax_error:entry.py" in verification.gap_ids


def test_every_registered_module_is_consumed_not_just_the_first(
    tmp_path: Path,
) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    good = materialized / "good"
    good.mkdir()
    (good / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    bad = materialized / "bad"
    bad.mkdir()
    (bad / "__init__.py").write_text("def broken(:\n", encoding="utf-8")

    verification = SourceVerificationService().verify(
        _request(materialized),
        _verified_observation(
            materialized,
            observed_import_ref=str(good / "__init__.py"),
            observed_modules=(
                SourceModuleFact(
                    module="good",
                    expected_digest=_FILE_DIGEST,
                    loaded_file=str(good / "__init__.py"),
                    observed_digest=_FILE_DIGEST,
                ),
                SourceModuleFact(
                    module="bad",
                    expected_digest=_FILE_DIGEST,
                    loaded_file=None,
                    observed_digest=None,
                    load_error="SyntaxError: invalid syntax",
                ),
            ),
        ),
    )

    # 第二个模块失败必须留下缺口：只取第一个非空 file 的实现会漏掉它。
    assert verification.state is SourceVerificationState.UNVERIFIED
    assert any(gap.startswith("module[bad]") for gap in verification.gap_ids)


def test_module_loaded_outside_the_materialized_source_is_a_mismatch(
    tmp_path: Path,
) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    outside = tmp_path / "editable" / "pkg"
    outside.mkdir(parents=True)
    (outside / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    verification = SourceVerificationService().verify(
        _request(materialized),
        _verified_observation(
            materialized,
            observed_modules=(
                SourceModuleFact(
                    module="pkg",
                    expected_digest=_FILE_DIGEST,
                    loaded_file=str(outside / "__init__.py"),
                    observed_digest=_FILE_DIGEST,
                ),
            ),
        ),
    )

    assert verification.state is SourceVerificationState.MISMATCH
    assert any("outside_materialized_source" in gap for gap in verification.gap_ids)


def test_module_digest_mismatch_is_a_mismatch(tmp_path: Path) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    package = materialized / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    verification = SourceVerificationService().verify(
        _request(materialized),
        _verified_observation(
            materialized,
            observed_modules=(
                SourceModuleFact(
                    module="pkg",
                    expected_digest=_FILE_DIGEST,
                    loaded_file=str(package / "__init__.py"),
                    observed_digest="e" * 64,
                ),
            ),
        ),
    )

    assert verification.state is SourceVerificationState.MISMATCH
    assert any("digest_mismatch" in gap for gap in verification.gap_ids)


def test_namespace_package_without_a_file_is_not_a_gap(tmp_path: Path) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    namespace = materialized / "pkg"
    namespace.mkdir()
    (namespace / "child.py").write_text("VALUE = 1\n", encoding="utf-8")

    verification = SourceVerificationService().verify(
        _request(materialized),
        _verified_observation(
            materialized,
            observed_modules=(
                SourceModuleFact(
                    module="pkg",
                    expected_digest=None,
                    loaded_file=None,
                    observed_digest=None,
                ),
            ),
        ),
    )

    assert verification.state is SourceVerificationState.VERIFIED
    assert verification.gap_ids == ()


def test_probe_failure_never_yields_verified(tmp_path: Path) -> None:
    materialized = tmp_path / "materialized"
    materialized.mkdir()

    verification = SourceVerificationService().verify(
        _request(materialized),
        _verified_observation(
            materialized,
            probe_error="探针进程退出码 1: boom",
        ),
    )

    assert verification.state is SourceVerificationState.BLOCKED
    assert verification.gap_ids == ("environment_unreachable",)
