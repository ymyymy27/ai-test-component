"""Report current-status docs whose numeric claims disagree with the code.

**只扫现行状态文档**（现状页、`AGENTS.md`、README、交接说明）：这些文档描述"现在是什么样"，
一旦与代码矛盾就会误导后续工作（本项目把文档当合同）。**不碰历史留痕**——修改日志、归档交接、
`docs/validation/` 里的记录都是当时的真实结果，改它们等于篡改历史。

**只报告、不自动改写**：文档修订需要人判断（一处数字变了，往往还要改上下文、边界与指向）。

用法：

    .venv/Scripts/python.exe scripts/check_docs_facts.py            # 快检（不跑 mypy / 不装配核心）
    .venv/Scripts/python.exe scripts/check_docs_facts.py --full     # 另跑 mypy 与真实装配取动作数

退出码 0 表示所有**已知断言**都与代码一致；1 表示有差异或无法判定。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 现行状态文档。注意**不要**把修改日志/归档/验证记录加进来。
DOCS = (
    "docs/现状.md",
    "AGENTS.md",
    "README.md",
    "docs/一期工程整改/04-交接/交接说明-2026-10-12.md",
)


@dataclass(frozen=True, slots=True)
class Rule:
    """一条"文档断言 → 代码事实"的对应关系。"""

    label: str
    pattern: re.Pattern[str]
    fact: str
    needs_full: bool = False


RULES = (
    Rule(
        "src 模块数",
        re.compile(r"\|\s*`src/aitest`\s*\|\s*\*\*([\d,]+)\*\*\s*个模块"),
        "modules",
    ),
    Rule(
        "src 总行数",
        re.compile(r"\|\s*`src/aitest`\s*\|[^|\n]*\*\*([\d,]+)\*\*\s*行"),
        "lines",
    ),
    Rule(
        "mypy 检查文件数",
        re.compile(r"`mypy`[（(]\s*strict[，,]\s*(\d+)\s*文件[）)]"),
        "mypy_files",
        needs_full=True,
    ),
    Rule(
        "测试节点数",
        re.compile(r"(\d+)\s*个节点"),
        "nodes",
    ),
    Rule(
        "默认可派发动作数",
        re.compile(r"默认可派发[^\n]{0,40}?\*\*(\d+)\*\*"),
        "actions",
        needs_full=True,
    ),
)


def _number(raw: str) -> int:
    return int(raw.replace(",", ""))


def module_count() -> int:
    return len(list((ROOT / "src/aitest").rglob("*.py")))


def line_count() -> int:
    total = 0
    for path in (ROOT / "src/aitest").rglob("*.py"):
        total += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return total


def mypy_file_count() -> int | None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        # mypy 在本机 Windows 控制台会输出非 UTF-8 字节（此前实测 0xc1 解码失败），
        # 因此容错替换——我们要的是汇总行里的数字，不是逐字复现其输出。
        errors="replace",
    )
    # mypy 成功时措辞是 "no issues found in N source files"，失败时是
    # "Found ... (checked N source files)"——两种都要认，否则成功时取不到数字。
    for pattern in (r"no issues found in (\d+) source files", r"checked (\d+) source files"):
        match = re.search(pattern, result.stdout)
        if match:
            return int(match.group(1))
    return None


def node_count() -> int | None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q", "tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    # 本工程 `-q` 下**没有** "N tests collected" 汇总行，只有按文件计数（`path: N`），
    # 因此先试汇总行，再退回按文件计数求和。
    for pattern in (r"(\d+) tests? collected", r"collected (\d+) tests?"):
        matches = re.findall(pattern, result.stdout)
        if matches:
            return int(matches[-1])
    per_file = [int(value) for value in re.findall(r":\s*(\d+)\s*$", result.stdout, re.M)]
    return sum(per_file) if per_file else None


def dispatchable_action_count() -> int | None:
    """真实装配一次工作空间核心并数出可派发动作（不看字符串、不靠命名猜测）。"""

    script = """
import json, sys
from pathlib import Path
from aitest.bootstrap import assemble_workspace_core

with __import__("tempfile").TemporaryDirectory() as raw:
    assembly = assemble_workspace_core(Path(raw), instance_id="docs-facts")
    print("ACTIONS=" + json.dumps(sorted(assembly.api.handlers) + ["doctor"]))
"""
    with tempfile.TemporaryDirectory(prefix="aitest-docs-facts-") as raw:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**_environment(), "TEMP": raw, "TMP": raw},
        )
    match = re.search(r"ACTIONS=(\[.*\])", result.stdout)
    if not match:
        return None
    return len(json.loads(match.group(1)))


def _environment() -> dict[str, str]:
    import os

    return dict(os.environ)


def facts(*, full: bool) -> dict[str, int]:
    values = {
        "modules": module_count(),
        "lines": line_count(),
    }
    nodes = node_count()
    if nodes is not None:
        values["nodes"] = nodes
    if full:
        files = mypy_file_count()
        if files is not None:
            values["mypy_files"] = files
        actions = dispatchable_action_count()
        if actions is not None:
            values["actions"] = actions
    return values


def report(*, full: bool) -> int:
    actual = facts(full=full)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    print(f"产品版本 {version}")
    for key, value in sorted(actual.items()):
        print(f"  代码事实 {key} = {value}")
    print()

    problems = 0
    checked = 0
    for name in DOCS:
        path = ROOT / name
        if not path.is_file():
            print(f"X 现行状态文档不存在: {name}")
            problems += 1
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for rule in RULES:
            if rule.needs_full and not full:
                continue
            expected = actual.get(rule.fact)
            for number, line in enumerate(lines, 1):
                match = rule.pattern.search(line)
                if match is None:
                    continue
                checked += 1
                claimed = _number(match.group(1))
                if expected is None:
                    print(f"? {name}:{number} [{rule.label}] 文档称 {claimed}，但无法取到代码事实")
                    problems += 1
                elif claimed != expected:
                    print(
                        f"X {name}:{number} [{rule.label}] 文档称 {claimed}，代码为 {expected}"
                    )
                    problems += 1

    print()
    print(f"已核对断言 {checked} 处，差异或无法判定 {problems} 处")
    if not full:
        print("（快检模式：跳过了需要 --full 的 mypy 文件数与可派发动作数）")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(report(full="--full" in sys.argv[1:]))
