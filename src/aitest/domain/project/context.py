"""Project, module, dependency, environment and delivery domain models.

设计依据：`docs/文档-feix-a/B包/05-项目上下文领域层设计说明.md`
（一期架构文档《01-项目与计划》第 1、2、3、7 节）。

本模块只使用标准库，零 I/O：
不读目录、不探测盘符、不算文件摘要。凡需 I/O 的判定，这里只提供纯规则，
事实由调用方传入（见 `reject_workspace_location`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

SCHEMA_VERSION_PROJECT = "aitest.project/2.0"
SCHEMA_VERSION_TASK = "aitest.task/2.0"
SCHEMA_VERSION_DELIVERY = "aitest.delivery/2.0"


def _require_text(value: str, name: str) -> None:
    """空文本不是合法取值；不适用一律用 ``None``。"""
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_items(values: tuple[str, ...], name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must not contain empty values")


def _require_unique(values: tuple[str, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


def _canonical_portable_path(raw: str) -> str:
    """校验规范绝对路径文本；不访问文件系统。

    Windows 上 `Path` 即 `WindowsPath`，同时接受 ``\\`` 与 ``/`` 作为分隔符。
    相对路径与含 ``.``／``..`` 段的路径不是规范路径。
    """
    _require_text(raw, "canonical_path")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("canonical_path must be absolute")
    if any(part in {".", ".."} for part in path.parts):
        raise ValueError("canonical_path must not contain '.' or '..' segments")
    return raw


# --------------------------------------------------------------------- 词汇表


class BindingForm(StrEnum):
    """绑定形态。由绑定是否含仓库标识决定，不靠"取不到值"判断。

    值集合与 `contracts/prepared_run.py` 的 `BindingFormFact` 一致，
    由 `tests/contracts/test_project_vocabulary.py` 锁定。
    """

    GIT = "git"
    PLAIN = "plain"


class IsolationMode(StrEnum):
    """依赖环境隔离方式。

    `NONE` 表示用户**显式选择不隔离**，是合法事实，不等同于"缺配置"；
    需求 P1-FR07 要求它冻结于运行且不自动降低证据等级。
    """

    VENV = "venv"
    NONE = "none"
    UNMANAGED = "unmanaged"


class DriveKind(StrEnum):
    """路径所在位置的类型；由调用方探测后传入。"""

    FIXED = "fixed"
    REMOVABLE = "removable"
    NETWORK = "network"
    SYNC_FOLDER = "sync_folder"
    UNKNOWN = "unknown"


class WorkspaceLocationRejection(StrEnum):
    """拒绝在该位置建立工作空间的原因。拒绝必须给出原因，不只给布尔值。"""

    NETWORK_DRIVE = "network_drive"
    SYNC_FOLDER = "sync_folder"


class ImplementationStatus(StrEnum):
    UNIMPLEMENTED = "unimplemented"
    PARTIAL = "partial"
    COMPLETE = "complete"
    DEPRECATED = "deprecated"


class DependencyOrigin(StrEnum):
    """依赖边来源。`INFERRED` 必须在 FR03 中标注"推导结果，可能不完整"。"""

    REGISTERED = "registered"
    INFERRED = "inferred"


# ----------------------------------------------------------------- 项目与绑定


@dataclass(frozen=True, slots=True)
class LocalProject:
    """本地项目。`local_project_id` 与对外 DTO 的 `project_id` 是同一值。

    路径只是可修订的绑定属性，不是业务主键：显示名与路径变化不改变历史身份。
    """

    local_project_id: str
    workspace_id: str
    name: str
    goal: str
    created_at_commit: str
    revision: int = 1
    modules: tuple[Module, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION_PROJECT

    def __post_init__(self) -> None:
        _require_text(self.local_project_id, "local_project_id")
        _require_text(self.workspace_id, "workspace_id")
        _require_text(self.name, "project name")
        _require_text(self.goal, "project goal")
        _require_text(self.created_at_commit, "created_at_commit")
        if self.revision < 1:
            raise ValueError("project revision must be >= 1")
        _require_unique(tuple(module.module_id for module in self.modules), "module_id")
        for module in self.modules:
            if module.project_id != self.local_project_id:
                raise ValueError("module project_id must match its project")

    @property
    def project_id(self) -> str:
        """对外身份别名：与 `local_project_id` 是同一值，不是第二个编号。"""
        return self.local_project_id

    def module(self, module_id: str) -> Module:
        for module in self.modules:
            if module.module_id == module_id:
                return module
        raise ValueError(f"unknown module_id: {module_id}")


@dataclass(frozen=True, slots=True)
class LocalProjectBinding:
    """项目与目录的绑定修订。

    目录移动或绑定形态变化创建**新修订**，历史修订保留。
    `binding_form` 决定仓库字段的存在性：`git` 必须具备仓库标识、分支与基准提交；
    `plain` 三者必须为 `None`（序列化时真正省略键，不用空值假装存在）。
    """

    binding_id: str
    binding_revision: int
    project_id: str
    canonical_path: str
    binding_form: BindingForm
    repository_id: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    manifest_digest: str | None = None
    local_owner: str | None = None
    confirmed: bool = False

    def __post_init__(self) -> None:
        _require_text(self.binding_id, "binding_id")
        _require_text(self.project_id, "project_id")
        if self.binding_revision < 1:
            raise ValueError("binding_revision must be >= 1")
        _canonical_portable_path(self.canonical_path)
        if self.local_owner is not None:
            _require_text(self.local_owner, "local_owner")
        if self.binding_form is BindingForm.GIT:
            for value, name in (
                (self.repository_id, "repository_id"),
                (self.branch, "branch"),
                (self.base_commit, "base_commit"),
            ):
                if value is None:
                    raise ValueError(f"git binding requires {name}")
                _require_text(value, name)
            if self.manifest_digest is not None:
                raise ValueError("git binding must not carry a plain manifest digest")
        elif self.binding_form is BindingForm.PLAIN:
            for value, name in (
                (self.repository_id, "repository_id"),
                (self.branch, "branch"),
                (self.base_commit, "base_commit"),
            ):
                if value is not None:
                    raise ValueError(f"plain binding must omit {name}")
            if self.manifest_digest is None:
                raise ValueError("plain binding requires manifest_digest")
            _require_text(self.manifest_digest, "manifest_digest")
        else:  # pragma: no cover - 枚举已穷尽
            raise ValueError(f"unmapped binding form: {self.binding_form}")

    @property
    def is_git(self) -> bool:
        return self.binding_form is BindingForm.GIT

    @property
    def is_plain(self) -> bool:
        return self.binding_form is BindingForm.PLAIN


def reject_workspace_location(
    canonical_path: str, drive_kind: DriveKind
) -> WorkspaceLocationRejection | None:
    """同步盘／网络盘不能作为活动数据工作空间（需求 P1-FR01）。

    同步器会破坏排他锁与原子替换语义，导致单写保护与崩溃一致性失效。
    返回拒绝原因；可以建立时返回 `None`。

    `UNKNOWN` 不在这里被擅自拒绝——是否因此阻塞由调用方决定。
    盘符与同步目录的探测属 I/O，由调用方完成。
    """
    _canonical_portable_path(canonical_path)
    if drive_kind is DriveKind.NETWORK:
        return WorkspaceLocationRejection.NETWORK_DRIVE
    if drive_kind is DriveKind.SYNC_FOLDER:
        return WorkspaceLocationRejection.SYNC_FOLDER
    return None


# ----------------------------------------------------------------- 模块与依赖


@dataclass(frozen=True, slots=True)
class Module:
    """登记职责、输入输出、接口说明与实现状态的模块。"""

    module_id: str
    project_id: str
    name: str
    responsibility: str
    interface_note: str
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)
    implementation_status: ImplementationStatus = ImplementationStatus.UNIMPLEMENTED
    owner: str | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        _require_text(self.module_id, "module_id")
        _require_text(self.project_id, "project_id")
        _require_text(self.name, "module name")
        _require_text(self.responsibility, "module responsibility")
        _require_text(self.interface_note, "module interface_note")
        if self.revision < 1:
            raise ValueError("module revision must be >= 1")
        _require_items(self.inputs, "inputs")
        _require_items(self.outputs, "outputs")
        if self.owner is not None:
            _require_text(self.owner, "owner")


@dataclass(frozen=True, slots=True)
class Dependency:
    """依赖边：**使用者 → 提供者**（需求 P1-FR03）。

    允许循环：循环只影响传播遍历，不能阻止建立项目。
    自环无意义，直接拒绝。
    """

    consumer_module_id: str
    provider_module_id: str
    origin: DependencyOrigin = DependencyOrigin.REGISTERED
    source: str | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        _require_text(self.consumer_module_id, "consumer_module_id")
        _require_text(self.provider_module_id, "provider_module_id")
        if self.consumer_module_id == self.provider_module_id:
            raise ValueError("a module must not depend on itself")
        if self.revision < 1:
            raise ValueError("dependency revision must be >= 1")
        if self.origin is DependencyOrigin.INFERRED and self.source is None:
            raise ValueError("inferred dependency requires a source")
        if self.source is not None:
            _require_text(self.source, "source")

    @property
    def is_inferred(self) -> bool:
        return self.origin is DependencyOrigin.INFERRED


@dataclass(frozen=True, slots=True)
class ModuleDependencyGraph:
    """模块与依赖边的只读投影。

    `Dependency` 记录是依赖关系的唯一权威来源；本对象只做投影，
    不提供第二处可写入口（见设计说明第 4 节决定 1）。
    """

    project_id: str
    modules: tuple[Module, ...] = field(default_factory=tuple)
    dependencies: tuple[Dependency, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text(self.project_id, "project_id")
        _require_unique(tuple(m.module_id for m in self.modules), "module_id")
        for module in self.modules:
            if module.project_id != self.project_id:
                raise ValueError("module project_id must match the graph project")
        known = {module.module_id for module in self.modules}
        for edge in self.dependencies:
            for endpoint in (edge.consumer_module_id, edge.provider_module_id):
                if endpoint not in known:
                    raise ValueError(f"dependency references unknown module: {endpoint}")
            if edge.revision < 1:
                raise ValueError("dependency revision must be >= 1")

    def provider_ids(self) -> dict[str, tuple[str, ...]]:
        """投影为 ``{使用者: (提供者, ...)}``，供回归传播函数使用。

        同一对模块重复登记只保留一条，顺序按首次出现，保证结果确定性。
        """
        collected: dict[str, list[str]] = {}
        for edge in self.dependencies:
            providers = collected.setdefault(edge.consumer_module_id, [])
            if edge.provider_module_id not in providers:
                providers.append(edge.provider_module_id)
        return {consumer: tuple(providers) for consumer, providers in collected.items()}

    def inferred_edges(self) -> tuple[Dependency, ...]:
        """仅由推导得出、必须在报告中标注"可能不完整"的边。"""
        return tuple(edge for edge in self.dependencies if edge.is_inferred)


def affected_modules(
    changed: frozenset[str], dependencies: Mapping[str, Sequence[str]]
) -> frozenset[str]:
    """沿反向依赖计算受影响的使用者集合。

    与 `aitest.application.planning.regression.affected_modules` 同义；此处直接接受
    `ModuleDependencyGraph.provider_ids()` 的结果，便于领域内自洽使用。
    """
    affected = set(changed)
    pending = list(changed)
    reverse: dict[str, set[str]] = {}
    for consumer, providers in dependencies.items():
        for provider in providers:
            reverse.setdefault(provider, set()).add(consumer)
    while pending:
        for consumer in reverse.get(pending.pop(), set()):
            if consumer not in affected:
                affected.add(consumer)
                pending.append(consumer)
    return frozenset(affected)


# ----------------------------------------------------------------- 环境与凭据


@dataclass(frozen=True, slots=True)
class SecretRef:
    """按用途的凭据**引用**。

    本对象在类型层面无法表达凭据正文：凭据正文不进配置、日志、面板、导出。
    字段集合由测试锁定，防止后续新增承载正文的字段。
    """

    purpose: str
    env_key: str
    ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.purpose, "purpose")
        _require_text(self.env_key, "env_key")
        if self.ref is not None:
            _require_text(self.ref, "ref")


@dataclass(frozen=True, slots=True)
class EnvironmentRef:
    """环境的**声明**与策略，不是已解析的事实。

    解释器实际身份、依赖集合摘要等值在 prepare 时刻由应用用例解析产生
    （见 `ResolvedEnvironment`，Sprint 2），因此不在本对象上。
    """

    environment_id: str
    revision: int
    interpreter_requirement: str
    dependency_declaration: str
    isolation_mode: IsolationMode = IsolationMode.VENV
    isolation_confirmed: bool = False
    data_isolated: bool | None = None
    data_reset_policy: str | None = None
    target_deployment_identity: str | None = None
    request_timeout_seconds: int | None = None
    step_timeout_seconds: int | None = None
    network_targets: tuple[str, ...] = field(default_factory=tuple)
    secret_refs: tuple[SecretRef, ...] = field(default_factory=tuple)
    revision_note: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.environment_id, "environment_id")
        if self.revision < 1:
            raise ValueError("environment revision must be >= 1")
        _require_text(self.interpreter_requirement, "interpreter_requirement")
        _require_text(self.dependency_declaration, "dependency_declaration")
        if self.isolation_mode is not IsolationMode.VENV and not self.isolation_confirmed:
            raise ValueError("a non-default isolation mode must be explicitly confirmed")
        for timeout, timeout_name in (
            (self.request_timeout_seconds, "request_timeout_seconds"),
            (self.step_timeout_seconds, "step_timeout_seconds"),
        ):
            if timeout is not None and timeout <= 0:
                raise ValueError(f"{timeout_name} must be positive when set")
        for optional_text, optional_name in (
            (self.data_reset_policy, "data_reset_policy"),
            (self.target_deployment_identity, "target_deployment_identity"),
            (self.revision_note, "revision_note"),
        ):
            if optional_text is not None:
                _require_text(optional_text, optional_name)
        _require_items(self.network_targets, "network_targets")
        _require_unique(self.network_targets, "network_targets")
        _require_unique(tuple(ref.env_key for ref in self.secret_refs), "secret env_key")

    @property
    def isolation_is_explicitly_disabled(self) -> bool:
        """显式不隔离是合法事实，不等于缺配置。"""
        return self.isolation_mode is not IsolationMode.VENV


@dataclass(frozen=True, slots=True)
class ResolvedEnvironment:
    """prepare 时刻解析出的环境事实；与 `contracts.EnvironmentRefFact` 同构。

    由应用用例产生（Sprint 2），领域层不计算结果。
    """

    environment_id: str
    revision: int
    isolation_mode: IsolationMode
    interpreter_identity: str
    dependency_set_digest: str

    def __post_init__(self) -> None:
        _require_text(self.environment_id, "environment_id")
        if self.revision < 1:
            raise ValueError("environment revision must be >= 1")
        _require_text(self.interpreter_identity, "interpreter_identity")
        _require_text(self.dependency_set_digest, "dependency_set_digest")


# ----------------------------------------------------- 源码内容身份（plain 最小集）


@dataclass(frozen=True, slots=True)
class SourceFileDigest:
    """单个文件的内容身份。

    `mtime_hint` 只是变化提示：任何"文件相同"的判定都不得只依据它。
    """

    relative_path: str
    size: int
    content_digest: str
    mtime_hint: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.relative_path, "relative_path")
        path = Path(self.relative_path)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            raise ValueError("relative_path must stay inside the source scope")
        if self.size < 0:
            raise ValueError("size must be non-negative")
        _require_text(self.content_digest, "content_digest")
        if self.mtime_hint is not None:
            _require_text(self.mtime_hint, "mtime_hint")


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """`plain` 形态的内容身份：文件清单摘要与复取范围。

    `git` 形态的基准提交与差异摘要属于源码快照对象，其归属待裁定，
    不在本 Sprint 定义（见设计说明第 1.2 节）。
    """

    source_scope: str
    manifest_digest: str
    files: tuple[SourceFileDigest, ...] = field(default_factory=tuple)
    exclusion_rules: tuple[str, ...] = field(default_factory=tuple)
    refetch_dependencies: tuple[str, ...] = field(default_factory=tuple)
    refetch_scope: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.source_scope, "source_scope")
        _require_text(self.manifest_digest, "manifest_digest")
        _require_unique(tuple(f.relative_path for f in self.files), "relative_path")
        _require_items(self.exclusion_rules, "exclusion_rules")
        _require_items(self.refetch_dependencies, "refetch_dependencies")
        if self.refetch_scope is not None:
            _require_text(self.refetch_scope, "refetch_scope")


# --------------------------------------------------------- 任务、验收项与交付


@dataclass(frozen=True, slots=True)
class AcceptanceItem:
    acceptance_item_id: str
    observable_result: str
    required: bool = True

    def __post_init__(self) -> None:
        _require_text(self.acceptance_item_id, "acceptance_item_id")
        _require_text(self.observable_result, "observable_result")


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    project_id: str
    goal: str
    scope: str
    acceptance_items: tuple[AcceptanceItem, ...]
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)
    preconditions: tuple[str, ...] = field(default_factory=tuple)
    owner: str | None = None
    acceptor: str | None = None
    schema_version: str = SCHEMA_VERSION_TASK
    revision: int = 1

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.project_id, "project_id")
        _require_text(self.goal, "task goal")
        _require_text(self.scope, "task scope")
        if not self.acceptance_items:
            raise ValueError("task must contain at least one acceptance item")
        _require_unique(
            tuple(item.acceptance_item_id for item in self.acceptance_items),
            "acceptance_item_id",
        )
        if self.revision < 1:
            raise ValueError("task revision must be >= 1")
        _require_items(self.inputs, "inputs")
        _require_items(self.outputs, "outputs")
        _require_items(self.preconditions, "preconditions")
        for value, name in ((self.owner, "owner"), (self.acceptor, "acceptor")):
            if value is not None:
                _require_text(value, name)


@dataclass(frozen=True, slots=True)
class SelfReport:
    """开发方自述，**不是证据**。自述完成不等于测试验证完成。"""

    completed: tuple[str, ...] = field(default_factory=tuple)
    incomplete: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_items(self.completed, "completed")
        _require_items(self.incomplete, "incomplete")
        if set(self.completed) & set(self.incomplete):
            raise ValueError("completed and incomplete items must not overlap")


@dataclass(frozen=True, slots=True)
class Delivery:
    """交付说明：自述与验证事实**结构分离**（需求 P1-FR02）。

    `verified_in_scope` 只能来自执行事实。本对象不提供把自述复制进
    `verified_in_scope` 的构造路径，也不因 `self_report.completed` 非空而使其非空。
    """

    delivery_id: str
    task_id: str
    version: str
    run_method: str
    self_report: SelfReport = field(default_factory=SelfReport)
    verified_in_scope: tuple[str, ...] = field(default_factory=tuple)
    unverified_scope: tuple[str, ...] = field(default_factory=tuple)
    changed_modules: tuple[str, ...] = field(default_factory=tuple)
    api_changes: tuple[str, ...] = field(default_factory=tuple)
    test_data: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    mock_declarations: tuple[str, ...] = field(default_factory=tuple)
    known_issues: tuple[str, ...] = field(default_factory=tuple)
    self_test_evidence: tuple[str, ...] = field(default_factory=tuple)
    submitted_by: str | None = None
    schema_version: str = SCHEMA_VERSION_DELIVERY
    revision: int = 1

    def __post_init__(self) -> None:
        _require_text(self.delivery_id, "delivery_id")
        _require_text(self.task_id, "task_id")
        _require_text(self.version, "version")
        _require_text(self.run_method, "run_method")
        if self.revision < 1:
            raise ValueError("delivery revision must be >= 1")
        if set(self.verified_in_scope) & set(self.unverified_scope):
            raise ValueError("verified and unverified scope must not overlap")
        for name in (
            "verified_in_scope",
            "unverified_scope",
            "changed_modules",
            "api_changes",
            "test_data",
            "dependencies",
            "mock_declarations",
            "known_issues",
            "self_test_evidence",
        ):
            _require_items(getattr(self, name), name)
        if self.submitted_by is not None:
            _require_text(self.submitted_by, "submitted_by")

    @property
    def completed(self) -> tuple[str, ...]:
        """兼容既有字段：转发到自述，语义仍是"开发方称已完成"。"""
        return self.self_report.completed

    @property
    def incomplete(self) -> tuple[str, ...]:
        """兼容既有字段：转发到自述。"""
        return self.self_report.incomplete

    @property
    def has_self_reported_completion(self) -> bool:
        return bool(self.self_report.completed)

    @property
    def is_verified(self) -> bool:
        """有已验证范围才算测试验证完成；与自述无关。"""
        return bool(self.verified_in_scope)
