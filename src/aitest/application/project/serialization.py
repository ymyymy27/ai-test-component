"""项目与绑定的 payload 序列化：不适用键真正省略。

`LocalProjectBinding` 的领域不变量（`aitest.domain.project.context`）规定：

- `git` 形态必须带 `repository_id` / `branch` / `base_commit`，`manifest_digest` 必须为 `None`；
- `plain` 形态前三者必须为 `None`，`manifest_digest` 必填。

**序列化层的责任**是把上述"必须为 `None`"的字段**真正从 payload 里去掉**，
而不是写成空值。需求 P1-AC25 的原文是"界面与报告中不出现仓库、分支、提交、远端任何内容，
**也不显示为空值或"未知"**"——存储里先出现 `null`，下游投影就很容易把它渲染成空值。

`from_payload()` 反向解析时把**缺键与 `null` 同等拒绝**，这样"存时省略、读时补空"
这条不一致路径也会被抓到。

本模块只做纯值映射，不含 I/O，也不决定存储格式（落盘由 A 的记录仓储承接）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aitest.domain.project.context import (
    SCHEMA_VERSION_PROJECT,
    AcceptanceItem,
    BindingForm,
    Delivery,
    Dependency,
    DependencyOrigin,
    EnvironmentRef,
    ImplementationStatus,
    IsolationMode,
    LocalProject,
    LocalProjectBinding,
    Module,
    ModuleDependencyGraph,
    SecretRef,
    SelfReport,
    SourceFileDigest,
    SourceForm,
    SourceManifest,
    Task,
    _canonical_portable_path,
    source_content_identity,
)

#: `git` 形态专有键；`plain` 形态的 payload 中必须**不出现**这些键。
GIT_ONLY_KEYS: tuple[str, ...] = ("repository_id", "branch", "base_commit")

#: `plain` 形态专有键；`git` 形态的 payload 中必须**不出现**。
PLAIN_ONLY_KEYS: tuple[str, ...] = ("manifest_digest",)

_BINDING_COMMON_KEYS: tuple[str, ...] = (
    "schema_version",
    "binding_id",
    "binding_revision",
    "project_id",
    "canonical_path",
    "binding_form",
)


def _binding_key_order(binding: LocalProjectBinding) -> tuple[str, ...]:
    form_keys = GIT_ONLY_KEYS if binding.is_git else PLAIN_ONLY_KEYS
    return _BINDING_COMMON_KEYS + form_keys + ("local_owner", "confirmed")


def binding_to_payload(binding: LocalProjectBinding) -> dict[str, Any]:
    """把绑定写成 payload；**只包含该形态适用的键**。

    `local_owner` 为 `None` 时同样省略：它是可选业务字段，"未登记"与"登记为空"
    没有区别，保留 `null` 只会给下游增加一种要处理的状态。
    """
    payload: dict[str, Any] = {
        "schema_version": "aitest.binding/2.0",
        "binding_id": binding.binding_id,
        "binding_revision": binding.binding_revision,
        "project_id": binding.project_id,
        "canonical_path": binding.canonical_path,
        "binding_form": binding.binding_form.value,
        "confirmed": binding.confirmed,
    }
    if binding.is_git:
        payload["repository_id"] = binding.repository_id
        payload["branch"] = binding.branch
        payload["base_commit"] = binding.base_commit
    else:
        payload["manifest_digest"] = binding.manifest_digest
    if binding.local_owner is not None:
        payload["local_owner"] = binding.local_owner

    for key in GIT_ONLY_KEYS if binding.is_plain else PLAIN_ONLY_KEYS:
        if key in payload:
            raise ValueError(f"a {binding.binding_form.value} binding must omit {key}")
    return {key: payload[key] for key in _binding_key_order(binding) if key in payload}


def binding_from_payload(payload: Mapping[str, Any]) -> LocalProjectBinding:
    """从 payload 还原绑定；**缺键与 `null` 同等拒绝**。"""
    raw_form = payload.get("binding_form")
    if not isinstance(raw_form, str):
        raise ValueError("binding_form must be a string")
    try:
        form = BindingForm(raw_form)
    except ValueError as error:
        raise ValueError(f"unknown binding_form: {raw_form}") from error

    forbidden = PLAIN_ONLY_KEYS if form is BindingForm.GIT else GIT_ONLY_KEYS
    for key in forbidden:
        if key in payload:
            raise ValueError(f"a {form.value} binding must omit {key}")

    required_form_keys = GIT_ONLY_KEYS if form is BindingForm.GIT else PLAIN_ONLY_KEYS
    for key in required_form_keys:
        if key not in payload or payload[key] is None:
            raise ValueError(f"a {form.value} binding requires {key}")

    local_owner = payload.get("local_owner")
    if local_owner is not None and not isinstance(local_owner, str):
        raise ValueError("local_owner must be a string when present")

    canonical_path = payload.get("canonical_path")
    if not isinstance(canonical_path, str):
        raise ValueError("canonical_path must be a string")
    _canonical_portable_path(canonical_path)

    binding_revision = payload.get("binding_revision")
    if not isinstance(binding_revision, int) or isinstance(binding_revision, bool):
        raise ValueError("binding_revision must be an integer")

    confirmed = payload.get("confirmed", False)
    if not isinstance(confirmed, bool):
        raise ValueError("confirmed must be a boolean")

    def _text(key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        return value

    return LocalProjectBinding(
        binding_id=_text("binding_id"),
        binding_revision=binding_revision,
        project_id=_text("project_id"),
        canonical_path=canonical_path,
        binding_form=form,
        repository_id=_text("repository_id") if form is BindingForm.GIT else None,
        branch=_text("branch") if form is BindingForm.GIT else None,
        base_commit=_text("base_commit") if form is BindingForm.GIT else None,
        manifest_digest=_text("manifest_digest") if form is BindingForm.PLAIN else None,
        local_owner=local_owner,
        confirmed=confirmed,
    )


def _module_to_payload(module: Module) -> dict[str, Any]:
    """模块 payload；`owner` 未登记时**省略该键**（可选字段不写空值）。"""
    payload: dict[str, Any] = {
        "module_id": module.module_id,
        "project_id": module.project_id,
        "name": module.name,
        "responsibility": module.responsibility,
        "interface_note": module.interface_note,
        "inputs": list(module.inputs),
        "outputs": list(module.outputs),
        "implementation_status": module.implementation_status.value,
        "revision": module.revision,
    }
    if module.owner is not None:
        payload["owner"] = module.owner
    if module.source_paths:
        # 未登记源码范围时**省略该键**，不写空列表——留空会被下游读成"影响所有文件"。
        payload["source_paths"] = list(module.source_paths)
    return payload


def _module_from_payload(raw: object) -> Module:
    if not isinstance(raw, Mapping):
        raise ValueError("each module entry must be an object")

    def _text(key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str):
            raise ValueError(f"module {key} must be a string")
        return value

    def _text_list(key: str) -> tuple[str, ...]:
        value = raw.get(key, [])
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"module {key} must be a list")
        entries: list[str] = []
        for entry in value:
            if not isinstance(entry, str):
                raise ValueError(f"module {key} must contain strings")
            entries.append(entry)
        return tuple(entries)

    raw_status = _text("implementation_status")
    try:
        status = ImplementationStatus(raw_status)
    except ValueError as error:
        raise ValueError(f"unknown implementation_status: {raw_status}") from error

    revision = raw.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise ValueError("module revision must be an integer")

    owner = raw.get("owner")
    if owner is not None and not isinstance(owner, str):
        raise ValueError("module owner must be a string when present")

    return Module(
        module_id=_text("module_id"),
        project_id=_text("project_id"),
        name=_text("name"),
        responsibility=_text("responsibility"),
        interface_note=_text("interface_note"),
        inputs=_text_list("inputs"),
        outputs=_text_list("outputs"),
        implementation_status=status,
        owner=owner,
        revision=revision,
        source_paths=_text_list("source_paths"),
    )


def project_to_payload(project: LocalProject) -> dict[str, Any]:
    """项目 payload；模块随项目一起序列化，可完整往返。"""
    return {
        "schema_version": SCHEMA_VERSION_PROJECT,
        "local_project_id": project.local_project_id,
        "project_id": project.project_id,
        "workspace_id": project.workspace_id,
        "name": project.name,
        "goal": project.goal,
        "revision": project.revision,
        "created_at_commit": project.created_at_commit,
        "modules": [_module_to_payload(module) for module in project.modules],
    }


def project_from_payload(payload: Mapping[str, Any]) -> LocalProject:
    """从 payload 还原项目，模块一并还原。"""
    raw_modules = payload.get("modules", [])
    if not isinstance(raw_modules, (list, tuple)):
        raise ValueError("modules must be a list")

    def _text(key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        return value

    revision = payload.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise ValueError("revision must be an integer")

    return LocalProject(
        local_project_id=_text("local_project_id"),
        workspace_id=_text("workspace_id"),
        name=_text("name"),
        goal=_text("goal"),
        created_at_commit=_text("created_at_commit"),
        revision=revision,
        modules=tuple(_module_from_payload(raw) for raw in raw_modules),
    )


def _secret_ref_to_payload(secret: SecretRef) -> dict[str, Any]:
    """凭据**引用**的 payload；只落引用，不落正文。"""
    payload: dict[str, Any] = {"purpose": secret.purpose, "env_key": secret.env_key}
    if secret.ref is not None:
        payload["ref"] = secret.ref
    return payload


def environment_to_payload(
    environment: EnvironmentRef, *, project_id: str
) -> dict[str, Any]:
    """环境的 payload；未登记的可选字段**省略该键**，不写 `null` 占位。

    `project_id` 必须写进 payload：记录标识在存储层是全局的，项目范围只能由
    payload 表达；缺了它，按项目范围的有限查询就查不到这条记录。
    `EnvironmentRef` 自身不带项目，因此由调用方显式给出。
    """
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    payload: dict[str, Any] = {
        "project_id": project_id,
        "environment_id": environment.environment_id,
        "revision": environment.revision,
        "interpreter_requirement": environment.interpreter_requirement,
        "dependency_declaration": environment.dependency_declaration,
        "isolation_mode": environment.isolation_mode.value,
        "isolation_confirmed": environment.isolation_confirmed,
        "network_targets": list(environment.network_targets),
        "secret_refs": [
            _secret_ref_to_payload(secret) for secret in environment.secret_refs
        ],
    }
    optional: dict[str, object] = {
        "data_isolated": environment.data_isolated,
        "data_reset_policy": environment.data_reset_policy,
        "target_deployment_identity": environment.target_deployment_identity,
        "request_timeout_seconds": environment.request_timeout_seconds,
        "step_timeout_seconds": environment.step_timeout_seconds,
        "revision_note": environment.revision_note,
    }
    for key, value in optional.items():
        if value is not None:
            payload[key] = value
    return payload


def environment_from_payload(payload: Mapping[str, Any]) -> EnvironmentRef:
    """从 payload 还原环境；**缺键与 `null` 同等拒绝**（可选字段除外）。"""

    def _text(key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return value

    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("environment revision must be an integer >= 1")

    raw_mode = _text("isolation_mode")
    try:
        isolation_mode = IsolationMode(raw_mode)
    except ValueError as error:
        raise ValueError(f"unknown isolation_mode: {raw_mode}") from error

    confirmed = payload.get("isolation_confirmed", False)
    if not isinstance(confirmed, bool):
        raise ValueError("isolation_confirmed must be a boolean")

    secrets: list[SecretRef] = []
    raw_secrets = payload.get("secret_refs", [])
    if not isinstance(raw_secrets, (list, tuple)):
        raise ValueError("secret_refs must be a list")
    for raw in raw_secrets:
        if not isinstance(raw, Mapping):
            raise ValueError("each secret ref must be an object")
        purpose = raw.get("purpose")
        env_key = raw.get("env_key")
        ref = raw.get("ref")
        if not isinstance(purpose, str) or not isinstance(env_key, str):
            raise ValueError("secret ref needs purpose and env_key strings")
        if ref is not None and not isinstance(ref, str):
            raise ValueError("secret ref ref must be a string when present")
        secrets.append(SecretRef(purpose=purpose, env_key=env_key, ref=ref))

    targets = payload.get("network_targets", [])
    if not isinstance(targets, (list, tuple)):
        raise ValueError("network_targets must be a list")
    for entry in targets:
        if not isinstance(entry, str):
            raise ValueError("network_targets must contain strings")

    def _optional_text(key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string when present")
        return value

    def _optional_int(key: str) -> int | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be an integer when present")
        return value

    isolated = payload.get("data_isolated")
    if isolated is not None and not isinstance(isolated, bool):
        raise ValueError("data_isolated must be a boolean when present")

    return EnvironmentRef(
        environment_id=_text("environment_id"),
        revision=revision,
        interpreter_requirement=_text("interpreter_requirement"),
        dependency_declaration=_text("dependency_declaration"),
        isolation_mode=isolation_mode,
        isolation_confirmed=confirmed,
        data_isolated=isolated,
        data_reset_policy=_optional_text("data_reset_policy"),
        target_deployment_identity=_optional_text("target_deployment_identity"),
        request_timeout_seconds=_optional_int("request_timeout_seconds"),
        step_timeout_seconds=_optional_int("step_timeout_seconds"),
        network_targets=tuple(str(entry) for entry in targets),
        secret_refs=tuple(secrets),
        revision_note=_optional_text("revision_note"),
    )


def _dependency_to_payload(dependency: Dependency) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "consumer_module_id": dependency.consumer_module_id,
        "provider_module_id": dependency.provider_module_id,
        "origin": dependency.origin.value,
        "revision": dependency.revision,
    }
    if dependency.source is not None:
        payload["source"] = dependency.source
    return payload


def dependency_graph_to_payload(graph: ModuleDependencyGraph) -> dict[str, Any]:
    """模块依赖图的 payload；项目范围内可完整往返。

    `edges_declared` 一并落盘（检查项 B-17）：它是"已明确没有依赖"与"依赖尚未登记"的唯一
    区分依据，丢了这个布尔值，读回时就只能靠"有没有边"猜，误判不可避免。
    """
    return {
        "project_id": graph.project_id,
        "modules": [_module_to_payload(module) for module in graph.modules],
        "dependencies": [
            _dependency_to_payload(dependency) for dependency in graph.dependencies
        ],
        "edges_declared": graph.edges_declared,
    }


def dependency_graph_from_payload(payload: Mapping[str, Any]) -> ModuleDependencyGraph:
    """从 payload 还原依赖图。

    旧记录（本键之前的版本）没有 `edges_declared`：那时"有模块没有边"一律按缺口处理，
    为了**不改动既有记录的语义**，这类记录读回时视为 `edges_declared=True`（即沿用旧口径、
    不新造缺口），而不是顺手把它们变成阻塞项。
    """
    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id must be a non-empty string")

    raw_modules = payload.get("modules", [])
    if not isinstance(raw_modules, (list, tuple)):
        raise ValueError("modules must be a list")

    raw_dependencies = payload.get("dependencies", [])
    if not isinstance(raw_dependencies, (list, tuple)):
        raise ValueError("dependencies must be a list")

    dependencies: list[Dependency] = []
    for raw in raw_dependencies:
        if not isinstance(raw, Mapping):
            raise ValueError("each dependency entry must be an object")
        origin_raw = raw.get("origin")
        if not isinstance(origin_raw, str):
            raise ValueError("dependency origin must be a string")
        try:
            origin = DependencyOrigin(origin_raw)
        except ValueError as error:
            raise ValueError(f"unknown dependency origin: {origin_raw}") from error
        revision = raw.get("revision", 1)
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ValueError("dependency revision must be an integer")
        consumer = raw.get("consumer_module_id")
        provider = raw.get("provider_module_id")
        if not isinstance(consumer, str) or not isinstance(provider, str):
            raise ValueError("dependency needs consumer and provider ids")
        source = raw.get("source")
        if source is not None and not isinstance(source, str):
            raise ValueError("dependency source must be a string when present")
        dependencies.append(
            Dependency(
                consumer_module_id=consumer,
                provider_module_id=provider,
                origin=origin,
                source=source,
                revision=revision,
            )
        )

    raw_declared = payload.get("edges_declared")
    if raw_declared is None:
        edges_declared = True
    elif isinstance(raw_declared, bool):
        edges_declared = raw_declared
    else:
        raise ValueError("edges_declared must be a boolean when present")

    return ModuleDependencyGraph(
        project_id=project_id,
        modules=tuple(_module_from_payload(raw) for raw in raw_modules),
        dependencies=tuple(dependencies),
        edges_declared=edges_declared,
    )


# ------------------------------------------------------------------ 任务与交付


def _require_payload_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _payload_text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _payload_optional_text(payload: Mapping[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _payload_revision(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value


def _payload_text_tuple(payload: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = payload.get(name, [])
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    entries: list[str] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"{name}[{index}] must be a non-empty string")
        entries.append(entry)
    return tuple(entries)


def acceptance_item_to_payload(item: AcceptanceItem) -> dict[str, Any]:
    return {
        "acceptance_item_id": item.acceptance_item_id,
        "observable_result": item.observable_result,
        "required": item.required,
    }


def acceptance_item_from_payload(raw: object) -> AcceptanceItem:
    payload = _require_payload_mapping(raw, "acceptance item")
    required = payload.get("required", True)
    if not isinstance(required, bool):
        raise ValueError("acceptance item required must be a boolean")
    return AcceptanceItem(
        acceptance_item_id=_payload_text(payload, "acceptance_item_id"),
        observable_result=_payload_text(payload, "observable_result"),
        required=required,
    )


def task_to_payload(task: Task) -> dict[str, Any]:
    """任务落盘形状。

    `acceptance_items` **按声明顺序保留、不排序**：顺序是任务语义的一部分
    （验收项按重要性排列），与"集合"类字段（关联项集合）的处理不同。
    `acceptance_item` **不做成独立记录**：它在领域里没有独立身份、始终属于某个任务，
    单独建记录会出现"同一事实两处保存"（与 `module` 随项目一起落盘同一处理方式）。
    """
    return {
        "project_id": task.project_id,
        "task_id": task.task_id,
        "goal": task.goal,
        "scope": task.scope,
        "acceptance_items": [
            acceptance_item_to_payload(item) for item in task.acceptance_items
        ],
        "inputs": list(task.inputs),
        "outputs": list(task.outputs),
        "preconditions": list(task.preconditions),
        "owner": task.owner,
        "acceptor": task.acceptor,
        "schema_version": task.schema_version,
        "revision": task.revision,
    }


def task_from_payload(payload: Mapping[str, Any]) -> Task:
    raw_items = payload.get("acceptance_items")
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        raise ValueError("task payload must carry at least one acceptance item")
    return Task(
        task_id=_payload_text(payload, "task_id"),
        project_id=_payload_text(payload, "project_id"),
        goal=_payload_text(payload, "goal"),
        scope=_payload_text(payload, "scope"),
        acceptance_items=tuple(
            acceptance_item_from_payload(item) for item in raw_items
        ),
        inputs=_payload_text_tuple(payload, "inputs"),
        outputs=_payload_text_tuple(payload, "outputs"),
        preconditions=_payload_text_tuple(payload, "preconditions"),
        owner=_payload_optional_text(payload, "owner"),
        acceptor=_payload_optional_text(payload, "acceptor"),
        schema_version=_payload_text(payload, "schema_version"),
        revision=_payload_revision(payload, "revision"),
    )


def delivery_to_payload(delivery: Delivery, *, project_id: str) -> dict[str, Any]:
    """交付说明落盘形状：**自述与验证事实结构分离**（需求 P1-FR02）。

    两者字段各自独立成块，读回时也各自重建——不给"把自述当验证事实"留通道。

    `project_id` 写在**记录正文里**，不只留在记录的命名空间（检查项 B-12）：
    `Delivery` 领域对象本身不带项目字段，正文又没有项目时，这条记录就成了
    "归属未知"，任何项目都能按同一个 `delivery_id` 读回。项目由调用方显式给出，
    与 `acceptance_scope`/`case`/`rule_draft` 的既有做法一致。
    """
    return {
        "project_id": project_id,
        "delivery_id": delivery.delivery_id,
        "task_id": delivery.task_id,
        "version": delivery.version,
        "run_method": delivery.run_method,
        "self_report": {
            "completed": list(delivery.self_report.completed),
            "incomplete": list(delivery.self_report.incomplete),
        },
        "verified_in_scope": list(delivery.verified_in_scope),
        "unverified_scope": list(delivery.unverified_scope),
        "changed_modules": list(delivery.changed_modules),
        "api_changes": list(delivery.api_changes),
        "test_data": list(delivery.test_data),
        "dependencies": list(delivery.dependencies),
        "mock_declarations": list(delivery.mock_declarations),
        "known_issues": list(delivery.known_issues),
        "self_test_evidence": list(delivery.self_test_evidence),
        "submitted_by": delivery.submitted_by,
        "schema_version": delivery.schema_version,
        "revision": delivery.revision,
    }


def delivery_from_payload(payload: Mapping[str, Any]) -> Delivery:
    raw_report = _require_payload_mapping(
        payload.get("self_report"), "self_report"
    )
    return Delivery(
        delivery_id=_payload_text(payload, "delivery_id"),
        task_id=_payload_text(payload, "task_id"),
        version=_payload_text(payload, "version"),
        run_method=_payload_text(payload, "run_method"),
        self_report=SelfReport(
            completed=_payload_text_tuple(raw_report, "completed"),
            incomplete=_payload_text_tuple(raw_report, "incomplete"),
        ),
        verified_in_scope=_payload_text_tuple(payload, "verified_in_scope"),
        unverified_scope=_payload_text_tuple(payload, "unverified_scope"),
        changed_modules=_payload_text_tuple(payload, "changed_modules"),
        api_changes=_payload_text_tuple(payload, "api_changes"),
        test_data=_payload_text_tuple(payload, "test_data"),
        dependencies=_payload_text_tuple(payload, "dependencies"),
        mock_declarations=_payload_text_tuple(payload, "mock_declarations"),
        known_issues=_payload_text_tuple(payload, "known_issues"),
        self_test_evidence=_payload_text_tuple(payload, "self_test_evidence"),
        submitted_by=_payload_optional_text(payload, "submitted_by"),
        schema_version=_payload_text(payload, "schema_version"),
        revision=_payload_revision(payload, "revision"),
    )


# ----------------------------------------------------- 源码内容身份（SourceManifest）


def source_manifest_to_payload(
    manifest: SourceManifest, *, project_id: str, snapshot_id: str, purpose: str
) -> dict[str, Any]:
    """源码快照的落盘 payload。

    **形式互斥**：`git` 形态**真正省略** `manifest_digest` 键，
    `plain` 形态**真正省略** `git_base_commit` / `git_diff_digest` 键
    （不是写 `None`、不是写空串——与绑定序列化同一做法）。

    `content_identity` 一并落盘：读回时据此核对"这份快照的身份没被改过"，
    而不是重新扫描目录（端口语义见 `AB-001` 第 11.4 节）。
    """
    payload: dict[str, Any] = {
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "purpose": purpose,
        "source_scope": manifest.source_scope,
        "source_form": manifest.source_form.value,
        "content_identity": source_content_identity(manifest),
        "files": [
            {
                "relative_path": item.relative_path,
                "size": item.size,
                "content_digest": item.content_digest,
            }
            for item in manifest.normalized_files()
        ],
        "exclusion_rules": list(manifest.exclusion_rules),
        "refetch_dependencies": list(manifest.refetch_dependencies),
        "refetch_scope": manifest.refetch_scope,
    }
    if manifest.source_form is SourceForm.GIT:
        payload["git_base_commit"] = manifest.git_base_commit
        payload["git_diff_digest"] = manifest.git_diff_digest
    else:
        payload["plain_manifest_digest"] = manifest.manifest_digest
    return payload


def source_manifest_from_payload(payload: Mapping[str, Any]) -> SourceManifest:
    """从落盘 payload 还原源码内容身份；形态字段缺失即报错（不用空值假装存在）。"""
    raw_form = _payload_text(payload, "source_form")
    try:
        form = SourceForm(raw_form)
    except ValueError as error:
        raise ValueError(f"unknown source_form: {raw_form}") from error

    raw_files = payload.get("files")
    if not isinstance(raw_files, (list, tuple)):
        raise ValueError("files must be a list")
    files: list[SourceFileDigest] = []
    for index, item in enumerate(raw_files):
        entry = _require_payload_mapping(item, f"files[{index}]")
        files.append(
            SourceFileDigest(
                relative_path=_payload_text(entry, "relative_path"),
                size=_payload_non_negative_int(entry, "size"),
                content_digest=_payload_text(entry, "content_digest"),
            )
        )

    common: dict[str, Any] = {
        "source_scope": _payload_text(payload, "source_scope"),
        "source_form": form,
        "files": tuple(files),
        "exclusion_rules": _payload_text_tuple(payload, "exclusion_rules"),
        "refetch_dependencies": _payload_text_tuple(payload, "refetch_dependencies"),
        "refetch_scope": _payload_optional_text(payload, "refetch_scope"),
    }
    if form is SourceForm.GIT:
        # 形态互斥：git 形态下 `manifest_digest` 键必须**不出现**。
        if "plain_manifest_digest" in payload:
            raise ValueError("a git source payload must omit plain_manifest_digest")
        return SourceManifest(
            **common,
            git_base_commit=_payload_text(payload, "git_base_commit"),
            git_diff_digest=_payload_text(payload, "git_diff_digest"),
        )
    if "git_base_commit" in payload or "git_diff_digest" in payload:
        raise ValueError("a plain source payload must omit the git identity keys")
    return SourceManifest(
        **common,
        manifest_digest=_payload_text(payload, "plain_manifest_digest"),
    )


def _payload_non_negative_int(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


__all__ = [
    "GIT_ONLY_KEYS",
    "PLAIN_ONLY_KEYS",
    "acceptance_item_from_payload",
    "acceptance_item_to_payload",
    "binding_from_payload",
    "binding_to_payload",
    "delivery_from_payload",
    "delivery_to_payload",
    "dependency_graph_from_payload",
    "dependency_graph_to_payload",
    "environment_from_payload",
    "environment_to_payload",
    "project_from_payload",
    "project_to_payload",
    "source_manifest_from_payload",
    "source_manifest_to_payload",
    "task_from_payload",
    "task_to_payload",
]
