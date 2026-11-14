"""来源核验装配：把已保存来源、入口映射与检查事实合成为执行事实。

原先定义在 `bootstrap.py` 的 2657 行装配文件里；这些函数只依赖应用/领域类型，
不依赖 `CoreAssembly`，拆出后 bootstrap 继续 re-export 同名符号。
"""

from __future__ import annotations

import hashlib
import json
import subprocess as subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from aitest.application.execution.source_checks import (
    SourceCheckRequest,
    SourceCommandRequest,
    SourceModuleFact,
    SourceProbeObservation,
    SourceSyntaxErrorFact,
    SourceVerificationService,
    merge_source_checks,
)
from aitest.application.execution.start_source_binding import SourceBindingUnverified
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import (
    SourceSnapshotPort,
)
from aitest.application.project.persistence import load_project
from aitest.application.project.serialization import source_manifest_from_payload
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.contracts.prepared_run import (
    EnvironmentResolutionFact,
    SourceCheckApplicability,
    SourceCheckPlan,
    SourceCheckPlans,
)
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.execution.runs import FailureClass, PlanRevisionRef
from aitest.domain.execution.runtime_source import RuntimeSourceFile, RuntimeSourcePlan
from aitest.domain.execution.sources import (
    ExecutionSourceVerification,
    SourceCheckResult,
    SourceCheckStatus,
    SourceCheckStatusState,
    SourceCheckType,
    SourceVerificationState,
)
from aitest.domain.project.context import SourceFileDigest, source_content_identity
from aitest.infrastructure.adapters.execution.python_checks import (
    LoadSourceFact,
    PythonLoadSourceProbe,
    PythonSourceProbeError,
)
from aitest.infrastructure.adapters.execution.runtime_source import carrier_digest
from aitest.infrastructure.adapters.execution.source_check_commands import (
    ControlledSourceCheckRunner,
)
from aitest.infrastructure.environment_configuration import (
    carrier_configuration as carrier_configuration,
)
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.host_lifetime import cli_host_pid as cli_host_pid
from aitest.infrastructure.model_configuration import (
    validate_model_configuration as validate_model_configuration,
)


def _interpreter_verifier() -> Callable[[str, EnvironmentResolutionFact], str]:
    """装配层适配：把基础设施探针包成应用层可调用的「现场观测解释器」回调。

    应用层不得导入 ``infrastructure``（依赖方向），故探针只能在此注入。探针以**冻结环境事实**
    的解释器路径启动，交回该解释器**自报的 realpath**（``LoadSourceFact.executable``）——
    这是独立观测，不是把配置值回读一遍。比较口径由应用层按
    ``source_checks._derive_state``（解析后路径一致）执行。

    空模块清单是刻意的：解释器身份核对不需要项目模块；模块加载来源核对属另一项，尚未接入。
    """

    def verify(workdir: str, environment: EnvironmentResolutionFact) -> str:
        fact = PythonLoadSourceProbe(executable=environment.executable_path).probe(
            materialized_root=workdir,
            modules=(),
        )
        return fact.executable

    return verify

def _module_names_from_project(
    reader: RecordReader, project_id: str, project_revision: object, workdir: str
) -> tuple[str, ...]:
    """从项目登记推导**可导入模块名**（落地方式见交接说明 §6.2 第 1 点）。

    合同的 `Module.source_paths` 是「实际变化的文件 → 模块」的映射模式（示例：
    `src/ticket/**`、`src/ticket`、`tests/ticket`），**不是**模块名，也没有单独的「导入根」字段。
    因此这里不新增字段、也不猜布局，而是**按探针自己的 sys.path 推导**：探针以物化根作为唯一
    PYTHONPATH（见 `PythonLoadSourceProbe`），所以把 `source_paths` 去掉尾部通配后得到的相对目录，
    其在物化根下的点号名就是可导入名（PEP 420 命名空间包同样成立）。

    三条好处：① 推导出的名字**必然可被探针导入**，不会因此假阻塞；② 探针报告的实际加载路径
    若落在物化根之外（例如解析到 editable 安装或原仓库），`_derive_state` 会判
    `import_outside_materialized_source` 而阻塞；③ 推导不出的模块**不参与探测**，不伪造结论。

    只在物化根内**确实存在**且含 `.py` 的目录才计入，否则视为不可核对。
    """

    if type(project_revision) is not int or project_revision < 1:
        raise SourceBindingUnverified("frozen preparation has no exact project revision")
    project = load_project(reader, project_id=project_id, revision=project_revision)
    root = Path(workdir).resolve()
    names: set[str] = set()
    for module in project.modules:
        for pattern in module.source_paths:
            relative = _pattern_relative_dir(pattern)
            if relative is None:
                continue
            candidate = root / relative
            if not candidate.is_dir() or not any(candidate.rglob("*.py")):
                continue
            names.add(".".join(relative.parts))
    return tuple(sorted(names))

