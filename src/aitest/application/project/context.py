"""项目上下文用例：建立项目、绑定、模块、依赖与环境，并**列缺口而阻塞**。

对应需求 P1-FR01（本地项目与模块上下文）与 P1-FR02 的载体部分。
依赖方向：`application → domain`，本模块**只做编排与校验**，
落盘一律经 `substrate.UnitOfWork`（A 的端口落地后由转接头对接）。

**本模块最重要的一条**：上下文缺失时**列缺口并阻塞**，不编造依赖与结论
（需求 P1-AC17 原文："项目上下文缺失时列出缺什么并保持阻塞，不编造依赖和结论"）。
因此这里没有"猜一个默认值"的分支：缺什么就报什么。

路径与盘符探测属 I/O（由调用方完成），本模块不访问文件系统。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from aitest.domain.project.context import (
    BindingForm,
    Dependency,
    DriveKind,
    EnvironmentRef,
    IsolationMode,
    LocalProject,
    LocalProjectBinding,
    Module,
    ModuleDependencyGraph,
    SecretRef,
    WorkspaceLocationRejection,
    reject_workspace_location,
)

# ------------------------------------------------------------------ 缺口语义

#: 阻塞级缺口：上下文不完整，**不得继续准备或执行**。
GAP_MISSING_DEPENDENCY_REGISTRATION = "missing_dependency_registration"
GAP_MISSING_ENVIRONMENT_CARRIER = "missing_environment_carrier"
GAP_INCOMPLETE_ENVIRONMENT = "incomplete_environment"
GAP_NO_MODULES = "no_modules"

#: 提示级缺口：可在报告中声明，但**不阻塞**（不是"未执行"，也不是"不适用"）。
WARN_UNVERIFIED_DRIVE_KIND = "unverified_drive_kind"

_BLOCKING_GAP_KINDS: frozenset[str] = frozenset(
    {
        GAP_MISSING_DEPENDENCY_REGISTRATION,
        GAP_MISSING_ENVIRONMENT_CARRIER,
        GAP_INCOMPLETE_ENVIRONMENT,
        GAP_NO_MODULES,
    }
)


@dataclass(frozen=True, slots=True)
class ContextGap:
    """一处上下文缺口。

    `blocking` 决定它是"阻塞准备"还是"仅作提示"——两者不可混同：
    "未执行"不等于"不适用"，"无法核实"也不等于"通过"。
    """

    kind: str
    subject: str
    detail: str
    blocking: bool = True

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("gap kind must not be empty")
        if not self.subject.strip():
            raise ValueError("gap subject must not be empty")
        if not self.detail.strip():
            raise ValueError("gap detail must not be empty")
        if self.kind in _BLOCKING_GAP_KINDS and not self.blocking:
            raise ValueError(f"{self.kind} is a blocking gap by definition")


def blocking_gaps(gaps: tuple[ContextGap, ...]) -> tuple[ContextGap, ...]:
    return tuple(gap for gap in gaps if gap.blocking)


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_revision(value: int, name: str) -> None:
    if value < 1:
        raise ValueError(f"{name} must be >= 1")


# ------------------------------------------------------------------ 项目


def create_project(
    *,
    project_id: str,
    workspace_id: str,
    name: str,
    goal: str,
    created_at_commit: str,
    revision: int = 1,
    modules: tuple[Module, ...] = (),
) -> LocalProject:
    """建立本地项目。

    **目录不是 Git 仓库不构成拒绝理由**：绑定形态由 `create_binding` 决定，
    与本函数无关（需求 P1-AC25：非 Git 目录可正常建立项目）。
    """
    return LocalProject(
        local_project_id=project_id,
        workspace_id=workspace_id,
        name=name,
        goal=goal,
        created_at_commit=created_at_commit,
        revision=revision,
        modules=modules,
    )


def revise_project(project: LocalProject, **changes: object) -> LocalProject:
    """在既有项目上产生**新修订**；旧修订由调用方保留。

    不提供原地修改：`LocalProject` 是冻结对象，历史修订不可被覆盖。
    """
    values: dict[str, object] = {
        "local_project_id": project.local_project_id,
        "workspace_id": project.workspace_id,
        "name": project.name,
        "goal": project.goal,
        "created_at_commit": project.created_at_commit,
        "revision": project.revision + 1,
        "modules": project.modules,
    }
    values.update(changes)
    return LocalProject(**values)  # type: ignore[arg-type]


# ------------------------------------------------------------------ 绑定


def path_is_canonical_absolute(canonical_path: str) -> bool:
    """规范绝对路径校验；**按 Windows 路径语义**，不访问文件系统。

    用 `PureWindowsPath` 而不是 `Path`：后者在 Linux 上是 `PosixPath`，
    会把 ``C:\\work`` 判成相对路径，使同一份代码在不同平台上结论不一致。
    """
    if not canonical_path.strip():
        return False
    path = PureWindowsPath(canonical_path)
    if not path.is_absolute():
        return False
    return not any(part in {".", ".."} for part in path.parts)


@dataclass(frozen=True, slots=True)
class BindingInputs:
    """建立绑定所需的全部输入。

    `binding_form` 决定哪些字段**必须存在**：
    `git` 必须给仓库标识、分支与基准提交；`plain` 必须给清单摘要。
    另一形态的字段传进来即报错，**不用空值假装存在**（需求 P1-AC25）。
    """

    binding_id: str
    project_id: str
    canonical_path: str
    binding_form: BindingForm
    drive_kind: DriveKind
    binding_revision: int = 1
    repository_id: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    manifest_digest: str | None = None
    local_owner: str | None = None
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class BindingResult:
    """绑定尝试的结果：要么得到绑定，要么得到拒绝/缺口，**不会两者都有**。"""

    binding: LocalProjectBinding | None
    rejection: WorkspaceLocationRejection | None = None
    gaps: tuple[ContextGap, ...] = ()

    def __post_init__(self) -> None:
        if self.binding is None and self.rejection is None and not self.gaps:
            raise ValueError("a binding attempt must produce a binding, a rejection or a gap")
        if self.binding is not None and self.rejection is not None:
            raise ValueError("a rejected location must not produce a binding")


def create_binding(inputs: BindingInputs) -> BindingResult:
    """建立绑定修订。

    **同步盘与网络盘拒绝建立并说明原因**：同步器会破坏排他锁与原子替换语义。
    盘符探测属 I/O，由调用方给定 `drive_kind`；`UNKNOWN` 不被擅自拒绝，
    只产生一条**非阻塞**提示（由调用方决定是否据此阻塞）。
    """
    _require_text(inputs.binding_id, "binding_id")
    _require_text(inputs.project_id, "project_id")
    _require_revision(inputs.binding_revision, "binding_revision")
    if not path_is_canonical_absolute(inputs.canonical_path):
        raise ValueError("canonical_path must be a canonical absolute Windows path")

    rejection = reject_workspace_location(inputs.canonical_path, inputs.drive_kind)
    if rejection is not None:
        return BindingResult(binding=None, rejection=rejection)

    if inputs.binding_form is BindingForm.GIT:
        missing = [
            name
            for name, value in (
                ("repository_id", inputs.repository_id),
                ("branch", inputs.branch),
                ("base_commit", inputs.base_commit),
            )
            if value is None
        ]
        if missing:
            return BindingResult(
                binding=None,
                gaps=tuple(
                    ContextGap(
                        kind=GAP_MISSING_ENVIRONMENT_CARRIER,
                        subject=f"binding:{inputs.binding_id}",
                        detail=f"git binding requires {name}",
                    )
                    for name in missing
                ),
            )
    else:
        if inputs.manifest_digest is None:
            return BindingResult(
                binding=None,
                gaps=(
                    ContextGap(
                        kind=GAP_MISSING_ENVIRONMENT_CARRIER,
                        subject=f"binding:{inputs.binding_id}",
                        detail="plain binding requires a source manifest digest",
                    ),
                ),
            )

    binding = LocalProjectBinding(
        binding_id=inputs.binding_id,
        binding_revision=inputs.binding_revision,
        project_id=inputs.project_id,
        canonical_path=inputs.canonical_path,
        binding_form=inputs.binding_form,
        repository_id=inputs.repository_id,
        branch=inputs.branch,
        base_commit=inputs.base_commit,
        manifest_digest=inputs.manifest_digest,
        local_owner=inputs.local_owner,
        confirmed=inputs.confirmed,
    )
    gaps: tuple[ContextGap, ...] = ()
    if inputs.drive_kind is DriveKind.UNKNOWN:
        gaps = (
            ContextGap(
                kind=WARN_UNVERIFIED_DRIVE_KIND,
                subject=f"binding:{inputs.binding_id}",
                detail="the workspace drive kind could not be determined",
                blocking=False,
            ),
        )
    return BindingResult(binding=binding, gaps=gaps)


def move_binding(
    binding: LocalProjectBinding,
    *,
    canonical_path: str,
    drive_kind: DriveKind,
) -> BindingResult:
    """目录移动产生**新绑定修订**；历史修订保留，仍归同一项目。"""
    return create_binding(
        BindingInputs(
            binding_id=binding.binding_id,
            project_id=binding.project_id,
            canonical_path=canonical_path,
            binding_form=binding.binding_form,
            drive_kind=drive_kind,
            binding_revision=binding.binding_revision + 1,
            repository_id=binding.repository_id,
            branch=binding.branch,
            base_commit=binding.base_commit,
            manifest_digest=binding.manifest_digest,
            local_owner=binding.local_owner,
            confirmed=binding.confirmed,
        )
    )


# ------------------------------------------------------------------ 环境


@dataclass(frozen=True, slots=True)
class EnvironmentInputs:
    """建立环境声明所需的输入。

    `interpreter_requirement` 与 `dependency_declaration` 是**载体**：
    缺失时不构造环境对象，而是返回阻塞缺口——不填一个"未知"占位冒充已登记。
    """

    environment_id: str
    interpreter_requirement: str | None
    dependency_declaration: str | None
    revision: int = 1
    isolation_mode: IsolationMode = IsolationMode.VENV
    isolation_confirmed: bool = False
    data_isolated: bool | None = None
    data_reset_policy: str | None = None
    target_deployment_identity: str | None = None
    request_timeout_seconds: int | None = None
    step_timeout_seconds: int | None = None
    network_targets: tuple[str, ...] = field(default_factory=tuple)
    secret_refs: tuple[SecretRef, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class EnvironmentResult:
    environment: EnvironmentRef | None
    gaps: tuple[ContextGap, ...] = ()

    def __post_init__(self) -> None:
        if self.environment is None and not self.gaps:
            raise ValueError("a failed environment construction requires a gap")
        if self.environment is not None and self.gaps:
            raise ValueError("a constructed environment must not carry gaps")


def create_environment(inputs: EnvironmentInputs) -> EnvironmentResult:
    """建立环境**声明**（不是已解析的事实；解析在 prepare 时刻由用例完成）。

    载体缺失即返回**阻塞缺口**：环境缺载体阻塞（需求 P1-FR01 与施工清单第 2 节）。
    显式不隔离是**合法事实**，不在这里被降级或拒绝。
    """
    _require_text(inputs.environment_id, "environment_id")
    _require_revision(inputs.revision, "environment revision")

    missing = tuple(
        ContextGap(
            kind=GAP_MISSING_ENVIRONMENT_CARRIER,
            subject=f"environment:{inputs.environment_id}",
            detail=detail,
        )
        for name, value, detail in (
            (
                "interpreter_requirement",
                inputs.interpreter_requirement,
                "the environment declares no interpreter or runtime carrier",
            ),
            (
                "dependency_declaration",
                inputs.dependency_declaration,
                "the environment declares no dependency carrier",
            ),
        )
        if value is None or not value.strip()
    )
    if missing:
        return EnvironmentResult(environment=None, gaps=missing)

    assert inputs.interpreter_requirement is not None
    assert inputs.dependency_declaration is not None
    return EnvironmentResult(
        environment=EnvironmentRef(
            environment_id=inputs.environment_id,
            revision=inputs.revision,
            interpreter_requirement=inputs.interpreter_requirement,
            dependency_declaration=inputs.dependency_declaration,
            isolation_mode=inputs.isolation_mode,
            isolation_confirmed=inputs.isolation_confirmed,
            data_isolated=inputs.data_isolated,
            data_reset_policy=inputs.data_reset_policy,
            target_deployment_identity=inputs.target_deployment_identity,
            request_timeout_seconds=inputs.request_timeout_seconds,
            step_timeout_seconds=inputs.step_timeout_seconds,
            network_targets=inputs.network_targets,
            secret_refs=inputs.secret_refs,
        )
    )


# ------------------------------------------------------------------ 模块与依赖


def register_graph(
    *, project_id: str, modules: tuple[Module, ...], dependencies: tuple[Dependency, ...]
) -> ModuleDependencyGraph:
    """登记模块与依赖边。

    依赖边是**唯一权威来源**（`Module` 上没有第二个可写的依赖字段）。
    允许循环（只影响传播遍历），拒绝自环；悬空引用由领域构造即拒绝。
    """
    return ModuleDependencyGraph(
        project_id=project_id, modules=modules, dependencies=dependencies
    )


# ------------------------------------------------------------------ 缺口检测


def detect_context_gaps(
    *,
    project: LocalProject,
    graph: ModuleDependencyGraph,
    environment: EnvironmentRef | None,
    drive_kind: DriveKind | None = None,
) -> tuple[ContextGap, ...]:
    """列出项目上下文的缺口；**非空即阻塞准备**。

    逐条依据（每行一条，便于核对）：

    - 项目没有任何模块 → `no_modules`：FR01 需要可执行的模块范围；
    - 有模块但没有登记任何依赖边 → `missing_dependency_registration`：
      P1-FR03 的回归范围需要依赖，**"映射缺失不能解释为没有影响"**；
    - 没有环境 → `missing_environment_carrier`：环境缺载体阻塞；
    - 盘符类型无法核实 → `unverified_drive_kind`（**非阻塞**）：
      `DriveKind.UNKNOWN` 不由领域层擅自拒绝。
    """
    _require_text(project.local_project_id, "project_id")

    gaps: list[ContextGap] = []
    if not project.modules:
        gaps.append(
            ContextGap(
                kind=GAP_NO_MODULES,
                subject=project.local_project_id,
                detail="the project registers no module, so no applicable scope can be derived",
            )
        )
    if graph.modules and not graph.dependencies:
        gaps.append(
            ContextGap(
                kind=GAP_MISSING_DEPENDENCY_REGISTRATION,
                subject=project.local_project_id,
                detail=(
                    "modules are registered but no dependency edge is: a missing mapping "
                    "must not be read as 'no impact'"
                ),
            )
        )
    if environment is None:
        gaps.append(
            ContextGap(
                kind=GAP_MISSING_ENVIRONMENT_CARRIER,
                subject=project.local_project_id,
                detail="no environment is registered for this project",
            )
        )
    if drive_kind is DriveKind.UNKNOWN:
        gaps.append(
            ContextGap(
                kind=WARN_UNVERIFIED_DRIVE_KIND,
                subject=project.local_project_id,
                detail="the workspace drive kind could not be determined",
                blocking=False,
            )
        )
    return tuple(gaps)


__all__ = [
    "GAP_INCOMPLETE_ENVIRONMENT",
    "GAP_MISSING_DEPENDENCY_REGISTRATION",
    "GAP_MISSING_ENVIRONMENT_CARRIER",
    "GAP_NO_MODULES",
    "WARN_UNVERIFIED_DRIVE_KIND",
    "BindingInputs",
    "BindingResult",
    "ContextGap",
    "EnvironmentInputs",
    "EnvironmentResult",
    "blocking_gaps",
    "create_binding",
    "create_environment",
    "create_project",
    "detect_context_gaps",
    "move_binding",
    "path_is_canonical_absolute",
    "register_graph",
    "revise_project",
]
