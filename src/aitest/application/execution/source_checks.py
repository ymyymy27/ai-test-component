"""实际物化源码的入口、导入与来源身份核验。

本模块只记录执行侧实际观察到的来源事实，不复制 B 包的源码快照规则。
调用方提供 B 冻结的期望来源摘要，以及执行探针观察到的解释器/入口/导入
结果；这里负责形成可持久化的 ``ExecutionSourceVerification`` 和
``SourceCheckResult``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from aitest.domain.execution.runs import FailureClass, PlanRevisionRef
from aitest.domain.execution.sources import (
    ExecutionSourceVerification,
    SourceCheckResult,
    SourceCheckType,
    SourceVerificationState,
)


@dataclass(frozen=True, slots=True)
class SourceCheckRequest:
    """一次源码检查的冻结输入；规则来自 B，实际观察来自执行探针。"""

    verification_id: str
    check_result_id: str
    project_id: str
    attempt_id: str
    plan_revision_ref: PlanRevisionRef
    expected_source_content_digest: str
    materialized_snapshot_ref: str
    check_type: SourceCheckType
    scope: str
    source_snapshot_ref: str
    environment_ref: str
    rules_revision: str
    adapter_version: str
    observed_source_digest: str
    observed_entry_ref: str | None = None
    observed_import_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    raw_output_evidence_ref: str | None = None
    observed_at: datetime | None = None
    expected_interpreter_ref: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "verification_id",
            "check_result_id",
            "project_id",
            "attempt_id",
            "expected_source_content_digest",
            "materialized_snapshot_ref",
            "scope",
            "source_snapshot_ref",
            "environment_ref",
            "rules_revision",
            "adapter_version",
            "observed_source_digest",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class SourceSyntaxErrorFact:
    """物化源码中一个文件在**登记解释器**下的实际语法错误（C-16）。"""

    relative_path: str
    reason: str

    def __post_init__(self) -> None:
        if not self.relative_path.strip() or not self.reason.strip():
            raise ValueError("a syntax error fact requires its path and reason")


@dataclass(frozen=True, slots=True)
class SourceModuleFact:
    """一个**登记模块**的实际加载事实与冻结期望（C-16）。

    `expected_digest` 为 `None` 表示冻结清单里该模块没有文件型来源（PEP 420 命名空间包）。
    """

    module: str
    expected_digest: str | None
    loaded_file: str | None
    observed_digest: str | None
    load_error: str | None = None

    def __post_init__(self) -> None:
        if not self.module.strip():
            raise ValueError("a module fact requires its registered name")


@dataclass(frozen=True, slots=True)
class SourceProbeObservation:
    """执行探针返回的原始观察；不在此层做通过/失败结论。"""

    observed_source_digest: str
    observed_entry_ref: str | None = None
    observed_import_ref: str | None = None
    observed_interpreter_ref: str | None = None
    failure_class: FailureClass | None = None
    evidence_refs: tuple[str, ...] = ()
    raw_output_evidence_ref: str | None = None
    observed_at: datetime | None = None
    #: 物化范围内实际编译过的文件数与逐文件语法错误（全部文件，不只是第一个）。
    observed_python_file_count: int = 0
    observed_syntax_errors: tuple[SourceSyntaxErrorFact, ...] = ()
    #: 逐登记模块的实际加载事实（全部模块，不只是第一个非空文件）。
    observed_modules: tuple[SourceModuleFact, ...] = ()
    #: 探针自身失败事实；非空即取证缺口，不得给出 verified。
    probe_error: str | None = None


@dataclass(frozen=True, slots=True)
class SourceCheckOutcome:
    verification: ExecutionSourceVerification
    check_result: SourceCheckResult


class SourceProbePort(Protocol):
    def observe(
        self,
        *,
        materialized_snapshot_ref: str,
        entry_ref: str | None = None,
        import_ref: str | None = None,
    ) -> SourceProbeObservation: ...


class SourceVerificationService:
    """把探针事实与 B 的期望摘要合成为源码核验事实。"""

    def verify(
        self,
        request: SourceCheckRequest,
        observation: SourceProbeObservation,
    ) -> ExecutionSourceVerification:
        state, gaps = _derive_state(request, observation)
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    *observation.evidence_refs,
                    *(
                        (observation.raw_output_evidence_ref,)
                        if observation.raw_output_evidence_ref is not None
                        else ()
                    ),
                )
            )
        )
        return ExecutionSourceVerification(
            verification_id=request.verification_id,
            project_id=request.project_id,
            plan_revision_ref=request.plan_revision_ref,
            expected_source_content_digest=request.expected_source_content_digest,
            materialized_snapshot_ref=request.materialized_snapshot_ref,
            observed_source_digest=observation.observed_source_digest,
            state=state,
            observed_entry_ref=observation.observed_entry_ref,
            observed_import_ref=observation.observed_import_ref,
            observed_interpreter_ref=observation.observed_interpreter_ref,
            failure_class=observation.failure_class,
            gap_ids=gaps,
            evidence_refs=evidence_refs,
            verified_at=observation.observed_at or request.observed_at or datetime.now(UTC),
        )

    def check(
        self,
        request: SourceCheckRequest,
        observation: SourceProbeObservation,
    ) -> SourceCheckOutcome:
        verification = self.verify(request, observation)
        failure_class = _check_failure_class(verification, observation)
        return SourceCheckOutcome(
            verification=verification,
            check_result=SourceCheckResult(
                check_result_id=request.check_result_id,
                attempt_id=request.attempt_id,
                check_type=request.check_type,
                scope=request.scope,
                source_snapshot_ref=request.source_snapshot_ref,
                environment_ref=request.environment_ref,
                rules_revision=request.rules_revision,
                adapter_version=request.adapter_version,
                failure_class=failure_class,
                entry_ref=observation.observed_entry_ref,
                argument_refs=(),
                raw_output_evidence_ref=observation.raw_output_evidence_ref,
                evidence_refs=verification.evidence_refs,
            ),
        )


def _derive_state(
    request: SourceCheckRequest,
    observation: SourceProbeObservation,
) -> tuple[SourceVerificationState, tuple[str, ...]]:
    if observation.probe_error is not None or observation.failure_class in {
        FailureClass.DEPENDENCY_MISSING,
        FailureClass.ENVIRONMENT_UNREACHABLE,
    }:
        return SourceVerificationState.BLOCKED, ("environment_unreachable",)
    if observation.failure_class not in {
        None, FailureClass.PASSED, FailureClass.SOURCE_ERROR,
    }:
        return SourceVerificationState.UNKNOWN, ("tool_failure",)
    if not observation.observed_source_digest.strip():
        return SourceVerificationState.UNVERIFIED, ("source_digest_missing",)

    # C-16：语法必须**实际编译**过物化范围内的源码；有语法错误时不得给出 verified。
    # 只核摘要/文件存在就判通过，会把"字节固定但代码不可用"当成来源已核实。
    if observation.observed_syntax_errors:
        return SourceVerificationState.MISMATCH, tuple(
            f"source_syntax_error:{item.relative_path}"
            for item in observation.observed_syntax_errors
        )

    # C-16：逐**全部登记模块**核对实际加载来源/摘要/失败分类，不再只取第一个非空文件。
    module_mismatch, module_unverified = _module_gaps(request, observation)

    gaps: list[str] = []
    if (
        request.expected_interpreter_ref and observation.observed_interpreter_ref
        and Path(request.expected_interpreter_ref).resolve()
        != Path(observation.observed_interpreter_ref).resolve()
    ):
        return SourceVerificationState.MISMATCH, ("actual_interpreter_mismatch",)
    if _outside_materialized(
        observation.observed_entry_ref,
        request.materialized_snapshot_ref,
    ):
        gaps.append("entry_outside_materialized_source")
    if _outside_materialized(
        observation.observed_import_ref,
        request.materialized_snapshot_ref,
    ):
        gaps.append("import_outside_materialized_source")
    if gaps or module_mismatch:
        return SourceVerificationState.MISMATCH, tuple(gaps) + module_mismatch
    if observation.observed_source_digest != request.expected_source_content_digest:
        return SourceVerificationState.MISMATCH, ("source_digest_mismatch",)
    base = Path(request.materialized_snapshot_ref)
    if not base.is_absolute() or not base.is_dir():
        gaps.append("materialized_source_unverified")
    for name, value in (
        ("entry", observation.observed_entry_ref),
        ("load", observation.observed_import_ref),
    ):
        if value and (not Path(value).is_absolute() or not Path(value).is_file()):
            gaps.append(f"actual_{name}_path_unverified")
    if not observation.observed_entry_ref:
        gaps.append("actual_entry_missing")
    if not observation.observed_interpreter_ref:
        gaps.append("actual_interpreter_missing")
    elif (
        not Path(observation.observed_interpreter_ref).is_absolute()
        or not Path(observation.observed_interpreter_ref).is_file()
    ):
        gaps.append("actual_interpreter_path_unverified")
    if (
        request.check_type in {SourceCheckType.LOAD, SourceCheckType.MINIMAL_START}
        and not observation.observed_import_ref
    ):
        gaps.append("actual_load_source_missing")
    if gaps or module_unverified:
        return SourceVerificationState.UNVERIFIED, tuple(gaps) + module_unverified
    return SourceVerificationState.VERIFIED, ()


def _module_gaps(
    request: SourceCheckRequest,
    observation: SourceProbeObservation,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """逐登记模块判定，返回 `(mismatch 缺口, unverified 缺口)`（C-16）。

    - 导入失败/取不到文件：无法证明字节身份 → `unverified`（保留缺口，不冒充通过）；
    - 冻结清单里该模块**没有**文件型来源（命名空间包）：导入结果无文件且无错误即符合期望；
      若反而加载出一个文件，就是来源与冻结清单不符 → `mismatch`；
    - 冻结清单里该模块**有**文件：实际加载文件必须在固定 workdir 内，且摘要逐字相符；
      越界（外部同名包覆盖、editable 指回原仓库）或摘要不符 → `mismatch`。
    """

    mismatch: list[str] = []
    unverified: list[str] = []
    for item in observation.observed_modules:
        prefix = f"module[{item.module}]"
        if item.load_error is not None:
            unverified.append(f"{prefix} load_error={item.load_error}")
            continue
        if item.loaded_file is None:
            if item.expected_digest is None and item.observed_digest is None:
                continue
            unverified.append(f"{prefix} load_source_missing")
            continue
        if item.expected_digest is None:
            mismatch.append(f"{prefix} unexpected_file_source")
            continue
        if _outside_materialized(item.loaded_file, request.materialized_snapshot_ref):
            mismatch.append(f"{prefix} outside_materialized_source")
            continue
        if item.observed_digest != item.expected_digest:
            mismatch.append(f"{prefix} digest_mismatch")
    return tuple(mismatch), tuple(unverified)


def _check_failure_class(
    verification: ExecutionSourceVerification,
    observation: SourceProbeObservation,
) -> FailureClass:
    if observation.failure_class not in {None, FailureClass.PASSED}:
        assert observation.failure_class is not None
        return observation.failure_class
    if verification.state is SourceVerificationState.VERIFIED:
        return FailureClass.PASSED
    if verification.state in {
        SourceVerificationState.BLOCKED,
        SourceVerificationState.UNVERIFIED,
    }:
        return FailureClass.ENVIRONMENT_UNREACHABLE
    if verification.state is SourceVerificationState.UNKNOWN:
        return FailureClass.TOOL_FAILURE
    return FailureClass.SOURCE_ERROR


def _outside_materialized(reference: str | None, materialized_ref: str) -> bool:
    if reference is None or not reference.strip():
        return False
    base = Path(materialized_ref)
    if not base.is_absolute() and not base.exists():
        return False
    target = Path(reference)
    if not target.is_absolute():
        return False
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return True
    return False


__all__ = [
    "SourceCheckOutcome",
    "SourceCheckRequest",
    "SourceModuleFact",
    "SourceProbeObservation",
    "SourceProbePort",
    "SourceSyntaxErrorFact",
    "SourceVerificationService",
]