def _pattern_relative_dir(pattern: object) -> Path | None:
    """把 `source_paths` 的一个模式折成相对目录；含通配或上跳时返回 `None`。"""

    if not isinstance(pattern, str) or not pattern.strip():
        return None
    raw = pattern.strip().replace("\\", "/").rstrip("/")
    # 绝对路径不是「项目内的相对目录」，直接拒绝——不能靠后续 split 把前导分隔符吃掉。
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        return None
    while raw.endswith(("/**", "/*")):
        raw = raw[: raw.rfind("/")]
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." or "*" in part or "?" in part for part in parts):
        return None
    return Path(*parts)

def _required_source_analysis(dependencies: BUseCaseDependencies) -> SourceAnalysisService:
    """默认 start 来源链要求的 B 来源分析能力；缺失即显式拒绝，不静默降级。"""

    source_analysis = dependencies.source_analysis
    if source_analysis is None:
        raise SourceBindingUnverified("the source analysis capability is not configured")
    return source_analysis

def _technical_identity(saved: Mapping[str, object]) -> str:
    """已核对的业务快照记录 → 技术固定清单身份（键缺失/空值即拒绝）。"""

    technical = saved.get("pinned_snapshot_id")
    if not isinstance(technical, str) or not technical.strip():
        raise SourceBindingUnverified(
            "the saved business snapshot has no technical pinned identity"
        )
    return technical

def _frozen_step_timeout(
    reader: RecordReader, project_id: str, environment_id: str, revision: int
) -> int | None:
    """权威环境记录里声明的步骤截止（秒）。

    与 `ExecutionAuthorizationService._frozen_step_deadline_ms` 同一权威来源：冻结准备记录的
    `EnvironmentRefFact` 投影不带该字段，只读投影会让解析器算出 `None`、授权侧算出真实毫秒数，
    默认链因此在"未冻结的截止"处阻塞。声明缺失返回 `None`（历史事实，不是新的执行依据）。
    """

    from aitest.application.planning.substrate import read_scoped_record

    record = read_scoped_record(
        reader,
        project_id=project_id,
        aggregate_kind="environment",
        record_id=environment_id,
        revision=revision,
    )
    declared = record.payload.get("step_timeout_seconds")
    if declared is None:
        return None
    if type(declared) is not int or declared <= 0:
        raise ValueError("the frozen environment step timeout cannot be verified")
    return declared

def _pinned_snapshot_identity(
    source_analysis: SourceAnalysisService,
    project_id: str,
    source_snapshot_id: str,
    record_revision: int,
) -> str:
    """业务 `SourceSnapshot` 记录 → **技术固定清单身份**（C-15 的权威映射）。

    依据 AB-001 1.21/1.22 与 BC-001 第 6 节：B 保存不可变**业务**快照（`snapshot_id` 带
    项目/绑定修订命名空间）、`content_identity` 与 `pinned_snapshot_id`；物化端口只认技术
    `snap-*`。`SourceAnalysisService.verify_saved` 会按准确修订读回业务记录、重新核对固定
    blob 与业务清单逐项一致，再由它交回技术身份——既不做"业务 ID 当前缀"的猜测，也不跳过
    固定字节复核。
    """

    payload = source_analysis.verify_saved(
        project_id=project_id, snapshot_id=source_snapshot_id, revision=record_revision
    )
    return _technical_identity(payload)

