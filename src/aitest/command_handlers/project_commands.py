"""项目建档、配置读写、模块登记与 AI 模块推荐命令处理器。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aitest.command_handlers._common import (
    Payload,
    _config_path,
    _find_secret_field,
    _probe_writable,
    _project,
    _project_map_path,
    _project_secrets,
    _require,
    _strip_secret_fields,
    _text,
    cmd_facade_attr,
)
from aitest.config import known_secret_values
from aitest.core.modules import build_context as build_module_context
from aitest.core.modules import invalid_dependencies
from aitest.core.project_context import build_project_context
from aitest.core.shared_config import (
    extract_conventions,
    load_shared_repo_config,
    merge_project_configs,
)
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import build_client
from aitest.models import Dependency, Module, Project, new_id
from aitest.security import redact, redact_tree
from aitest.storage.files import check_schema_version, read_json, write_json
from aitest.storage.workspace import Workspace

CONFIG_SECTIONS: tuple[str, ...] = (
    "model",
    "services",
    "link",
    "runners",
    "evidence",
    "l1",
    "data",
    "environments",
    "conventions",
)
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def cmd_project_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    root = Path(_text({"root": _require(payload, "root")}, "root")).expanduser().resolve()
    if not root.is_dir():
        raise invalid_path(f"目录不存在：{root}")
    writable, writable_error = _probe_writable(root)
    if not writable:
        raise invalid_path(f"目录不可写：{root}；{writable_error}")
    existing = ws.find_by_root(str(root))
    if existing is not None:
        return {
            "project_id": existing.project_id,
            "revision": existing.revision,
            "modules": [module.model_dump(mode="json") for module in existing.modules],
            "existing": True,
        }
    overlap = ws.find_overlapping(str(root))
    if overlap:
        raise invalid_request(
            "根目录与已登记项目重叠：" + "、".join(item.project_id for item in overlap),
            "明确选择要登记的项目根目录",
        )
    secrets = known_secret_values([])
    project = Project(
        project_id=new_id("proj"),
        name=redact(_text(payload, "name") or root.name, secrets),
        root=str(root),
        goal=redact(_text(payload, "goal"), secrets),
    )
    ws.save_project(project)
    return {
        "project_id": project.project_id,
        "revision": project.revision,
        "modules": [],
        "existing": False,
    }


def cmd_project_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del payload, confirm
    return {
        "projects": [
            {
                "project_id": p.project_id,
                "name": p.name,
                "root": p.root,
                "revision": p.revision,
                "updated_at": p.updated_at,
                "archived": p.archived,
            }
            for p in ws.list_projects()
        ]
    }


def cmd_project_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    gaps: list[dict[str, str]] = []
    map_path = _project_map_path(ws, project.project_id)
    if map_path.is_file():
        try:
            record = read_json(map_path)
        except AiTestError:
            record = {}
        unknowns = record.get("unknowns") if isinstance(record, dict) else None
        if isinstance(unknowns, list):
            for entry in unknowns:
                if isinstance(entry, dict):
                    area = str(entry.get("area") or "").strip()
                    reason = str(entry.get("reason") or "").strip()
                    if area:
                        gaps.append({"area": area, "reason": reason or "未说明"})
    return {
        "project": project.model_dump(mode="json"),
        "modules": [module.model_dump(mode="json") for module in project.modules],
        "binding": {"root": project.root},
        "gaps": gaps,
    }


def cmd_project_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    expected = payload.get("expected_revision")
    if expected is not None and expected != project.revision:
        raise AiTestError("RUN_STATE_CONFLICT", "项目修订号不匹配", "刷新后重试")
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        raise invalid_request("patch 必须是对象")
    unknown = sorted(set(patch) - {"name", "goal", "archived"})
    if unknown:
        raise invalid_request(
            "project.update 不支持这些字段：" + "、".join(unknown),
            "只允许修改 name、goal 与 archived",
        )
    data = project.model_dump(mode="json")
    secrets = _project_secrets(ws, project.project_id)
    for key in ("name", "goal"):
        if key in patch:
            value = patch[key]
            if not isinstance(value, str):
                raise invalid_request(f"字段必须是字符串：{key}")
            data[key] = redact(value, secrets)
    if "archived" in patch:
        value = patch["archived"]
        if not isinstance(value, bool):
            raise invalid_request("archived 必须是布尔值")
        data["archived"] = value
    project = Project.model_validate(data)
    project.revision += 1
    ws.save_project(project)
    return {"project_id": project.project_id, "revision": project.revision}


def _read_project_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = read_json(path)
    check_schema_version(data, path)
    return data


def _merge_config(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge_config(current, value)
        else:
            merged[key] = value
    return merged


def _whitelisted_config(data: dict[str, Any]) -> dict[str, Any]:
    return {section: data[section] for section in CONFIG_SECTIONS if section in data}


def _validate_config_numbers(merged: dict[str, Any]) -> None:
    evidence = merged.get("evidence")
    if isinstance(evidence, dict):
        for key in (
            "singleFileMb",
            "mediaMb",
            "single_file_mb",
            "file_cap_mb",
            "media_budget_mb",
        ):
            value = evidence.get(key)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0
            ):
                raise invalid_request(
                    f"evidence.{key} 必须是大于 0 的数字",
                    "证据上限按 MB 填写",
                )
    services = merged.get("services")
    if isinstance(services, dict):
        port = services.get("port")
        if port is not None and (
            not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
        ):
            raise invalid_request("services.port 必须是 1–65535 的整数", "端口范围 1–65535")
        for key in ("timeoutSeconds", "timeout_seconds"):
            value = services.get(key)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0
            ):
                raise invalid_request(f"services.{key} 必须是大于 0 的秒数", "超时按秒填写")


def cmd_project_config(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Read or merge project.config.json without ever returning credential bodies."""
    del confirm
    project = _project(ws, payload)
    action = _text(payload, "action", "get")
    path = _config_path(ws, project.project_id)
    raw = _read_project_config(path)
    existing = _strip_secret_fields(raw)
    if action == "get":
        shared = _strip_secret_fields(load_shared_repo_config(Path(project.root)))
        effective = merge_project_configs(shared, existing) if shared else existing
        result: dict[str, Any] = {"config": _whitelisted_config(effective)}
        if shared:
            result["shared_config_loaded"] = True
        return result
    if action == "add_convention":
        pattern = _text({"pattern": _require(payload, "pattern")}, "pattern")
        reason = _text(payload, "reason") or "项目正常约定"
        match_type = "regex" if _text(payload, "match_type") == "regex" else "contains"
        scope = _text(payload, "scope") or "all"
        conv_list = extract_conventions(existing)
        new_item = {
            "id": new_id("conv"),
            "pattern": pattern,
            "reason": reason,
            "match_type": match_type,
            "scope": scope,
        }
        updated_convs = [
            entry
            for entry in conv_list
            if not (entry.get("pattern") == pattern and entry.get("scope") == scope)
        ]
        updated_convs.append(new_item)
        merged_conv = dict(existing)
        merged_conv["schema_version"] = 1
        merged_conv["conventions"] = updated_convs
        write_json(path, merged_conv)
        return {"convention": new_item, "conventions": updated_convs}
    if action == "remove_convention":
        target_id = _text(payload, "convention_id") or _text(payload, "id")
        target_pattern = _text(payload, "pattern")
        if not target_id and not target_pattern:
            raise invalid_request("需要 convention_id 或 pattern")
        conv_list = extract_conventions(existing)
        updated_convs = [
            entry
            for entry in conv_list
            if (not target_id or entry.get("id") != target_id)
            and (not target_pattern or entry.get("pattern") != target_pattern)
        ]
        merged_conv = dict(existing)
        merged_conv["schema_version"] = 1
        merged_conv["conventions"] = updated_convs
        write_json(path, merged_conv)
        return {"conventions": updated_convs}
    if action == "detect":
        root_path = Path(project.root)
        derived = build_project_context(root_path, {})
        test_cmds = [str(c) for c in derived.get("test_commands") or [] if isinstance(c, str)]
        existing_cmds = derived.get("existing_commands") or []
        build_cmd = next(
            (
                str(c)
                for c in [*existing_cmds, *test_cmds]
                if isinstance(c, str) and "build" in c.lower()
            ),
            "",
        )
        smallest = str(derived.get("smallest_test") or "")
        smoke_cmd = str(derived.get("smoke_command") or "")
        if not smoke_cmd and smallest:
            smoke_cmd = f"python -m pytest {smallest}"
        draft: dict[str, Any] = {}
        l1_draft: dict[str, Any] = {}
        if build_cmd:
            l1_draft["build"] = build_cmd
        if smoke_cmd:
            l1_draft["smoke_command"] = smoke_cmd
        if l1_draft:
            draft["l1"] = l1_draft
        if test_cmds:
            draft["runners"] = {"test": test_cmds[0]}
        raw_services = derived.get("services")
        if isinstance(raw_services, dict) and raw_services:
            first_svc = next(
                (v for v in raw_services.values() if isinstance(v, dict)),
                None,
            )
            svc_src = first_svc if first_svc is not None else raw_services
            svc_draft: dict[str, Any] = {}
            if isinstance(svc_src.get("start"), str) and svc_src["start"]:
                svc_draft["start"] = svc_src["start"]
            health = str(svc_src.get("health") or "")
            if health:
                svc_draft["health"] = health
                parsed = urlsplit(health)
                if parsed.scheme and parsed.netloc:
                    svc_draft["base_url"] = f"{parsed.scheme}://{parsed.netloc}"
            if svc_draft:
                draft["services"] = svc_draft
        secrets = known_secret_values([raw])
        return {
            "draft": _whitelisted_config(_strip_secret_fields(draft)),
            "detected": redact_tree(
                {
                    "framework": derived.get("framework", []),
                    "test_commands": test_cmds[:8],
                    "services": raw_services or {},
                },
                secrets,
            ),
        }
    if action != "update":
        raise invalid_request(
            f"不支持的动作：{action}",
            "动作只能是 get、update、detect、add_convention 或 remove_convention",
        )
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        raise invalid_request("patch 必须是对象", "把要修改的配置项放进 patch")
    unknown = [str(key) for key in patch if key not in CONFIG_SECTIONS]
    if unknown:
        raise invalid_request(
            "项目配置不支持这些字段：" + "、".join(unknown),
            "只允许 " + "、".join(CONFIG_SECTIONS),
        )
    secret = _find_secret_field(patch)
    if secret is not None:
        raise AiTestError(
            "INVALID_REQUEST",
            f"项目配置不允许包含密钥字段：{secret}",
            "把密钥放到用户配置或环境变量中；项目配置只保存非敏感设置",
        )
    for section, value in patch.items():
        if section == "conventions":
            if value is not None and not isinstance(value, (dict, list)):
                raise invalid_request(
                    "conventions 必须是数组或对象",
                    "把 conventions 写成数组或含 items 的对象后重试",
                )
            continue
        if value is not None and not isinstance(value, dict):
            raise invalid_request(
                f"{section} 必须是对象", f"把 {section} 写成 JSON 对象后重试"
            )
    merged = dict(existing)
    for section, value in patch.items():
        if value is None:
            merged.pop(section, None)
            continue
        if section == "conventions":
            merged[section] = extract_conventions({"conventions": value})
            continue
        current = merged.get(section)
        merged[section] = _merge_config(
            current if isinstance(current, dict) else {}, value
        )
    merged["schema_version"] = 1
    l1_config = merged.get("l1")
    if isinstance(l1_config, dict) and "entry" in l1_config:
        entries = l1_config.get("entry")
        if not isinstance(entries, list) or any(
            not isinstance(item, str) or not _MODULE_NAME.match(item)
            for item in entries
        ):
            raise invalid_request(
                'l1.entry 必须是模块名数组，例如 ["iniconfig"]',
                "填可导入的模块名，不要填目录路径或文件路径",
            )
    _validate_config_numbers(merged)
    write_json(path, merged)
    return {"config": _whitelisted_config(_strip_secret_fields(merged))}


