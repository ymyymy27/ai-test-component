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
    BindingForm,
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
    _canonical_portable_path,
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
    """模块依赖图的 payload；项目范围内可完整往返。"""
    return {
        "project_id": graph.project_id,
        "modules": [_module_to_payload(module) for module in graph.modules],
        "dependencies": [
            _dependency_to_payload(dependency) for dependency in graph.dependencies
        ],
    }


def dependency_graph_from_payload(payload: Mapping[str, Any]) -> ModuleDependencyGraph:
    """从 payload 还原依赖图。"""
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

    return ModuleDependencyGraph(
        project_id=project_id,
        modules=tuple(_module_from_payload(raw) for raw in raw_modules),
        dependencies=tuple(dependencies),
    )


__all__ = [
    "GIT_ONLY_KEYS",
    "PLAIN_ONLY_KEYS",
    "binding_from_payload",
    "binding_to_payload",
    "dependency_graph_from_payload",
    "dependency_graph_to_payload",
    "environment_from_payload",
    "environment_to_payload",
    "project_from_payload",
    "project_to_payload",
]