def _pinned_file_digests(pinned: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    """技术固定清单的逐文件身份（`verify_pinned` 已重新核对 blob 后才交回）。"""

    raw = pinned.get("files")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SourceBindingUnverified("the pinned source has no frozen file manifest")
    entries: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise SourceBindingUnverified("a pinned manifest entry is unreadable")
        name, digest, size = item.get("relative_path"), item.get("sha256"), item.get("size")
        if not isinstance(name, str) or not name.strip() or type(size) is not int:
            raise SourceBindingUnverified("a pinned manifest entry lacks its exact identity")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise SourceBindingUnverified("a pinned manifest entry digest is invalid")
        entries.append(
            {
                "relative_path": name,
                "size": size,
                "content_digest": "sha256:" + digest,
            }
        )
    return tuple(entries)

def _observed_content_identity(saved: Mapping[str, object], pinned: Mapping[str, object]) -> str:
    """用**重新核对过的固定字节**重算业务内容身份（不回声冻结值）。

    只核摘要或只比"记录里写的身份"都是空转：这里把技术清单的实际逐文件摘要放回 B 冻结的
    形态身份（git 基准提交/差异摘要或 plain 清单摘要）与范围声明，按 `source_content_identity`
    唯一口径重算。重算值与冻结 `content_identity` 不符即 `source_digest_mismatch`。
    """

    try:
        manifest = source_manifest_from_payload(saved)
    except ValueError as error:
        raise SourceBindingUnverified(f"saved source manifest is unreadable: {error}") from error
    files = _pinned_file_digests(pinned)
    try:
        observed = replace(
            manifest,
            files=tuple(
                SourceFileDigest(
                    relative_path=str(item["relative_path"]),
                    size=cast(int, item["size"]),
                    content_digest=str(item["content_digest"]),
                )
                for item in files
            ),
        )
    except ValueError as error:
        raise SourceBindingUnverified(f"pinned file identity is invalid: {error}") from error
    return source_content_identity(observed)

def _module_expectations(
    reader: RecordReader,
    project_id: str,
    project_revision: object,
    workdir: str,
    pinned: Mapping[str, object],
) -> tuple[tuple[str, ...], dict[str, str]]:
    """登记模块 → 冻结期望摘要（只对冻结清单里**确有文件型来源**的模块给期望）。

    `_module_names_from_project` 已保证推导出的名字都存在于物化根内；这里再把每个名字映射到
    冻结清单中的实际文件（常规包取 `__init__.py`）。命名空间包在清单里没有文件，交由
    `evaluate_source_binding` 之外的观测事实按"无文件且无错误"处理。
    """

    names = _module_names_from_project(reader, project_id, project_revision, workdir)
    digests = {
        str(item["relative_path"]): str(item["content_digest"]).removeprefix("sha256:")
        for item in _pinned_file_digests(pinned)
    }
    expected: dict[str, str] = {}
    for name in names:
        relative = name.replace(".", "/")
        digest = digests.get(f"{relative}/__init__.py")
        if digest is not None:
            expected[name] = digest
    return names, expected

def _probe_output_reference(
    objects: FileObjectStore | None, project_id: str, fact: object
) -> StoredObjectRef | None:
    """永久保存安全探针捕获流，返回包含摘要、大小和路径的完整对象引用。

    关闭条件要求"永久原始输出"：逐模块/语法结论必须能回到原始事实，而不是只有汇总状态。
    捕获流含协议包裹和模块 stdout，使用 text/plain；对象不可变且可按摘要回读。
    """

    raw = getattr(fact, "raw_output", b"")
    if objects is None or not isinstance(raw, bytes) or not raw:
        return None
    stored = objects.publish_bytes(project_id, raw, media_type="text/plain; charset=utf-8")
    return stored

def _registered_source_check_statuses(
    prepared: Mapping[str, object],
    *,
    project_id: str,
    technical_id: str,
    workdir: str,
    interpreter: str,
    layer: str,
    runner: ControlledSourceCheckRunner | None,
    environment_ref: str,
    rules_revision: str,
    adapter_version: str,
) -> tuple[
    tuple[SourceCheckStatus, ...],
    tuple[SourceCheckStatus, ...],
    tuple[SourceCheckResult, ...],
]:
    """按冻结登记运行 build/minimal_start，返回 `(全部子项, 必需失败子项)`（DEC-021）。

    - 服务模板缺登记：`UNKNOWN` + 必需失败，L1 不得整体 verified；
    - 库/其他已知模板未登记：显式 `not_applicable` 并带原因，不静默跳过；
    - 模板无法判断：`UNKNOWN`，不代判不适用；
    - 只有 `required` 子项失败会降级总状态，`optional` 仍逐项保存。
    """

    if layer != "L1":
        # 构建/最小启动是 L1 子项；L2/L3 动作不重复启动服务，也不把未执行写成通过。
        return (
            tuple(
                SourceCheckStatus(
                    check_type=check_type,
                    state=SourceCheckStatusState.NOT_APPLICABLE,
                    reason="not_an_l1_action",
                )
                for check_type in (
                    SourceCheckType.APPLICABLE_BUILD,
                    SourceCheckType.MINIMAL_START,
                )
            ),
            (),
            (),
        )
    execution_source = prepared.get("execution_source")
    if not isinstance(execution_source, Mapping):
        return (), (), ()
    raw_templates = prepared.get("template_versions")
    template_ids = sorted(
        {
            str(item["template_id"])
            for item in raw_templates
            if isinstance(item, Mapping) and isinstance(item.get("template_id"), str)
        }
        if isinstance(raw_templates, (list, tuple))
        else ()
    )
    is_service = "python-service" in template_ids
    raw_plans = execution_source.get("source_checks")
    plans = SourceCheckPlans()
    if isinstance(raw_plans, Mapping):
        try:
            plans = SourceCheckPlans.model_validate(raw_plans)
        except ValueError as error:
            invalid = tuple(
                SourceCheckStatus(
                    check_type=check_type,
                    state=SourceCheckStatusState.UNKNOWN,
                    reason=f"source_check_plan_invalid: {error}",
                )
                for check_type in (
                    SourceCheckType.APPLICABLE_BUILD,
                    SourceCheckType.MINIMAL_START,
                )
            )
            return invalid, invalid, ()

    statuses: list[SourceCheckStatus] = []
    failures: list[SourceCheckStatus] = []
    results: list[SourceCheckResult] = []
    for slot, check_type in (
        ("build", SourceCheckType.APPLICABLE_BUILD),
        ("minimal_start", SourceCheckType.MINIMAL_START),
    ):
        plan: SourceCheckPlan | None = getattr(plans, slot)
        if plan is None:
            if is_service:
                status = SourceCheckStatus(
                    check_type=check_type,
                    state=SourceCheckStatusState.UNKNOWN,
                    reason="source_check_plan_unregistered",
                )
                failures.append(status)
            elif template_ids:
                status = SourceCheckStatus(
                    check_type=check_type,
                    state=SourceCheckStatusState.NOT_APPLICABLE,
                    reason="template_default_not_applicable:" + ",".join(template_ids),
                )
            else:
                status = SourceCheckStatus(
                    check_type=check_type,
                    state=SourceCheckStatusState.UNKNOWN,
                    reason="source_check_template_unknown",
                )
            statuses.append(status)
            continue
        if plan.applicability is SourceCheckApplicability.NOT_APPLICABLE:
            statuses.append(
                SourceCheckStatus(
                    check_type=check_type,
                    state=SourceCheckStatusState.NOT_APPLICABLE,
                    reason=plan.reason or "explicitly not applicable",
                )
            )
            continue
        if runner is None:
            status = SourceCheckStatus(
                check_type=check_type,
                state=SourceCheckStatusState.BLOCKED,
                reason="source_check_evidence_store_unavailable",
            )
        else:
            try:
                outcome = runner.run(
                    SourceCommandRequest(
                        check_type=check_type,
                        check_id=f"source-check:{technical_id}:{check_type.value}",
                        project_id=project_id,
                        command=plan.command,
                        workdir=workdir,
                        interpreter=interpreter,
                        timeout_ms=plan.timeout_ms,
                        descriptor=plan.descriptor,
                        ready_when=plan.ready_when,
                    )
                )
            except ValueError as error:
                status = SourceCheckStatus(
                    check_type=check_type,
                    state=SourceCheckStatusState.BLOCKED,
                    reason=f"source_check_request_invalid: {error}",
                )
            else:
                status = SourceCheckStatus(
                    check_type=check_type,
                    state=outcome.state,
                    reason=outcome.reason,
                    evidence_ref=outcome.evidence_ref,
                    failure_class=outcome.failure_class,
                )
                results.append(
                    SourceCheckResult(
                        check_result_id=f"source-check:{technical_id}:{check_type.value}",
                        attempt_id="pending-attempt",
                        check_type=check_type,
                        scope=workdir,
                        source_snapshot_ref=technical_id,
                        environment_ref=environment_ref,
                        rules_revision=rules_revision,
                        adapter_version=adapter_version,
                        failure_class=outcome.failure_class,
                        raw_output_evidence_ref=outcome.evidence_ref,
                        evidence_refs=(
                            (outcome.evidence_ref,)
                            if outcome.evidence_ref is not None
                            else ()
                        ),
                        argument_refs=plan.command[1:],
                    )
                )
        statuses.append(status)
        if plan.applicability is SourceCheckApplicability.REQUIRED and (
            status.state is not SourceCheckStatusState.VERIFIED
        ):
            failures.append(status)
    return tuple(statuses), tuple(failures), tuple(results)

def _source_verification_provider(
    snapshots: SourceSnapshotPort,
    reader: RecordReader,
    source_analysis: SourceAnalysisService,
    objects: FileObjectStore | None = None,
    *,
    instance_id: str | None = None,
) -> Callable[
    [str, Mapping[str, object], Mapping[str, object], str], ExecutionSourceVerification
]:
    """装配层适配：合成「来源核验事实」（架构01 第 12 节 / P1-FR08 / P1-AC13）。

    这是**一期**要求：需求 P1-FR08 明确 L1 覆盖「Python 语法、适用构建、加载或最小启动」，
    P1-AC13 要求 start 准入核对实际来源。

    **L1 子项**：

    1. `SYNTAX`／`LOAD`：有登记模块时用 `LOAD`（真实导入 + 逐模块摘要），无登记模块时用
       `SYNTAX`（实际编译物化范围内全部 `.py` 文件）；两类都**实际执行**，不再只核
       "文件存在/摘要非空"。
    2. `APPLICABLE_BUILD`／`MINIMAL_START`：按 DEC-021 的 `ExecutionSourceBinding.source_checks`
       显式登记，由 `ControlledSourceCheckRunner` 用冻结解释器实际执行；每个子项的状态、原因
       和证据引用进入 `checks`。`required` 子项失败会降级总来源核验；服务模板缺登记在准备
       阶段已阻塞，旧记录缺字段则保留 `unknown` 缺口，不追溯升级。

    **非空转**：
    - 业务 `SnapshotRef` → 技术 `pinned_snapshot_id` 走 `SourceAnalysisService.verify_saved`
      （按项目与准确修订读回并逐 blob 复核），不再把业务 ID 当技术 ID（C-15）；
    - `observed_source_digest` 由重新核对过的固定字节按唯一内容身份口径**重算**，不回声冻结值；
    - 语法在**登记解释器**下逐个 `.py` 实际 `compile`；每个登记模块的实际加载文件/摘要/失败
      全部进入观测事实，由 `_derive_state` 逐项判定（C-16）；
    - 入口路径取自物化的期望→实际映射（`paths[*].actual_path`，由 `resolve` 保证落在固定
      workdir 内）。映射摘要一致性**已由** `StartSourceBindingResolver.resolve` 重算比对，
      此处不重复声称。
    """

    service = SourceVerificationService()
    runner = ControlledSourceCheckRunner(objects) if objects is not None else None

    def provide(
        workdir: str,
        admitted: Mapping[str, object],
        prepared: Mapping[str, object],
        layer: str,
    ) -> ExecutionSourceVerification:
        project_id = _required_text(prepared.get("project_id"), "project_id")
        snapshot = prepared.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise SourceBindingUnverified("frozen preparation has no snapshot to verify")
        business_id = _required_text(
            snapshot.get("source_snapshot_id"), "snapshot.source_snapshot_id"
        )
        record_revision = _positive_int(
            snapshot.get("record_revision", 1), "snapshot.record_revision"
        )
        expected_identity = _required_text(
            snapshot.get("content_identity"), "snapshot.content_identity"
        )
        # 一次权威读取：既给出技术身份，也用同一份业务记录重算内容身份。
        saved = source_analysis.verify_saved(
            project_id=project_id, snapshot_id=business_id, revision=record_revision
        )
        technical_id = _technical_identity(saved)
        pinned = snapshots.verify_pinned(technical_id)
        observed_identity = _observed_content_identity(saved, pinned)
        environment = prepared.get("environment")
        if not isinstance(environment, Mapping):
            raise SourceBindingUnverified("frozen preparation has no environment")
        resolution = environment.get("resolution")
        if not isinstance(resolution, Mapping):
            raise SourceBindingUnverified("frozen environment has no resolved carrier")
        interpreter = _required_text(
            resolution.get("executable_path"), "environment.resolution.executable_path"
        )
        entry_path = _entry_actual_path(admitted.get("paths"), workdir, prepared)
        plan_revision = prepared.get("plan_revision")
        if not isinstance(plan_revision, Mapping):
            raise SourceBindingUnverified("frozen preparation has no plan revision")
        # 登记模块的实际加载来源：以探针自己的 sys.path（物化根）推导可导入名并真的导入。
        # 推导不出任何模块时不伪造结论，退回只覆盖「语法」一类的 SYNTAX。
        module_names, expected_modules = _module_expectations(
            reader, project_id, prepared.get("project_revision"), workdir, pinned
        )
        try:
            fact = PythonLoadSourceProbe(executable=interpreter).probe(
                materialized_root=workdir, modules=module_names
            )
        except PythonSourceProbeError as error:
            # 探针起不来（登记解释器不可执行/超时/协议被破坏）是**来源取证缺口**，
            # 不是可以逃逸的内部错误：按 DEC-012 归为不可重试的来源未核实阻塞。
            fact = LoadSourceFact(
                requested_executable=interpreter,
                executable=interpreter,
                version="",
                prefix="",
                pythonpath_env=None,
                sys_path=(),
                project_scope_entries=(),
                modules=(),
                probe_error=str(error),
            )
        raw_object = _probe_output_reference(objects, project_id, fact)
        raw_reference = raw_object.relative_path if raw_object is not None else None
        observed_modules: list[SourceModuleFact] = []
        modules_by_name = {item.module: item for item in fact.modules}
        for name in module_names:
            item = modules_by_name.get(name)
            observed_modules.append(
                SourceModuleFact(
                    module=name,
                    expected_digest=expected_modules.get(name),
                    loaded_file=item.file if item is not None else None,
                    observed_digest=item.sha256 if item is not None else None,
                    load_error=(
                        item.load_error
                        if item is not None
                        else "the probe reported no fact for this registered module"
                    ),
                )
            )
        loaded_files = [
            item.file
            for item in fact.modules
            if item.file is not None and item.load_error is None and item.module in expected_modules
        ]
        # `LOAD` 只在**确有文件型登记的模块**时使用；纯命名空间包没有文件型加载来源，
        # 退回 `SYNTAX`（仍然实际编译范围内全部 .py），不伪造一个 load 引用。
        check_type = SourceCheckType.LOAD if expected_modules else SourceCheckType.SYNTAX
        import_ref = loaded_files[0] if loaded_files else None
        observation = SourceProbeObservation(
            observed_source_digest=observed_identity,
            observed_entry_ref=entry_path,
            observed_import_ref=import_ref,
            # 实际解释器取探针自报的 realpath，**不是**冻结值回声；冻结值作为期望侧比对。
            observed_interpreter_ref=fact.executable,
            failure_class=(
                FailureClass.ENVIRONMENT_UNREACHABLE
                if fact.probe_error is not None
                else (FailureClass.SOURCE_ERROR if fact.syntax_errors else None)
            ),
            raw_output_evidence_ref=raw_reference,
            observed_python_file_count=len(getattr(fact, "python_files", ())),
            observed_syntax_errors=tuple(
                SourceSyntaxErrorFact(relative_path=item.relative_path, reason=item.reason)
                for item in fact.syntax_errors
            ),
            observed_modules=tuple(observed_modules),
            probe_error=fact.probe_error,
        )
        request = SourceCheckRequest(
            verification_id="source-verification:" + technical_id,
            check_result_id="source-check:" + technical_id,
            project_id=project_id,
            attempt_id="pending-attempt",
            plan_revision_ref=PlanRevisionRef(
                revision_id=_required_text(plan_revision.get("revision_id"), "plan.revision_id"),
                revision_no=_positive_int(plan_revision.get("revision_no"), "plan.revision_no"),
                digest=_required_text(plan_revision.get("digest"), "plan.digest"),
            ),
            expected_source_content_digest=expected_identity,
            materialized_snapshot_ref=workdir,
            check_type=check_type,
            scope=workdir,
            source_snapshot_ref=technical_id,
            environment_ref=_environment_ref(environment),
            rules_revision=_rules_revision(prepared),
            adapter_version=_frozen_adapter_version(prepared),
            observed_source_digest=observed_identity,
            observed_entry_ref=entry_path,
            observed_import_ref=import_ref,
            expected_interpreter_ref=interpreter,
            raw_output_evidence_ref=raw_reference,
        )
        checked = service.check(request, observation)
        verification = checked.verification
        admission_check_results = [checked.check_result]
        if raw_object is not None and raw_object.digest != (
            "sha256:" + hashlib.sha256(fact.raw_output).hexdigest()
        ):
            verification = replace(
                verification, state=SourceVerificationState.UNVERIFIED,
                gap_ids=(*verification.gap_ids, "source_probe_material_redacted"),
            )
        statuses, required_failures, command_results = _registered_source_check_statuses(
            prepared,
            project_id=project_id,
            technical_id=technical_id,
            workdir=workdir,
            interpreter=interpreter,
            layer=layer,
            runner=runner,
            environment_ref=_environment_ref(environment),
            rules_revision=_rules_revision(prepared),
            adapter_version=_frozen_adapter_version(prepared),
        )
        admission_check_results.extend(command_results)
        verification = merge_source_checks(
            verification,
            statuses=statuses,
            required_failures=required_failures,
        )
        verification = replace(
            verification, check_results=tuple(admission_check_results)
        )
        return replace(
            verification, raw_output_object=raw_object, source_instance_id=instance_id,
            capture_plan=RuntimeSourcePlan(carrier_digest=carrier_digest(), files=tuple(
                RuntimeSourceFile(str(item["relative_path"]), str(item["content_digest"]))
                for item in _pinned_file_digests(pinned)
                if str(item["relative_path"]).endswith(".py")
            )),
        )

    return provide

def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceBindingUnverified(f"{name} must be nonempty text")
    return value

def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise SourceBindingUnverified(f"{name} must be a positive integer")
    return value

def _entry_actual_path(
    paths: object, workdir: str, prepared: Mapping[str, object] | None = None
) -> str:
    """取物化的期望→实际映射里的入口实际路径（必须落在固定 workdir 内）。

    优先取**冻结入口参数**指向的文件（`execution_source.entry_arguments` 里的相对路径），
    而不是清单里的第一个文件：入口身份必须来自冻结事实，取任意文件只是"恰好存在"。
    冻结参数没有落入物化清单时回落到清单中的第一个文件（仍必须落在固定 workdir 内）。
    """

    if not isinstance(paths, (list, tuple)) or not paths:
        raise SourceBindingUnverified("materialization has no expected→actual path mapping")
    root = Path(workdir).resolve()
    # 清单里任何一条 actual_path 越界都必须拒绝：只在“取入口”时过滤会让
    # 冻结入口参数掩盖一条越界映射（测试 test_an_entry_outside... 锁定该行为）。
    for item in paths:
        if not isinstance(item, Mapping):
            continue
        actual = item.get("actual_path")
        if isinstance(actual, str) and actual.strip():
            candidate = Path(actual)
            if candidate.is_absolute() and not candidate.is_relative_to(root):
                raise SourceBindingUnverified("materialized path escapes the fixed workdir")
    wanted: set[str] = set()
    if isinstance(prepared, Mapping):
        source = prepared.get("execution_source")
        arguments = source.get("entry_arguments") if isinstance(source, Mapping) else None
        if isinstance(arguments, (list, tuple)):
            wanted = {
                item.replace("\\", "/").lstrip("./")
                for item in arguments
                if isinstance(item, str) and item.strip()
            }

    def _inside(actual: str) -> str | None:
        candidate = Path(actual)
        if (
            candidate.is_absolute()
            and candidate.is_relative_to(root)
            and (candidate.is_file() or candidate.is_dir())
        ):
            return str(candidate)
        return None

    # A frozen entry argument may be a module scope directory (e.g. pytest
    # `pkgA/tests`) rather than a file. It is still the actual entry when it
    # exists inside the fixed materialized workdir.
    for relative_entry in sorted(wanted):
        candidate = (root / relative_entry).resolve()
        if (
            candidate.is_relative_to(root)
            and (candidate.is_file() or candidate.is_dir())
        ):
            return str(candidate)

    fallback: str | None = None
    for item in paths:
        if not isinstance(item, Mapping):
            continue
        actual = item.get("actual_path")
        relative = item.get("relative_path")
        if not isinstance(actual, str) or not actual.strip():
            continue
        resolved = _inside(actual)
        if resolved is None:
            continue
        if fallback is None:
            fallback = resolved
        if isinstance(relative, str) and relative.replace("\\", "/") in wanted:
            return resolved
    if fallback is not None:
        return fallback
    raise SourceBindingUnverified("no entry path inside the fixed workdir to verify")

def _environment_ref(environment: Mapping[str, object]) -> str:
    environment_id = environment.get("environment_id")
    revision = environment.get("revision")
    if not isinstance(environment_id, str) or not environment_id.strip():
        raise SourceBindingUnverified("frozen environment has no identity")
    if type(revision) is not int or revision < 1:
        raise SourceBindingUnverified("frozen environment has no exact revision")
    return f"{environment_id}@{revision}"

def _frozen_adapter_version(prepared: Mapping[str, object]) -> str:
    """冻结的适配器版本：取 `execution_source.adapter_versions` 的唯一取值。

    与 `_adapter_kind` 同源（唯一键）。
    """

    source = prepared.get("execution_source")
    if not isinstance(source, Mapping):
        raise SourceBindingUnverified("frozen preparation has no execution source binding")
    versions = source.get("adapter_versions")
    if not isinstance(versions, Mapping) or not versions:
        raise SourceBindingUnverified("frozen binding has no adapter versions")
    unique = {str(value) for value in versions.values()}
    if len(unique) != 1:
        raise SourceBindingUnverified("frozen binding has ambiguous adapter versions")
    return next(iter(unique))

def _rules_revision(prepared: Mapping[str, object]) -> str:
    versions = prepared.get("rule_versions")
    if not isinstance(versions, (list, tuple)):
        raise SourceBindingUnverified("frozen preparation has no rule versions")
    payload = sorted(
        json.dumps(dict(item), sort_keys=True, ensure_ascii=False) for item in versions
    )
    return "rules:" + hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()
