"""统一只读仓库索引器、带内容校验指纹的缓存层与 Monorepo 原生发现。

集中替代散落在多个模块中互不一致的跳过目录集合，统一尊重 `.gitignore` / `.aitestignore`，
并对 Git 脏文件结合 `(mtime_ns, size)` 与内容摘要计算真实指纹，杜绝二次编辑缓存不失效
以及中文路径/重命名引号误解析问题。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitest.security import is_ignored_path, load_ignore_patterns

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.11+ 内置 tomllib
    tomllib = None  # type: ignore[assignment]

DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "coverage",
        "test-results",
        ".next",
        ".nuxt",
        "site-packages",
        ".tox",
        ".nox",
        ".yarn",
        ".cache",
        "legacy",
    }
)

_MANIFEST_NAMES: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
    }
)

_TEST_DIR_NAMES: frozenset[str] = frozenset({"tests", "test", "testing", "e2e", "__tests__"})
_TEST_FILE_PREFIXES: tuple[str, ...] = ("test_",)
_TEST_FILE_SUFFIXES: tuple[str, ...] = (
    "_test.py",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
)


@dataclass(frozen=True, slots=True)
class RepoFile:
    """单遍扫描收集的文件元数据。"""

    rel_posix: str
    path: Path
    size: int
    mtime_ns: int
    is_test: bool


@dataclass(frozen=True, slots=True)
class RepoScanResult:
    """单遍仓库扫描结果。"""

    files: tuple[RepoFile, ...]
    manifests: dict[str, Path]
    test_dirs: tuple[str, ...]
    truncated: bool


def is_test_relpath(rel_posix: str) -> bool:
    """判断相对路径是否属于测试目录或测试文件。"""
    parts = [part.lower() for part in rel_posix.split("/") if part]
    if not parts:
        return False
    if any(part in _TEST_DIR_NAMES for part in parts[:-1]):
        return True
    name = parts[-1]
    return name.startswith(_TEST_FILE_PREFIXES) or name.endswith(_TEST_FILE_SUFFIXES)


def scan_repo(
    root: Path,
    *,
    extensions: frozenset[str] | None = None,
    extra_skip_dirs: Iterable[str] = (),
    include_tests: bool = True,
    max_files: int | None = None,
    max_file_size: int = 512 * 1024,
) -> RepoScanResult:
    """单遍遍历仓库，统一应用 `.gitignore`/`.aitestignore` 与跳过目录集合。"""
    if not root.is_dir():
        return RepoScanResult(files=(), manifests={}, test_dirs=(), truncated=False)

    skip = DEFAULT_SKIP_DIRS | {str(item).lower() for item in extra_skip_dirs}
    ignore_patterns = load_ignore_patterns(root)
    files: list[RepoFile] = []
    manifests: dict[str, Path] = {}
    test_dirs_set: set[str] = set()
    truncated = False

    for base, dirs, filenames in os.walk(root):
        base_path = Path(base)
        kept_dirs: list[str] = []
        for dirname in sorted(dirs):
            low = dirname.lower()
            if low in skip or dirname.startswith("."):
                continue
            cand_dir = base_path / dirname
            if is_ignored_path(root, cand_dir, ignore_patterns):
                continue
            rel_dir = cand_dir.relative_to(root).as_posix()
            is_test_dir = low in _TEST_DIR_NAMES
            if is_test_dir:
                test_dirs_set.add(f"{rel_dir}/")
                if not include_tests:
                    continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs

        for name in sorted(filenames):
            file_path = base_path / name
            if is_ignored_path(root, file_path, ignore_patterns):
                continue
            try:
                rel_posix = file_path.relative_to(root).as_posix()
            except ValueError:
                continue
            if name in _MANIFEST_NAMES or re.fullmatch(r"requirements.*\.txt", name):
                manifests[rel_posix] = file_path
            if extensions is not None and Path(name).suffix.lower() not in extensions:
                continue
            is_test = is_test_relpath(rel_posix)
            if not include_tests and is_test:
                continue
            try:
                stat = file_path.stat()
            except OSError:
                continue
            if stat.st_size > max_file_size:
                continue
            files.append(
                RepoFile(
                    rel_posix=rel_posix,
                    path=file_path,
                    size=int(stat.st_size),
                    mtime_ns=int(stat.st_mtime_ns),
                    is_test=is_test,
                )
            )
            if max_files is not None and len(files) >= max_files:
                truncated = True
                break
        if truncated:
            break

    return RepoScanResult(
        files=tuple(files),
        manifests=manifests,
        test_dirs=tuple(sorted(test_dirs_set)),
        truncated=truncated,
    )


def _run_git_raw(root: Path, *args: str, timeout: float = 8.0) -> tuple[int, str, bool]:
    """运行只读 Git 查询并强制关闭引号转义，同时汇报是否超时。"""
    try:
        completed = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout, False
    except subprocess.TimeoutExpired:
        return 1, "", True
    except (OSError, subprocess.SubprocessError):
        return 1, "", False


def run_git_porcelain_z(
    root: Path, *, timeout: float = 8.0
) -> tuple[bool, list[tuple[str, str]], bool]:
    """使用 `git status --porcelain=v1 -z -uall` 安全解析脏文件（兼容中文及重命名）。

    返回 `(ok, [(xy_status, rel_posix), ...], timed_out)`。
    """
    code, out, timed_out = _run_git_raw(
        root, "status", "--porcelain=v1", "-z", "-uall", timeout=timeout
    )
    if code != 0:
        return False, [], timed_out
    entries: list[tuple[str, str]] = []
    tokens = out.split("\x00")
    idx = 0
    while idx < len(tokens):
        rec = tokens[idx]
        idx += 1
        if not rec or len(rec) < 4:
            continue
        xy = rec[:2]
        path = rec[3:].replace("\\", "/").lstrip("./")
        if (xy[0] in {"R", "C"} or xy[1] in {"R", "C"}) and idx < len(tokens):
            # -z 模式下重命名/复制后紧跟源路径 NUL 段，跳过旧路径保留当前新路径
            idx += 1
        if path:
            entries.append((xy, path))
    return True, entries, False


def _file_digest(path: Path, *, max_hash_bytes: int = 128 * 1024) -> str:
    """计算文件内容前缀或完整 SHA-256 与 (size, mtime_ns)，保证二次编辑必变。"""
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    hasher = hashlib.sha256()
    hasher.update(f"{stat.st_size}:{stat.st_mtime_ns}:".encode())
    with contextlib.suppress(OSError), path.open("rb") as handle:
        hasher.update(handle.read(max_hash_bytes))
    return hasher.hexdigest()[:24]


def compute_repo_fingerprint(
    root: Path,
    *,
    cache_version: int = 1,
    extra_data: Any = None,
    include_tests: bool = False,
    extensions: frozenset[str] | None = None,
    max_non_git_files: int = 8000,
) -> tuple[str, bool]:
    """计算带有脏文件内容验证的仓库指纹。

    返回值 `(fingerprint, cacheable)`：当 Git 超时或目录扫描因异常截断时 `cacheable=False`，
    防止把残缺结果固化进磁盘缓存。
    """
    hasher = hashlib.sha256()
    hasher.update(f"v={cache_version}|root={root.resolve().as_posix()}\n".encode())
    if extra_data:
        hasher.update(
            json.dumps(extra_data, ensure_ascii=False, sort_keys=True, default=str).encode()
        )

    if not root.is_dir():
        return hasher.hexdigest(), False

    cacheable = True
    used_git = False
    if (root / ".git").exists():
        h_code, head_out, h_to = _run_git_raw(root, "rev-parse", "HEAD")
        s_ok, dirty_entries, s_to = run_git_porcelain_z(root)
        if h_to or s_to:
            return hasher.hexdigest(), False
        if h_code == 0 and s_ok:
            used_git = True
            hasher.update(f"head={head_out.strip()}\n".encode())
            for xy, rel in sorted(dirty_entries, key=lambda item: (item[1], item[0])):
                if not include_tests and is_test_relpath(rel):
                    continue
                if extensions is not None and Path(rel).suffix.lower() not in extensions:
                    continue
                digest = _file_digest(root / rel)
                hasher.update(f"{xy}:{rel}:{digest}\n".encode())

    if not used_git:
        scan = scan_repo(
            root,
            extensions=extensions,
            include_tests=include_tests,
            max_files=max_non_git_files,
        )
        if scan.truncated:
            cacheable = False
        for entry in scan.files:
            digest = _file_digest(entry.path)
            hasher.update(f"{entry.rel_posix}:{digest}\n".encode())

    return hasher.hexdigest(), cacheable


def read_versioned_cache(
    cache_path: Path,
    *,
    fingerprint: str,
    cache_version: int,
    payload_key: str = "data",
) -> dict[str, Any] | None:
    """按版本号与真实指纹读取缓存，不匹配则返回 None。"""
    if not cache_path.is_file():
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("cache_version") != cache_version or raw.get("fingerprint") != fingerprint:
        return None
    value = raw.get(payload_key)
    return dict(value) if isinstance(value, dict) else None


def write_versioned_cache(
    cache_path: Path,
    *,
    fingerprint: str,
    cache_version: int,
    data: dict[str, Any],
    payload_key: str = "data",
    evict_prefix: str = "",
    keep_slots: int = 2,
) -> None:
    """原子写入版本化缓存并淘汰过期同前缀槽位，防止 `.cache/` 无限膨胀。"""
    payload = {
        "schema_version": 1,
        "cache_version": cache_version,
        "fingerprint": fingerprint,
        payload_key: data,
    }
    tmp_path: Path | None = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{cache_path.name}.", dir=cache_path.parent)
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        os.replace(tmp_path, cache_path)
    except OSError:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        return

    if evict_prefix and keep_slots >= 1:
        with contextlib.suppress(OSError):
            siblings = sorted(
                (
                    path
                    for path in cache_path.parent.glob(f"{evict_prefix}*.json")
                    if path.is_file()
                ),
                key=lambda item: item.stat().st_mtime_ns,
                reverse=True,
            )
            for stale in siblings[keep_slots:]:
                if stale != cache_path:
                    stale.unlink(missing_ok=True)


def _expand_workspace_globs(root: Path, patterns: Sequence[str]) -> list[Path]:
    """展开 `pnpm-workspace.yaml` / `package.json#workspaces` / `pyproject` 中的工作区路径。"""
    found: dict[Path, None] = {}
    root_res = root.resolve()
    for raw in patterns:
        pattern = raw.strip().strip('"').strip("'").rstrip("/")
        if not pattern or pattern.startswith("!"):
            continue
        if pattern in {".", "./"}:
            found[root_res] = None
            continue
        for match in sorted(root.glob(pattern)):
            if not match.is_dir():
                continue
            if any(part.lower() in DEFAULT_SKIP_DIRS for part in match.relative_to(root).parts):
                continue
            resolved = match.resolve()
            if resolved.is_relative_to(root_res):
                found[resolved] = None
    return list(found.keys())


def _parse_pnpm_workspace_yaml(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    patterns: list[str] = []
    in_packages = False
    for raw_line in lines:
        stripped = raw_line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if re.match(r"^packages\s*:\s*$", stripped):
            in_packages = True
            continue
        if in_packages:
            if not raw_line.startswith((" ", "\t", "-")):
                break
            if stripped.startswith("-"):
                item = stripped[1:].strip().strip('"').strip("'")
                if item:
                    patterns.append(item)
    return patterns


def discover_monorepo_packages(root: Path) -> list[Path]:
    """原生发现 Monorepo 多级子包（含 pnpm / npm / yarn / bun / uv / hatch workspaces）。"""
    if not root.is_dir():
        return []
    root_res = root.resolve()
    discovered: dict[Path, None] = {root_res: None}

    patterns: list[str] = []
    patterns.extend(_parse_pnpm_workspace_yaml(root / "pnpm-workspace.yaml"))

    pkg_file = root / "package.json"
    if pkg_file.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError, UnicodeDecodeError):
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
            if isinstance(pkg, dict):
                ws_cfg = pkg.get("workspaces")
                if isinstance(ws_cfg, list):
                    patterns.extend(str(item) for item in ws_cfg if isinstance(item, str))
                elif isinstance(ws_cfg, dict):
                    raw_pkgs = ws_cfg.get("packages")
                    if isinstance(raw_pkgs, list):
                        patterns.extend(str(item) for item in raw_pkgs if isinstance(item, str))

    py_file = root / "pyproject.toml"
    if py_file.is_file() and tomllib is not None:
        with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
            with py_file.open("rb") as handle:
                py_data = tomllib.load(handle)
            tool = py_data.get("tool") if isinstance(py_data, dict) else None
            if isinstance(tool, dict):
                uv_section = tool.get("uv")
                uv_ws = (
                    uv_section.get("workspace")
                    if isinstance(uv_section, dict)
                    else None
                )
                if isinstance(uv_ws, dict) and isinstance(uv_ws.get("members"), list):
                    patterns.extend(
                        str(item) for item in uv_ws["members"] if isinstance(item, str)
                    )

    for pkg_dir in _expand_workspace_globs(root, patterns):
        if (
            (pkg_dir / "package.json").is_file()
            or (pkg_dir / "pyproject.toml").is_file()
            or any(pkg_dir.glob("requirements*.txt"))
        ):
            discovered[pkg_dir] = None

    # 补充扫描顶层至二级目录的独立清单（兼容无 workspace 声明的复合仓）
    with contextlib.suppress(OSError):
        for child in sorted(root.iterdir()):
            if (
                not child.is_dir()
                or child.name.startswith(".")
                or child.name.lower() in DEFAULT_SKIP_DIRS
            ):
                continue
            if (
                (child / "package.json").is_file()
                or (child / "pyproject.toml").is_file()
                or any(child.glob("requirements*.txt"))
            ):
                discovered[child.resolve()] = None

    return list(discovered.keys())


def detect_package_runner(pkg_root: Path, workspace_root: Path | None = None) -> str:
    """按实际存在的 lockfile（支持向上回溯至 workspace 根）选择包运行器。"""
    candidates = [pkg_root]
    if workspace_root is not None and workspace_root.resolve() != pkg_root.resolve():
        candidates.append(workspace_root)
    for base in candidates:
        if (base / "bun.lock").is_file() or (base / "bun.lockb").is_file():
            return "bun"
        if (base / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (base / "yarn.lock").is_file():
            return "yarn"
        if (base / "package-lock.json").is_file():
            return "npm"
        if (base / "uv.lock").is_file():
            return "uv"
        if (base / "poetry.lock").is_file():
            return "poetry"
    return "python" if (pkg_root / "pyproject.toml").is_file() else "npm"
