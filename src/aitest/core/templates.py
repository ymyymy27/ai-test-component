"""Built-in template presets and their per-item model disposition.

Templates are informational: they never enter the required-test denominator and
never change acceptance items. A disposition of ``not_applicable`` or
``unsupported`` must carry a reason; a missing reason degrades to ``unknown``
instead of being treated as applicable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from aitest.errors import AiTestError
from aitest.models import Project, Task
from aitest.storage.workspace import iso_now

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MISSING_REASON = "模型未提供理由"
_ALLOWED = ("applicable", "not_applicable", "unsupported")

_SYSTEM_TEMPLATE = (
    "你是测试模板处置助手。只输出一个 JSON 对象，格式为 "
    '{"items": [{"item_id": "检查项 ID", '
    '"disposition": "applicable|not_applicable|unsupported", "reason": "理由"}]}。'
    "必须为每个检查项输出一项；不适用或不支持时必须写理由。"
    "模板不改变验收项，也不要补充新的检查项。不要输出 JSON 之外的任何文本。"
)


class JsonModel(Protocol):
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Return one validated JSON object."""
        ...


def templates_root() -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / "templates"


def _read_template(path: Path) -> dict[str, Any] | None:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    items = data.get("items")
    template_id = data.get("template_id")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(template_id, str) or not template_id.strip():
        return None
    if not isinstance(items, list):
        return None
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id.strip():
            continue
        normalized.append(
            {
                "item_id": item_id.strip(),
                "layer": str(item.get("layer") or ""),
                "objective": str(item.get("objective") or item.get("expected") or ""),
                "expected": str(item.get("expected") or ""),
                "preconditions": [
                    str(entry)
                    for entry in item.get("preconditions", [])
                    if isinstance(entry, str) and entry.strip()
                ]
                if isinstance(item.get("preconditions"), list)
                else [],
                "verification": str(item.get("verification") or ""),
            }
        )
    if not normalized:
        return None
    return {
        "template_id": template_id.strip(),
        "name": name.strip(),
        "version": str(data.get("version") or ""),
        "items": normalized,
    }


def _template_paths() -> list[Path]:
    root = templates_root()
    if not root.is_dir():
        return []
    paths: list[Path] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir():
            continue
        candidates = sorted(directory.glob("*.json"))
        if candidates:
            paths.append(candidates[-1])
    return paths


def list_templates() -> list[dict[str, Any]]:
    """Return one summary row per preset; unreadable presets are skipped."""
    summaries: list[dict[str, Any]] = []
    for path in _template_paths():
        template = _read_template(path)
        if template is None:
            continue
        summaries.append(
            {
                "template_id": template["template_id"],
                "name": template["name"],
                "version": template["version"],
                "item_count": len(template["items"]),
            }
        )
    return summaries


def load_template(name: str) -> dict[str, Any]:
    """Load one preset by directory name or template id."""
    requested = name.strip()
    if not requested or _SAFE_NAME.fullmatch(requested) is None:
        raise AiTestError(
            "INVALID_REQUEST", f"模板名称不合法：{name}", "使用 template.list 给出名称"
        )
    root = templates_root()
    directory = root / requested
    candidates: list[Path] = []
    if directory.is_dir():
        candidates = sorted(directory.glob("*.json"))
    else:
        for path in _template_paths():
            template = _read_template(path)
            if template is not None and template["template_id"] == requested:
                candidates = [path]
                break
    if not candidates:
        raise AiTestError(
            "INVALID_REQUEST", f"模板不存在：{name}", "先用 template.list 查看可用模板"
        )
    template = _read_template(candidates[-1])
    if template is None:
        raise AiTestError(
            "INVALID_REQUEST", f"模板文件不完整：{name}", "修复模板文件或换一个模板"
        )
    return template


def _answer_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = data.get("items")
    if raw is None:
        raw = data.get("dispositions")
    if not isinstance(raw, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("item_id")
        if isinstance(item_id, str) and item_id.strip():
            indexed.setdefault(item_id.strip(), entry)
    return indexed


def _reason(entry: dict[str, Any]) -> str:
    reason = entry.get("reason")
    return reason.strip() if isinstance(reason, str) and reason.strip() else ""


def apply_template(
    client: JsonModel,
    template: dict[str, Any],
    project: Project,
    task: Task,
) -> dict[str, Any]:
    """Ask the model to dispose every template item and keep unknown explicit."""
    payload = {
        "template": {
            "template_id": template["template_id"],
            "name": template["name"],
            "version": template["version"],
        },
        "project": {"name": project.name, "goal": project.goal},
        "task": {
            "title": task.title,
            "goal": task.goal,
            "scope": task.scope,
            "acceptance_items": [item.text for item in task.acceptance_items],
        },
        "items": template["items"],
    }
    data = client.complete_json(
        _SYSTEM_TEMPLATE, json.dumps(payload, ensure_ascii=False)
    )
    answers = _answer_index(data)
    dispositions: list[dict[str, Any]] = []
    counts = {"applicable": 0, "not_applicable": 0, "unsupported": 0, "unknown": 0}
    for item in template["items"]:
        entry = answers.get(item["item_id"])
        disposition = ""
        reason = ""
        if entry is not None:
            value = entry.get("disposition")
            if isinstance(value, str) and value.strip() in _ALLOWED:
                disposition = value.strip()
            reason = _reason(entry)
        if not disposition:
            disposition = "unknown"
            reason = reason or "模型未给出有效处置"
        elif disposition in {"not_applicable", "unsupported"} and not reason:
            disposition = "unknown"
            reason = _MISSING_REASON
        counts[disposition] += 1
        dispositions.append(
            {
                "item_id": item["item_id"],
                "layer": item["layer"],
                "objective": item["objective"],
                "disposition": disposition,
                "reason": reason,
            }
        )
    return {
        "template_id": template["template_id"],
        "name": template["name"],
        "version": template["version"],
        "applied_at": iso_now(),
        "items": dispositions,
        "summary": counts,
    }
