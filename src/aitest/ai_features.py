"""AI-assisted dependency judging and factual report summaries.

Both helpers treat model output as untrusted input: values are rebuilt from
validated fields, missing or invalid answers degrade to ``unknown`` and the
caller never receives a partial verdict. File contents are only placed in the
prompt and are never logged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aitest.adapters.model import ModelClient

_ALLOWED_KINDS = frozenset({"real", "mock", "mixed", "unknown"})

_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "dist",
        "build",
        "legacy",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

_SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".java",
        ".kt",
        ".kts",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".cs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".swift",
        ".m",
        ".mm",
        ".scala",
        ".sh",
        ".bash",
        ".ps1",
        ".bat",
        ".sql",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".conf",
        ".properties",
        ".gradle",
        ".xml",
    }
)

_CONFIG_NAMES = frozenset(
    {
        "dockerfile",
        "makefile",
        "gemfile",
        "procfile",
        "pipfile",
        "requirements.txt",
        "go.mod",
        "go.sum",
        "cargo.lock",
    }
)

_BANNED_SUMMARY_WORDS = (
    "通过",
    "不通过",
    "质量良好",
    "可以上线",
    "建议发布",
    "全部通过",
)

_SYSTEM_MOCK_JUDGE = (
    "你是测试依赖判定助手。只输出一个 JSON 对象，格式为 "
    '{"dependencies": [{"name": "依赖名", "kind": "real|mock|mixed|unknown", '
    '"reason": "判定依据", "evidence_refs": ["文件路径或证据引用"]}]}。'
    "必须为每个待判定依赖输出一项；evidence_refs 必须引用给出的文件或证据，"
    "无法确认时 kind 用 unknown。不要输出 JSON 之外的任何文本。"
)

_SYSTEM_REPORT_SUMMARY = (
    "你是测试报告摘要助手。只输出一个 JSON 对象，格式为 "
    '{"summary": "只陈述事实的摘要"}。'
    "只写事实，不写通过、不通过、质量、上线或发布建议等结论。"
    "不要输出 JSON 之外的任何文本。"
)

_MAX_EXCERPT_CHARS = 4000


def _unknown(
    name: str,
    reason: str,
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "unknown",
        "reason": reason,
        "evidence_refs": evidence_refs or [],
    }


def _is_candidate(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in _SOURCE_SUFFIXES or name in _CONFIG_NAMES


def _collect_files(root: Path, max_files: int, max_depth: int) -> list[Path]:
    if max_files <= 0:
        return []
    files: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if len(files) >= max_files:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            return
        for entry in entries:
            if len(files) >= max_files:
                return
            try:
                if entry.is_dir():
                    if depth < max_depth and entry.name not in _SKIP_DIRS:
                        walk(entry, depth + 1)
                    continue
            except OSError:
                continue
            if entry.is_file() and _is_candidate(entry):
                files.append(entry)

    walk(root, 0)
    return files


def _read_excerpt(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:_MAX_EXCERPT_CHARS]


def _sampled_payload(
    project_root: Path,
    max_files: int,
    max_depth: int,
) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    for path in _collect_files(project_root, max_files, max_depth):
        try:
            relative = path.relative_to(project_root).as_posix()
        except ValueError:
            relative = path.name
        samples.append({"path": relative, "content": _read_excerpt(path)})
    return samples


def _evidence_refs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            refs.append(item.strip())
    return refs


def _index_entries(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            indexed.setdefault(name.strip(), entry)
    return indexed


def _extract_entries(data: object) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    raw = data.get("dependencies")
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _normalize_entry(
    dependency: str,
    entry: dict[str, Any] | None,
) -> dict[str, Any]:
    if entry is None:
        return _unknown(dependency, "模型未对该依赖给出判断")
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return _unknown(dependency, "模型未提供有效理由")
    refs = _evidence_refs(entry.get("evidence_refs"))
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in _ALLOWED_KINDS:
        return _unknown(dependency, reason.strip(), refs)
    if kind != "unknown" and not refs:
        return _unknown(dependency, reason.strip(), refs)
    return {
        "name": dependency,
        "kind": kind,
        "reason": reason.strip(),
        "evidence_refs": refs,
    }


def judge_mock(
    project_root: Path,
    dependencies: list[str],
    evidence: list[str],
    *,
    client: ModelClient,
    max_files: int = 40,
    max_depth: int = 3,
) -> list[dict[str, Any]]:
    """Ask the model whether each dependency is real, mocked or mixed.

    Every input dependency appears exactly once in the result, in input order.
    Missing, invalid or unsupported model answers become ``unknown``; a model
    failure yields all ``unknown`` instead of raising.
    """
    if not dependencies:
        return []
    payload = {
        "dependencies": dependencies,
        "evidence": evidence,
        "files": _sampled_payload(project_root, max_files, max_depth),
    }
    try:
        data = client.complete_json(
            _SYSTEM_MOCK_JUDGE,
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        return [_unknown(dependency, "模型调用失败，保持未判定") for dependency in dependencies]
    indexed = _index_entries(_extract_entries(data))
    results: list[dict[str, Any]] = []
    for dependency in dependencies:
        entry = indexed.get(dependency.strip())
        results.append(_normalize_entry(dependency, entry))
    return results


def summarize_report(facts: dict[str, Any], *, client: ModelClient) -> str | None:
    """Return a factual summary, or ``None`` when the model is unusable or conclusive."""
    try:
        data = client.complete_json(
            _SYSTEM_REPORT_SUMMARY,
            json.dumps(facts, ensure_ascii=False, default=str),
        )
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    text: str | None = None
    for key in ("summary", "text", "report"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            break
    if text is None:
        return None
    if any(word in text for word in _BANNED_SUMMARY_WORDS):
        return None
    return text