def cmd_module_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    raw_deps = payload.get("deps", [])
    if not isinstance(raw_deps, list) or any(not isinstance(item, dict) for item in raw_deps):
        raise invalid_request("deps 必须是对象数组", "每个依赖写成 {to,type,note}")
    deps = [Dependency.model_validate(item) for item in raw_deps]
    secrets = _project_secrets(ws, project.project_id)
    module = Module(
        name=redact(_text({"name": _require(payload, "name")}, "name"), secrets),
        responsibility=redact(_text(payload, "responsibility"), secrets),
        owner=_text(payload, "owner"),
        status=_text(payload, "status", "开发中"),
        deps=deps,
    )
    project.modules.append(module)
    project.revision += 1
    ws.save_project(project)
    return {"module_id": module.module_id, "revision": project.revision}


def cmd_module_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    module_id = _text({"module_id": _require(payload, "module_id")}, "module_id")
    for index, module in enumerate(project.modules):
        if module.module_id == module_id:
            data = module.model_dump(mode="json")
            patch = payload.get("patch", {})
            if not isinstance(patch, dict):
                raise invalid_request("patch 必须是对象")
            allowed = {"name", "responsibility", "owner", "status", "deps"}
            unknown = sorted(set(patch) - allowed)
            if unknown:
                raise invalid_request(
                    "module.update 不支持这些字段：" + "、".join(unknown),
                    "只允许 name/responsibility/owner/status/deps；模块 ID 不可修改",
                )
            if "name" in patch:
                raise invalid_request(
                    "模块名不可修改；请新建模块后更新引用",
                    "模块名是任务与验收项的引用键",
                )
            if "deps" in patch:
                raw_deps = patch["deps"]
                if not isinstance(raw_deps, list) or any(
                    not isinstance(item, dict) for item in raw_deps
                ):
                    raise invalid_request("deps 必须是对象数组")
                data["deps"] = [
                    Dependency.model_validate(item).model_dump(mode="json")
                    for item in raw_deps
                ]
            secrets = _project_secrets(ws, project.project_id)
            for key in ("responsibility", "owner", "status"):
                if isinstance(patch.get(key), str):
                    data[key] = redact(patch[key], secrets)
            project.modules[index] = Module.model_validate(data)
            project.revision += 1
            ws.save_project(project)
            return {"module_id": module_id, "revision": project.revision}
    raise AiTestError("INVALID_REQUEST", f"模块不存在：{module_id}", "检查模块 ID")


def cmd_module_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    return {"modules": [m.model_dump(mode="json") for m in project.modules]}


def cmd_module_suggest(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Return AI module suggestions without writing anything."""
    del confirm
    project = _project(ws, payload)
    client = cmd_facade_attr("build_client", build_client)(ws, project.project_id)
    if client is None:
        raise AiTestError(
            "MODEL_UNAVAILABLE",
            "未配置模型密钥，无法建议模块",
            "设置 AITEST_MODEL_KEY 或在用户配置写入 model.api_key",
        )
    suggestions = client.suggest_modules(build_module_context(project))
    registered = {module.name for module in project.modules}
    invalid = invalid_dependencies(suggestions, registered)
    if invalid:
        raise AiTestError(
            "MODEL_OUTPUT_INVALID",
            "模块依赖指向未建议且未登记的模块：" + "、".join(invalid),
            "重试建议，或先登记被引用的模块",
        )
    return {"modules": suggestions, "registered": sorted(registered)}
