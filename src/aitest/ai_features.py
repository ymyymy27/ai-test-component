"""AI-assisted dependency judging and factual report summaries.

Both helpers treat model output as untrusted input: values are rebuilt from
validated fields, missing or invalid answers degrade to ``unknown`` and the
caller never receives a partial verdict. File contents are only placed in the
prompt and are never logged.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from aitest.adapters.model import ModelClient

_ALLOWED_KINDS = frozenset({"real", "mock", "mixed", "unknown"})

_TEST_NAME_MARKERS = ("test_", "_test.", ".test.", ".spec.", "tests/", "spec/")
_MAX_SCAN_FILES = 200
_MAX_FILE_READ = 128 * 1024

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
    "质量合格",
    "可以上线",
    "建议上线",
    "建议发布",
    "可以发布",
    "可以交付",
    "准备发布",
    "全部通过",
    "一切正常",
    "没有问题",
    "基本可用",
    "验收通过",
    "测试通过",
)

_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))
_TEST_FRAMEWORKS = frozenset(
    {"pytest", "unittest", "nose", "playwright", "selenium", "vitest", "jest", "mocha"}
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
    *,
    read_files: list[str] | None = None,
    limit_reached: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "unknown",
        "reason": reason,
        "evidence_refs": evidence_refs or [],
        "read_files": read_files or [],
        "limit_reached": limit_reached,
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
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(_MAX_EXCERPT_CHARS)
    except OSError:
        return ""


def _test_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(files) >= _MAX_SCAN_FILES:
            break
        if not path.is_file():
            continue
        lowered = path.as_posix().lower()
        name = path.name.lower()
        if not any(marker in lowered for marker in _TEST_NAME_MARKERS):
            continue
        if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".go")):
            files.append(path)
    return files


def _local_names(root: Path) -> set[str]:
    names: set[str] = set()
    try:
        entries = list(root.iterdir())
    except OSError:
        return names
    for entry in entries:
        if entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
        elif entry.is_file() and entry.suffix == ".py":
            names.add(entry.stem)
    src = root / "src"
    if src.is_dir():
        try:
            for entry in src.iterdir():
                if entry.is_dir():
                    names.add(entry.name)
                elif entry.suffix == ".py":
                    names.add(entry.stem)
        except OSError:
            pass
    return names


def collect_dependencies(project_root: Path) -> list[str]:
    """Collect likely external dependencies from test code and test config.

    This is deliberately shallow: imports and URLs visible in test files. The
    model still decides real/mock/mixed/unknown by reading the sampled source.
    """
    found: set[str] = set()
    local = _local_names(project_root)
    for path in _test_files(project_root):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(_MAX_FILE_READ)
        except OSError:
            continue
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            found.add(alias.name.split(".", 1)[0])
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        found.add(node.module.split(".", 1)[0])
        for match in re.finditer(
            r"""(?:require\s*\(\s*|from\s+|import\s+)["']([@\w./-]+)["']""", text
        ):
            name = match.group(1)
            if name.startswith((".", "/")):
                continue
            found.add(name.split("/", 1)[0])
        for match in re.finditer(r"https?://([A-Za-z0-9_.:-]+)", text):
            host = match.group(1).split(":", 1)[0]
            if host and host not in {"localhost", "127.0.0.1", "example.com"}:
                found.add(host)
    return sorted(
        name
        for name in found
        if name
        and name not in _STDLIB
        and name not in _TEST_FRAMEWORKS
        and name not in local
        and not name.startswith("_")
    )


def _sampled_payload(
    project_root: Path,
    max_files: int,
    max_depth: int,
) -> tuple[list[dict[str, str]], list[str], bool, list[dict[str, str]]]:
    """Read sample excerpts and return (payload, read_files, limit_reached, hashes)."""
    samples: list[dict[str, str]] = []
    read_files: list[str] = []
    hashes: list[dict[str, str]] = []
    if max_files <= 0:
        return samples, read_files, False, hashes
    collected = _collect_files(project_root, max_files + 1, max_depth)
    limit_reached = len(collected) > max_files
    for path in collected[:max_files]:
        try:
            relative = path.relative_to(project_root).as_posix()
        except ValueError:
            relative = path.name
        samples.append({"path": relative, "content": _read_excerpt(path)})
        read_files.append(relative)
        hashes.append({"path": relative, "sha256": _file_digest(path)})
    return samples, read_files, limit_reached, hashes


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _cache_key(
    project_revision: int,
    dependencies: list[str],
    hashes: list[dict[str, str]],
) -> str:
    payload = {
        "project_revision": project_revision,
        "dependencies": dependencies,
        "files": hashes,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_cached(path: Path) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    cached: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return None
        cached.append(item)
    return cached


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
    *,
    read_files: list[str],
    limit_reached: bool,
) -> dict[str, Any]:
    if entry is None:
        return _unknown(
            dependency,
            "模型未对该依赖给出判断",
            read_files=read_files,
            limit_reached=limit_reached,
        )
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return _unknown(
            dependency,
            "模型未提供有效理由",
            read_files=read_files,
            limit_reached=limit_reached,
        )
    refs = _evidence_refs(entry.get("evidence_refs"))
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in _ALLOWED_KINDS:
        return _unknown(
            dependency,
            reason.strip(),
            refs,
            read_files=read_files,
            limit_reached=limit_reached,
        )
    if kind != "unknown" and not refs:
        return _unknown(
            dependency,
            reason.strip(),
            refs,
            read_files=read_files,
            limit_reached=limit_reached,
        )
    return {
        "name": dependency,
        "kind": kind,
        "reason": reason.strip(),
        "evidence_refs": refs,
        "read_files": read_files,
        "limit_reached": limit_reached,
    }


def judge_mock(
    project_root: Path,
    dependencies: list[str],
    evidence: list[str],
    *,
    client: ModelClient,
    max_files: int = 40,
    max_depth: int = 3,
    workspace: Path | None = None,
    project_revision: int | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Ask the model whether each dependency is real, mocked or mixed.

    Every input dependency appears exactly once in the result, in input order.
    Missing, invalid or unsupported model answers become ``unknown``; a model
    failure yields all ``unknown`` instead of raising. When ``workspace`` and
    ``project_revision`` are given, successful judgements are cached by project
    revision plus sampled file digests so an unchanged project skips the model.
    """
    if not dependencies:
        return []
    samples, read_files, limit_reached, hashes = _sampled_payload(
        project_root, max_files, max_depth
    )
    if limit_reached:
        return [
            _unknown(
                dependency,
                f"达到读取上限（最多 {max_files} 个文件、{max_depth} 层），未判定",
                read_files=read_files,
                limit_reached=True,
            )
            for dependency in dependencies
        ]
    cache_path: Path | None = None
    if workspace is not None and project_revision is not None:
        key = _cache_key(project_revision, dependencies, hashes)
        cache_path = workspace / ".cache" / "mock" / f"{key}.json"
        if use_cache:
            cached = _read_cached(cache_path)
            if cached is not None:
                return cached
    payload = {
        "dependencies": dependencies,
        "evidence": evidence,
        "files": samples,
    }
    try:
        data = client.complete_json(
            _SYSTEM_MOCK_JUDGE,
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        return [
            _unknown(
                dependency,
                "模型调用失败，保持未判定",
                read_files=read_files,
                limit_reached=limit_reached,
            )
            for dependency in dependencies
        ]
    indexed = _index_entries(_extract_entries(data))
    results: list[dict[str, Any]] = []
    for dependency in dependencies:
        entry = indexed.get(dependency.strip())
        results.append(
            _normalize_entry(
                dependency,
                entry,
                read_files=read_files,
                limit_reached=limit_reached,
            )
        )
    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(results, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
    return results


def _conclusion_free(text: str) -> bool:
    """Reject conclusion words even when spaced or punctuated by the model."""
    compact = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).lower()
    return not any(word in compact for word in _BANNED_SUMMARY_WORDS)


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
    if not _conclusion_free(text):
        return None
    return text
