"""生产动作解析器：把 B 的冻结绑定与 C 的 start 准入事实构造成一次动作。

依据：架构01 第12节（prepare 冻结执行来源；start 在固定 workdir 解析实际路径并保存映射与
`source_binding_digest`）、架构02 第7节（`ExecutionRequest` 组成）、
`docs/接口对接/进行中/AB-001-端口与保存/contract.md` 1.36（四项 start 侧语义裁定）。

**职责边界**：只**构造**动作对象——不起进程、不写记录、不落证据。
`authorization_id` 与 `attempt_id` 由 `authorization.py::prepare` 在解析器返回后按
（工作空间, 项目, intent）自行铸造并复核环境/快照身份、步骤与计划修订、冻结步骤截止，
因此本解析器只需给出**自洽**的 `authorization_ref` 与 `attempt`/`request` 身份。

**无法从冻结数据推出的输入**（由装配注入，不臆造规则）：
- `attempt_index`：该 Run/Step 的下一尝试序号（需已保存尝试历史）；
- `side_effect_class`：该步骤的副作用类别（由冻结计划/步骤提供）；
- `pinned_snapshot_ref`：业务 `SnapshotRef` → 技术固定清单身份（需权威业务记录 + 逐 blob 复核）；
- `step_timeout_seconds`：冻结步骤截止（声明在**环境记录**里，冻结 `EnvironmentRefFact`
  投影不带该字段）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from aitest.application.execution.start_materialization import StartMaterializer
from aitest.application.execution.start_source_binding import (
    SourceBindingUnverified,
    StartSourceBindingResolver,
)
from aitest.application.ports import SourceSnapshotPort
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.prepared_run import EnvironmentResolutionFact, ExecutionSourceBinding
from aitest.domain.execution.authorization import ResolvedExecutionAction
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    AuthorizationRef,
    ExecutionRequest,
    RegisteredEntryRef,
    Run,
    SideEffectClass,
    Step,
)
from aitest.domain.execution.sources import ExecutionSourceVerification

#: 解析阶段的占位标识：prepare 返回后会用权威标识替换（见其 461-475 行）。
_PENDING_GRANT = "resolved-pending-grant"
_PENDING_ATTEMPT = "resolved-pending-attempt"


class ActionResolutionBlocked(ValueError):
    """内部阻塞标识：**不是**已登记的协议错误码。

    对外暴露前必须先在 AB-001 登记（做法参见 DEC-012 的错误码登记）。
    """

    code = "ACTION_RESOLUTION_BLOCKED"


class SavedActionResolver:
    """把冻结绑定 + start 准入事实解析成 `ResolvedExecutionAction`。"""

    _validators = StartSourceBindingResolver.__new__(StartSourceBindingResolver)

    def __init__(
        self,
        *,
        attempt_index: Callable[[Run, Step], int],
        side_effect_class: Callable[[Run, Step], SideEffectClass],
        admission: Callable[..., Mapping[str, object]] | None = None,
        interpreter_verifier: Callable[[str, EnvironmentResolutionFact], str] | None = None,
        source_verification_provider: (
            Callable[
                [str, Mapping[str, object], Mapping[str, object]],
                ExecutionSourceVerification,
            ]
            | None
        ) = None,
        pinned_snapshot_ref: Callable[[str, str, int], str] | None = None,
        step_timeout_seconds: Callable[[str, str, int], int | None] | None = None,
    ) -> None:
        if not callable(attempt_index) or not callable(side_effect_class):
            raise ActionResolutionBlocked("attempt index and side effect providers are required")
        if interpreter_verifier is not None and not callable(interpreter_verifier):
            raise ActionResolutionBlocked("interpreter verifier must be callable")
        if source_verification_provider is not None and not callable(source_verification_provider):
            raise ActionResolutionBlocked("source verification provider must be callable")
        if pinned_snapshot_ref is not None and not callable(pinned_snapshot_ref):
            raise ActionResolutionBlocked("pinned snapshot resolver must be callable")
        if step_timeout_seconds is not None and not callable(step_timeout_seconds):
            raise ActionResolutionBlocked("step timeout provider must be callable")
        self._attempt_index = attempt_index
        self._side_effect_class = side_effect_class
        self._admission = admission
        self._interpreter_verifier = interpreter_verifier
        self._source_verification_provider = source_verification_provider
        self._pinned_snapshot_ref = pinned_snapshot_ref
        self._step_timeout_seconds = step_timeout_seconds

    def resolve(
        self,
        *,
        run: Run,
        step: Step,
        intent_id: str,
        prepared: Mapping[str, object],
        step_content: Mapping[str, object],
    ) -> ResolvedExecutionAction:
        if not isinstance(prepared, Mapping) or not isinstance(step_content, Mapping):
            raise ActionResolutionBlocked("prepared run and step content must be mappings")
        if not isinstance(intent_id, str) or not intent_id.strip():
            raise ActionResolutionBlocked("action resolution requires a nonempty intent")
        binding = _binding(prepared)
        (
            business_snapshot_id,
            snapshot_revision,
            source_identity,
        ) = _snapshot(prepared)
        # C-15：业务 source-ID 与技术 pin-ID 是**两个**身份。冻结准备记录只携带业务
        # `SnapshotRef`（`source_snapshot_id`/`content_identity`/`record_revision`）；
        # 物化端口只接受技术 `snap-*`。两者必须经权威业务记录核对后映射，不能把业务
        # ID 直接当地址用（旧默认装配即因此报 "pinned source snapshot identity is invalid"）。
        snapshot_id = self._pinned_snapshot(run.project_id, business_snapshot_id, snapshot_revision)
        environment_identity = _environment_identity(prepared)
        timeout_ms = self._frozen_timeout_ms(run.project_id, prepared)
        _step_content_scope(step_content, run, step)
        environment_fact = _environment_resolution(prepared)
        entry = self._frozen_entry(step, binding, environment_fact)
        adapter_kind = self._adapter_kind(binding, entry.adapter_kind)
        arguments = self._arguments(binding, entry)
        source_binding_digest, workdir, admitted = _admission_digest(
            self._admission, run, step, snapshot_id, binding
        )
        self._require_registered_entry(binding, entry)
        self._require_observed_interpreter(workdir, environment_fact)
        source_verification = self._require_verified_source(workdir, admitted, prepared)
        index = self._attempt_index(run, step)
        if type(index) is not int or index < 1:
            raise ActionResolutionBlocked("attempt index must be a positive integer")
        side_effect_class = self._side_effect_class(run, step)
        if not isinstance(side_effect_class, SideEffectClass):
            raise ActionResolutionBlocked("side effect class must be a domain value")
        authorization = AuthorizationRef(
            authorization_id=_PENDING_GRANT,  # prepare 按（工作空间, 项目, intent）替换
            intent_id=intent_id,
            step_id=step.step_id,
            resolved_input_digest=binding.resolved_input_digest,
            target_ref=entry.entrypoint,
            credential_scope_ref=binding.test_config_ref,
            plan_revision_ref=run.plan_revision_ref,
            step_revision_ref=step.step_revision_ref,
        )
        try:
            request = ExecutionRequest(
                project_id=run.project_id,
                run_id=run.run_id,
                step_id=step.step_id,
                attempt_id=_PENDING_ATTEMPT,  # prepare 替换
                intent_id=intent_id,
                resolved_input_digest=binding.resolved_input_digest,
                registered_entry=RegisteredEntryRef(
                    entry_id=entry.entry_id,
                    adapter_kind=adapter_kind,
                    entrypoint=entry.entrypoint,
                    arguments=arguments,
                ),
                materialized_snapshot_ref=workdir,
                environment_ref=run.environment_ref,
                source_binding_digest=source_binding_digest,
                authorization_ref=authorization,
                side_effect_class=side_effect_class,
                timeout_ms=timeout_ms,
                expected_plan_revision_ref=run.plan_revision_ref,
            )
            attempt = Attempt(
                attempt_id=_PENDING_ATTEMPT,
                run_id=run.run_id,
                step_id=step.step_id,
                attempt_index=index,
                resolved_input_digest=binding.resolved_input_digest,
                step_revision_ref=step.step_revision_ref,
                source_binding_digest=source_binding_digest,
                side_effect_class=side_effect_class,
                adapter_kind=adapter_kind,
                adapter_version=binding.adapter_versions[adapter_kind.value],
                state=AttemptState.INTENT_RECORDED,
                intent_id=intent_id,
                expected_plan_revision_ref=run.plan_revision_ref,
                authorization_ref=authorization,
                timeout_ms=timeout_ms,
            )
        except ValueError as error:
            raise ActionResolutionBlocked(
                f"resolved action cannot be constructed: {error}"
            ) from error
        try:
            return ResolvedExecutionAction(
                attempt, request, environment_identity, source_identity, source_verification
            )
        except ValueError as error:  # 领域不变量：构造成功即证明自洽
            raise ActionResolutionBlocked(f"resolved action is inconsistent: {error}") from error

    def _frozen_timeout_ms(self, project_id: str, prepared: Mapping[str, object]) -> int | None:
        """冻结环境声明的**步骤截止**（毫秒）。

        截止由环境记录声明（`environment.step_timeout_seconds`），而冻结准备记录里的
        `EnvironmentRefFact` 投影**不带**该字段——`ExecutionAuthorizationService`
        另行从环境记录读取并逐字比对（`_require_frozen_step_deadline`）。若解析器只看投影，
        会算出 `None` 而权威侧算出真实毫秒数，默认链必然在授权阶段阻塞。

        因此这里与「业务→技术快照」同一做法：投影里有值就用投影（兼容旧记录），
        没有则由装配注入的 `step_timeout_seconds(project, environment_id, revision)`
        从**权威环境记录**读取；两处都没有即 `None`（缺失是历史事实，不是新的执行依据）。
        """

        raw = prepared.get("environment")
        seconds = raw.get("step_timeout_seconds") if isinstance(raw, Mapping) else None
        if seconds is None and self._step_timeout_seconds is not None and isinstance(raw, Mapping):
            environment_id, revision = raw.get("environment_id"), raw.get("revision")
            if isinstance(environment_id, str) and environment_id.strip() and type(revision) is int:
                try:
                    seconds = self._step_timeout_seconds(project_id, environment_id, revision)
                except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                    raise ActionResolutionBlocked(
                        f"the frozen step timeout cannot be verified: {error}"
                    ) from error
        if seconds is None:
            return None
        if type(seconds) is not int or seconds <= 0:
            raise ActionResolutionBlocked(
                "frozen step timeout must be a positive integer second count"
            )
        return seconds * 1000

    def _frozen_entry(
        self,
        step: Step,
        binding: ExecutionSourceBinding,
        environment: EnvironmentResolutionFact,
    ) -> RegisteredEntryRef:
        """确定本次执行的**已登记入口**；两条来源都必须是冻结事实。

        契约依据：
        - 架构01 第12节：prepare 冻结「解释器/可执行文件身份、…、已登记入口与参数」到
          `ExecutionSourceBinding`；`Step`（架构02 第1节）是该登记的逐步投影。
        - `EnvironmentResolutionFact` 的自述：*Observed by a core-owned registered carrier,
          never a client declaration* —— 可执行路径**只**能取核心观测事实。
        - 架构02 第1/7节：命令使用登记入口及参数数组，禁止把模型或正文拼成 shell。
        - 能力清单：用户登记解释器；**未登记入口不猜命令**。

        因此这里不发明命令：入口身份与参数逐字取自冻结事实。步骤侧已携带入口时以其为准
        （它是 prepare 写入的投影），未携带时（当前生产路径的现状）回落到冻结绑定。
        """

        declared = step.registered_entry_ref
        if declared is not None:
            return declared
        entry_id = binding.registered_entry
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise ActionResolutionBlocked("the frozen binding has no registered entry")
        executable = environment.executable_path
        if not isinstance(executable, str) or not executable.strip():
            raise ActionResolutionBlocked("the observed carrier has no executable identity")
        try:
            kind = AdapterKind(self._validators.resolve_adapter_kind(binding.adapter_versions))
        except (SourceBindingUnverified, ValueError) as error:
            raise ActionResolutionBlocked(str(error)) from error
        return RegisteredEntryRef(
            entry_id=entry_id,
            adapter_kind=kind,
            entrypoint=executable,
            arguments=tuple(binding.entry_arguments),
        )

    def _pinned_snapshot(
        self, project_id: str, source_snapshot_id: str, record_revision: int
    ) -> str:
        """业务来源记录 → **技术固定清单身份**（C-15）。

        依据 AB-001 1.20/1.21 与 BC-001 第 6 节：B 用现有 `SourceManifest`/`content_identity`
        合同保存不可变**业务**快照，另冻结 `pinned_snapshot_id` 与准确 binding 引用；业务
        `snapshot_id` 带项目/绑定修订命名空间。物化与逐 blob 复核只认技术清单身份，因此
        这里必须经**权威业务记录**核对后取技术身份，而不是把业务 ID 当地址。

        注入方（`bootstrap`）提供 `pinned_snapshot_ref`：按（项目, 业务快照 ID, 记录修订）
        读取已保存业务快照并**重新核对固定 blob**后交回技术身份。未注入即失败关闭：
        没有权威映射时不得猜测"业务 ID 等于技术 ID"。
        """

        if self._pinned_snapshot_ref is None:
            raise ActionResolutionBlocked(
                "the technical pinned snapshot carrier is not configured"
            )
        try:
            pinned = self._pinned_snapshot_ref(project_id, source_snapshot_id, record_revision)
        except SourceBindingUnverified as error:
            raise ActionResolutionBlocked(str(error)) from error
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            raise ActionResolutionBlocked(
                f"the pinned source snapshot cannot be verified: {error}"
            ) from error
        if not isinstance(pinned, str) or not pinned.strip():
            raise ActionResolutionBlocked("the pinned source snapshot has no technical identity")
        return pinned

    def _require_verified_source(
        self,
        workdir: str,
        admitted: Mapping[str, object],
        prepared: Mapping[str, object],
    ) -> ExecutionSourceVerification | None:
        """可选的「来源核验事实」核对（架构01 第 12 节 / P1-AC13）。

        注入方（`bootstrap`）提供 `source_verification_provider`：给定物化 workdir、准入映射
        （含期望→实际路径映射与映射摘要）与冻结准备记录，它**现场探测**并合成
        `ExecutionSourceVerification`；应用层只按既有 `require_verified_source` 判定——
        非 `verified` 一律阻塞，缺口编码由 `source_checks._derive_state` 给出，不在此层重算。

        未注入时本核对**不执行**，也不声称来源已核验（与解释器核对同一约定）。
        """

        if self._source_verification_provider is None:
            return None
        try:
            verification = self._source_verification_provider(workdir, admitted, prepared)
        except SourceBindingUnverified as error:
            raise ActionResolutionBlocked(str(error)) from error

        try:
            self._validators.require_verified_source(verification)
        except SourceBindingUnverified as error:
            raise ActionResolutionBlocked(str(error)) from error
        return verification

    def _require_registered_entry(
        self, binding: ExecutionSourceBinding, entry: RegisteredEntryRef
    ) -> None:
        """核对**已登记入口**：冻结来源绑定声明的入口 == 解析器实际将使用的入口。

        依据架构01 第 12 节：prepare 冻结「已登记入口与参数」，start 在固定 workdir 中解析
        实际路径并保存「期望→实际路径映射」。这里核对的是**入口身份**这一层：冻结绑定里的
        `registered_entry`（期望）必须与本次解析实际采用的入口身份（实际）逐字一致——
        能力清单「未登记入口不猜命令」的落点。`Step` 自带冻结入口、与绑定不一致时，
        本核对会阻断而不是照用其中一个。

        未登记入口（空值）不得通过：没有可核对的入口身份时，不能声称"入口已登记"。
        """

        observed = entry.entry_id
        if not isinstance(observed, str) or not observed.strip():
            raise ActionResolutionBlocked(
                "resolved entry has no registered entry identity to verify"
            )
        try:
            self._validators.require_registered_entry(
                frozen_entry=binding.registered_entry,
                observed_entry=observed,
            )
        except SourceBindingUnverified as error:
            raise ActionResolutionBlocked(str(error)) from error

    def _require_observed_interpreter(
        self, workdir: str, environment: EnvironmentResolutionFact
    ) -> None:
        """可选的「实际解释器」核对（架构01 第12节 / P1-AC13 / A-08）。

        注入方（`bootstrap`）提供 `interpreter_verifier`：给定物化 workdir 与冻结环境事实，
        它去**现场探测**并把解释器自报的 realpath 交回来；比较口径以
        `source_checks._derive_state` 第 182–186 行为准——**解析后的路径一致**，
        不引入摘要比对。未注入时本核对**不执行**，也不声称已核对。

        应用层不得导入 `infrastructure`（架构依赖方向），因此探针只能由装配层注入。
        """

        if self._interpreter_verifier is None:
            return
        try:
            observed = self._interpreter_verifier(workdir, environment)
        except SourceBindingUnverified as error:
            raise ActionResolutionBlocked(str(error)) from error
        if not isinstance(observed, str):
            raise ActionResolutionBlocked("interpreter verifier must return an observed path")
        try:
            self._validators.require_interpreter(
                frozen_interpreter=environment.executable_path,
                observed_executable=observed,
            )
        except SourceBindingUnverified as error:
            raise ActionResolutionBlocked(str(error)) from error

    def _adapter_kind(self, binding: ExecutionSourceBinding, frozen: AdapterKind) -> AdapterKind:
        try:
            key = self._validators.resolve_adapter_kind(binding.adapter_versions)
        except SourceBindingUnverified as error:
            raise ActionResolutionBlocked(str(error)) from error
        try:
            selected = AdapterKind(key)
        except ValueError as error:
            raise ActionResolutionBlocked(
                "adapter version key is not a known adapter kind"
            ) from error
        if selected is not frozen:
            raise ActionResolutionBlocked(
                "the frozen step entry adapter differs from the frozen binding adapter"
            )
        return selected

    def _arguments(
        self, binding: ExecutionSourceBinding, entry: RegisteredEntryRef
    ) -> tuple[str, ...]:
        try:
            return self._validators.require_frozen_arguments(
                frozen=binding.entry_arguments, actual=entry.arguments
            )
        except SourceBindingUnverified as error:
            raise ActionResolutionBlocked(str(error)) from error


def default_side_effect_class(run: Run, step: Step) -> SideEffectClass:
    """副作用类别：冻结计划/步骤未声明时取 **UNKNOWN（最严）**，绝不假定为只读。

    依据：领域既有取值 `SideEffectClass.UNKNOWN` 与项目"未知不得解释为安全/通过"的约定
    （未知副作用不得自动重放，须走受控授权）。若后续合同把该类别冻结进计划/步骤，
    装配应改为读取该字段。
    """
    return SideEffectClass.UNKNOWN


class FactsAttemptIndex:
    """从已保存运行事实派生下一尝试序号（`该步骤已有尝试数 + 1`）。

    事实形状由 `ExecutionFacts` **类型**约束（不再是鸭子类型）；只读、不写记录、不起进程。
    事实不可读或不一致时**失败关闭**（抛 `ActionResolutionBlocked`），不猜默认序号。
    """

    def __init__(self, read_facts: Callable[[str, str], ExecutionFacts]) -> None:
        self._read_facts = read_facts

    def __call__(self, run: Run, step: Step) -> int:
        try:
            facts = self._read_facts(run.project_id, run.run_id)
        except Exception as error:
            raise ActionResolutionBlocked(f"saved run facts cannot be read: {error}") from error
        if not isinstance(facts, ExecutionFacts):
            raise ActionResolutionBlocked(
                "saved run facts do not match the execution facts contract"
            )
        ids = [
            attempt.attempt_id for attempt in facts.attempts if attempt.step_id == step.step_id
        ]
        if any(not item.strip() for item in ids):
            raise ActionResolutionBlocked("saved attempts lack exact identities")
        if len(set(ids)) != len(ids):
            raise ActionResolutionBlocked("saved attempts repeat an identity")
        return len(ids) + 1


def _binding(prepared: Mapping[str, object]) -> ExecutionSourceBinding:
    raw = prepared.get("execution_source")
    if not isinstance(raw, Mapping):
        raise ActionResolutionBlocked("prepared run has no frozen execution source binding")
    try:
        return ExecutionSourceBinding.model_validate(dict(raw))
    except ValueError as error:
        raise ActionResolutionBlocked(
            f"frozen source binding cannot be verified: {error}"
        ) from error


def _snapshot(prepared: Mapping[str, object]) -> tuple[str, int, str]:
    """冻结的业务快照引用：`(source_snapshot_id, record_revision, content_identity)`。

    字段名以 `contracts.prepared_run.SnapshotRef` 为唯一来源（`source_snapshot_id`，
    **不是** `snapshot_id`）；`record_revision` 用于准确读取业务记录修订（BC-001 第 6 节）。
    """

    raw = prepared.get("snapshot")
    if not isinstance(raw, Mapping):
        raise ActionResolutionBlocked("prepared run has no frozen source snapshot")
    snapshot_id = raw.get("source_snapshot_id")
    identity = raw.get("content_identity")
    revision = raw.get("record_revision", 1)
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ActionResolutionBlocked("frozen snapshot lacks its identity")
    if not isinstance(identity, str) or not identity.strip():
        raise ActionResolutionBlocked("frozen snapshot lacks its content identity")
    if type(revision) is not int or revision < 1:
        raise ActionResolutionBlocked("frozen snapshot lacks its exact record revision")
    return snapshot_id, revision, identity


def _environment_resolution(prepared: Mapping[str, object]) -> EnvironmentResolutionFact:
    """冻结环境事实：由**核心观测的登记载体**产生，不是客户端自述。"""

    raw = prepared.get("environment")
    if not isinstance(raw, Mapping):
        raise ActionResolutionBlocked("prepared run has no frozen environment")
    resolution = raw.get("resolution")
    if not isinstance(resolution, Mapping):
        raise ActionResolutionBlocked("frozen environment has no resolved carrier")
    try:
        return EnvironmentResolutionFact.model_validate(dict(resolution))
    except ValueError as error:
        raise ActionResolutionBlocked(
            f"frozen environment fact cannot be verified: {error}"
        ) from error


def _environment_identity(prepared: Mapping[str, object]) -> str:
    fact = _environment_resolution(prepared)
    if not fact.content_identity.strip():
        raise ActionResolutionBlocked("frozen environment resolution has no content identity")
    return fact.content_identity


def _step_content_scope(step_content: Mapping[str, object], run: Run, step: Step) -> None:
    if step_content.get("step_id") not in (None, step.step_id):
        raise ActionResolutionBlocked("step content belongs to another step")
    if step_content.get("run_id") not in (None, run.run_id):
        raise ActionResolutionBlocked("step content belongs to another run")


def _admission_digest(
    admission: Callable[..., Mapping[str, object]] | None,
    run: Run,
    step: Step,
    snapshot_id: str,
    binding: ExecutionSourceBinding,
) -> tuple[str, str, Mapping[str, object]]:
    if admission is None:
        raise ActionResolutionBlocked(
            "start admission is required to derive the source binding digest"
        )
    admitted = admission(run, step, binding=binding, snapshot_id=snapshot_id)
    if not isinstance(admitted, Mapping):
        raise ActionResolutionBlocked("start admission must be a mapping")
    if admitted.get("snapshot_id") != snapshot_id:
        raise ActionResolutionBlocked("start admission belongs to another snapshot")
    digest = admitted.get("source_binding_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise ActionResolutionBlocked("start admission lacks an exact mapping digest")
    workdir = admitted.get("workdir")
    if not isinstance(workdir, str) or not workdir.strip():
        raise ActionResolutionBlocked("start admission lacks its fixed workdir")
    return digest, workdir, admitted


def build_saved_action_resolver(
    *,
    snapshots: SourceSnapshotPort,
    workspace_root: Path,
    read_facts: Callable[[str, str], ExecutionFacts],
    pinned_snapshot_ref: Callable[[str, str, int], str],
    read_step_timeout: Callable[[str, str, int], int | None],
    interpreter_verifier: Callable[[str, EnvironmentResolutionFact], str] | None = None,
    source_verification_provider: (
        Callable[
            [str, Mapping[str, object], Mapping[str, object]],
            ExecutionSourceVerification,
        ]
        | None
    ) = None,
) -> SavedActionResolver:
    """装配用工厂：业务→技术快照映射 + 物化准入 + 事实尝试序号 + 最严副作用类别。

    `pinned_snapshot_ref` 是**必填**：按（项目, 业务快照 ID, 记录修订）读取权威业务快照、
    重新核对固定 blob 后交回技术清单身份。缺少它默认装配不成立——旧实现把业务 `source-*`
    直接当技术 `snap-*` 交给物化端口，正是 C-15 的默认阻断。

    `read_step_timeout` 同样是**必填**：冻结步骤截止声明在环境记录里，而
    `EnvironmentRefFact` 投影不带该字段；授权阶段会拿环境记录逐字比对。
    只从投影读取会让默认链在授权处因"未冻结的截止"阻塞。

    准入覆盖范围（2026-10-11 明确，避免被误读为"只做物化 + 映射摘要"）：
    `StartMaterializer.materialize` 委托 `StartSourceBindingResolver.resolve`，因此这里
    **已经**完成物化结果必须 verified、快照身份一致、workdir 归属、逐文件摘要、映射摘要重算、
    期望清单恰好覆盖、受限 cwd 解析、entry_arguments 逐字与 adapter_versions 唯一键。

    **未覆盖**的是 `admit_start` 相对 `materialize` 多出的三项可选核对——实际已登记入口、
    实际解释器/可执行文件、以及来源核验必须为 `verified`；它们需要 source probe 与真实环境
    事实提供"实际值"。在接入这三项之前，默认装配不会声称启动前的实际来源已核对。
    """
    if not callable(pinned_snapshot_ref):
        raise ActionResolutionBlocked("a business to technical snapshot resolver is required")
    if not callable(read_step_timeout):
        raise ActionResolutionBlocked("a frozen step timeout provider is required")
    materializer = StartMaterializer(snapshots, workspace_root=workspace_root)

    def admission(
        run: Run,
        step: Step,
        *,
        binding: ExecutionSourceBinding,
        snapshot_id: str,
    ) -> Mapping[str, object]:
        return materializer.materialize(
            snapshot_id=snapshot_id, run_id=run.run_id, binding=binding
        )

    return SavedActionResolver(
        attempt_index=FactsAttemptIndex(read_facts),
        side_effect_class=default_side_effect_class,
        admission=admission,
        interpreter_verifier=interpreter_verifier,
        source_verification_provider=source_verification_provider,
        pinned_snapshot_ref=pinned_snapshot_ref,
        step_timeout_seconds=read_step_timeout,
    )


__all__ = [
    "ActionResolutionBlocked",
    "build_saved_action_resolver",
    "FactsAttemptIndex",
    "SavedActionResolver",
    "default_side_effect_class",
]
