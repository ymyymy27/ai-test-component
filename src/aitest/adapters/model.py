"""DeepSeek-compatible chat completion adapter with strict output validation.

The adapter talks to any OpenAI-compatible ``/chat/completions`` endpoint using
only the standard library. Model output is untrusted: every value returned to a
caller is rebuilt from validated fields, and error messages never include the
API key.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import Any

from aitest.errors import AiTestError

_ALLOWED_LAYERS = frozenset({"L1", "L2", "L3"})

_SYSTEM_SUGGEST = (
    "你是测试范围建议助手。只输出一个 JSON 对象，格式为 "
    '{"targets": [{"title": "标题", "reason": "理由"}]}。'
    '给出 3 到 5 条建议；如果没有足够信号，返回 {"targets": []}。'
    "不要输出 JSON 之外的任何文本。"
)

_SYSTEM_CASES = (
    "你是测试用例生成助手。只输出一个 JSON 对象，格式为 "
    '{"cases": [{"acceptance_item_id": "验收项 ID", "layer": "L1|L2|L3", '
    '"expected": "预期结果", "expectation_source": "期望来源", '
    '"verify_method": "必须以 command:/script:/http:/playwright: 开头", "depends_on": ["依赖项"], '
    '"script_name": "可选文件名", "script_content": "可选脚本内容"}]}。'
    "verify_method 只允许四种前缀：command:（shell 命令）、"
    "script:（配合 script_content）、http:（JSON 请求数组）、"
    "playwright:（脚本路径）。"
    "http: 的 JSON 每项为 {method,url,headers?,json?/form?,expect?:[{path,operator,value}]}，"
    "不支持变量替换；operator 只能是 equals/contains/exists。"
    "必须为每个选中的验收项至少生成一条用例，不要遗漏。"
    "不要输出 JSON 之外的任何文本。"
)

_SYSTEM_FAILURE = (
    "你是失败分析助手。只输出一个 JSON 对象，格式为 "
    '{"observed": "观察到的事实", "evidence_refs": ["证据引用"], '
    '"candidate_causes": ["候选原因"], "unverified": ["尚未验证的假设"], '
    '"next_steps": ["下一步"]}。'
    "unverified 必须存在且至少包含一条内容；不要输出 JSON 之外的任何文本。"
)


def _invalid(message: str) -> AiTestError:
    return AiTestError("MODEL_OUTPUT_INVALID", message, "检查模型输出结构后重试")


def _unavailable(message: str) -> AiTestError:
    return AiTestError("MODEL_UNAVAILABLE", message, "检查模型服务和网络后重试")


def _as_mapping(value: object, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(message)
    mapping: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise _invalid(message)
        mapping[key] = item
    return mapping


def _require_text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"字段 {key} 必须是非空字符串")
    return value


def _require_text_list(mapping: dict[str, Any], key: str, *, allow_empty: bool) -> list[str]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise _invalid(f"字段 {key} 必须是字符串数组")
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise _invalid(f"字段 {key} 必须只包含非空字符串")
        items.append(entry)
    if not allow_empty and not items:
        raise _invalid(f"字段 {key} 不能为空")
    return items


def _acceptance_ids(selected_items: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for item in selected_items:
        for key in ("acceptance_item_id", "id", "item_id"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                ids.add(value)
                break
        else:
            raise AiTestError(
                "INVALID_REQUEST",
                "验收项缺少 acceptance_item_id",
                "为每个选中项提供 acceptance_item_id",
            )
    return ids


def _extract_content(raw: bytes) -> str:
    try:
        envelope: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _unavailable("模型服务响应不是合法 JSON") from None
    if not isinstance(envelope, dict):
        raise _unavailable("模型服务响应结构异常")
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _unavailable("模型服务响应缺少 choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise _unavailable("模型服务响应结构异常")
    message = first.get("message")
    if not isinstance(message, dict):
        raise _unavailable("模型服务响应缺少 message")
    content = message.get("content")
    if not isinstance(content, str):
        raise _unavailable("模型服务响应缺少 content")
    return content


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        data: object = json.loads(content.strip())
    except json.JSONDecodeError:
        raise _invalid("模型输出不是合法 JSON") from None
    return _as_mapping(data, "模型输出不是 JSON 对象")


class ModelClient:
    """Minimal OpenAI-compatible client for a DeepSeek chat model."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 120,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Request one JSON object and return it after validation."""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                raw: bytes = response.read()
        except urllib.error.HTTPError as exc:
            raise _unavailable(f"模型服务返回 HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise _unavailable("模型服务不可用") from None
        return _parse_json_object(_extract_content(raw))

    def suggest_targets(self, context: str) -> list[dict[str, Any]]:
        """Return 3-5 suggested targets, or an empty list when there is no signal."""
        data = self.complete_json(_SYSTEM_SUGGEST, context)
        raw = data.get("targets")
        if raw is None:
            raw = data.get("suggestions")
        if not isinstance(raw, list):
            raise _invalid("字段 targets 必须是数组")
        targets: list[dict[str, Any]] = []
        for entry in raw:
            item = _as_mapping(entry, "targets 的条目必须是对象")
            targets.append(
                {
                    "title": _require_text(item, "title"),
                    "reason": _require_text(item, "reason"),
                }
            )
        if targets and not 3 <= len(targets) <= 5:
            raise _invalid("targets 必须为空或包含 3 到 5 条建议")
        return targets

    def generate_cases(
        self,
        task: dict[str, Any],
        selected_items: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate validated cases and require coverage of every selected item."""
        required_ids = _acceptance_ids(selected_items)
        user = json.dumps(
            {"task": task, "selected_items": selected_items, "context": context or {}},
            ensure_ascii=False,
        )
        data = self.complete_json(_SYSTEM_CASES, user)
        raw = data.get("cases")
        if raw is None:
            raw = data.get("test_cases")
        if not isinstance(raw, list):
            raise _invalid("字段 cases 必须是数组")
        cases: list[dict[str, Any]] = []
        covered: set[str] = set()
        for entry in raw:
            item = _as_mapping(entry, "cases 的条目必须是对象")
            acceptance_item_id = _require_text(item, "acceptance_item_id")
            layer = _require_text(item, "layer")
            if layer not in _ALLOWED_LAYERS:
                raise _invalid("layer 必须是 L1、L2 或 L3")
            case = {
                "acceptance_item_id": acceptance_item_id,
                "layer": layer,
                "expected": _require_text(item, "expected"),
                "expectation_source": _require_text(item, "expectation_source"),
                "verify_method": _require_text(item, "verify_method"),
                "depends_on": (
                    []
                    if item.get("depends_on") is None
                    else [item["depends_on"]]
                    if isinstance(item.get("depends_on"), str)
                    else _require_text_list(item, "depends_on", allow_empty=True)
                ),
                "script_name": str(item.get("script_name") or "") or None,
                "script_content": str(item.get("script_content") or "") or None,
            }
            cases.append(case)
            covered.add(acceptance_item_id)
        missing = required_ids - covered
        if missing:
            raise _invalid(f"用例未覆盖验收项：{', '.join(sorted(missing))}")
        return cases

    def analyze_failure(self, run: dict[str, Any], evidence: list[str]) -> dict[str, Any]:
        """Return the five-part failure analysis, including verified unknowns."""
        user = json.dumps({"run": run, "evidence": evidence}, ensure_ascii=False)
        data = self.complete_json(_SYSTEM_FAILURE, user)
        nested = data.get("analysis")
        if isinstance(nested, dict):
            data = _as_mapping(nested, "analysis 必须是对象")
        return {
            "observed": _require_text(data, "observed"),
            "evidence_refs": _require_text_list(data, "evidence_refs", allow_empty=True),
            "candidate_causes": _require_text_list(data, "candidate_causes", allow_empty=True),
            "unverified": _require_text_list(data, "unverified", allow_empty=False),
            "next_steps": _require_text_list(data, "next_steps", allow_empty=True),
        }
