"""跑前测试脚本静态安检器（ScriptLinter）。

在执行生成脚本之前通过 AST 与静态规则拦截无断言、恒真断言、
吞异常空 except，以及只判 2xx 却无写后读回校验的“水脚本”。
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from aitest.security import _assertion_profile, _is_trivial_assert, script_has_assertion

_WRITE_METHOD_RE = re.compile(
    r"(?:\.(?:post|put|patch|delete)\s*\(|method\s*=\s*[\"'](?:POST|PUT|PATCH|DELETE)[\"'])",
    re.IGNORECASE,
)
_READBACK_OR_BODY_RE = re.compile(
    r"(?:\.get\s*\(|method\s*=\s*[\"']GET[\"']|\.json\s*\(|json\.loads\s*\(|\.text\b|\.content\b|\.read\s*\()",
    re.IGNORECASE,
)
_CLIENT_ERROR_STATUS_RE = re.compile(r"\b4\d{2}\b")


@dataclass(frozen=True)
class LintIssue:
    """单条跑前脚本静态安检发现项。"""

    rule: str
    severity: Literal["error", "warning"]
    message: str
    line: int = 0


class ScriptLinter(Protocol):
    """脚本静态安检器协议接口（为阶段 2 多语言安检插槽预留）。"""

    extensions: tuple[str, ...]

    def lint(self, content: str) -> list[LintIssue]:
        """检查给定脚本文本并返回发现的问题清单。"""
        ...


def _is_swallowing_handler(handler: ast.ExceptHandler) -> bool:
    """判定 except 块是否吞掉异常（仅 pass/continue/.../print 而无断言、重抛或状态处理）。"""
    body = handler.body
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, (ast.Pass, ast.Continue)):
            continue
        if isinstance(stmt, ast.Expr):
            val = stmt.value
            if isinstance(val, ast.Constant) and (
                val.value is Ellipsis or isinstance(val.value, str)
            ):
                continue
            if isinstance(val, ast.Call):
                func = val.func
                func_name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else ""
                )
                if func_name in {"print", "debug", "info", "warning", "warn"}:
                    continue
        return False
    return True


def _has_two_stage_functions(tree: ast.AST) -> bool:
    """检测 Python AST 顶层是否同时定义了 setup 与 verify 两阶段函数（且非 pytest 测试）。"""
    if not isinstance(tree, ast.Module):
        return False
    top_funcs: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith(("test_", "check_")):
                return False
            top_funcs.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            return False
    return "setup" in top_funcs and "verify" in top_funcs


class PythonScriptLinter:
    """面向 `.py` 验证脚本的 AST 深度静态安检器。"""

    extensions: tuple[str, ...] = (".py",)

    def lint(self, content: str) -> list[LintIssue]:
        issues: list[LintIssue] = []
        if not content.strip():
            return [
                LintIssue(
                    rule="empty_script",
                    severity="error",
                    message="脚本内容为空，无法证明验收项",
                    line=1,
                )
            ]
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            return [
                LintIssue(
                    rule="syntax_error",
                    severity="error",
                    message=f"脚本存在语法错误（第 {exc.lineno or 1} 行）：{exc.msg}",
                    line=exc.lineno or 1,
                )
            ]

        assert_count, _raises_count, _eq_count, _codes, has_trivial = _assertion_profile(tree)
        if assert_count == 0:
            issues.append(
                LintIssue(
                    rule="no_assertion",
                    severity="error",
                    message="脚本不包含任何断言（assert / pytest.raises），不能充当通过证据",
                    line=1,
                )
            )
        if has_trivial:
            trivial_line = 1
            for node in ast.walk(tree):
                if isinstance(node, ast.Assert) and _is_trivial_assert(node):
                    trivial_line = getattr(node, "lineno", 1)
                    break
            issues.append(
                LintIssue(
                    rule="trivial_assertion",
                    severity="error",
                    message="脚本包含恒真或同值退化断言（例如 assert True / assert x == x）",
                    line=trivial_line,
                )
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_swallowing_handler(node):
                issues.append(
                    LintIssue(
                        rule="swallowed_exception",
                        severity="error",
                        message="except 代码块静默吞掉异常且未断言或上抛，可能掩盖真实失败",
                        line=getattr(node, "lineno", 1),
                    )
                )
                break

        if (
            _WRITE_METHOD_RE.search(content)
            and not _READBACK_OR_BODY_RE.search(content)
            and not _CLIENT_ERROR_STATUS_RE.search(content)
        ):
            issues.append(
                LintIssue(
                    rule="write_without_verify",
                    severity="warning",
                    message="脚本发起了写请求但未检测到响应体解析或二次 GET 读回持久化验证",
                    line=1,
                )
            )
        return issues


_BUILT_ASSERT_JS_RE = re.compile(
    r"(?:\bexpect\s*\(|\bassert(?:\.\w+)?\s*\(|\.(?:toBe|toEqual|toStrictEqual|toContain|"
    r"toHave|toMatch|toThrow|toBeTruthy|toBeVisible|toBeDefined|toBeNull)\b)",
)
_TRIVIAL_JS_ASSERT_RE = re.compile(
    r"(?:\bexpect\s*\(\s*(?:true|false|1|0)\s*\)\s*\.\s*(?:toBe|toEqual|toStrictEqual)\s*\("
    r"\s*(?:true|false|1|0)\s*\)|\bexpect\s*\(\s*true\s*\)\s*\.\s*toBeTruthy\s*\(|"
    r"\bassert\s*\.\s*(?:ok|strictEqual|equal)\s*\(\s*true\b)",
    re.IGNORECASE,
)
_EMPTY_JS_CATCH_RE = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?://[^\n]*)?\s*\}",
)


class JsTsScriptLinter:
    """面向 `.js` / `.ts` / `.mjs` / `.cjs`（含 Playwright Spec）脚本的静态安检器。"""

    extensions: tuple[str, ...] = (".js", ".ts", ".mjs", ".cjs")

    def lint(self, content: str) -> list[LintIssue]:
        issues: list[LintIssue] = []
        if not content.strip():
            return [
                LintIssue(
                    rule="empty_script",
                    severity="error",
                    message="JS/TS 脚本内容为空，无法证明验收项",
                    line=1,
                )
            ]
        if not _BUILT_ASSERT_JS_RE.search(content):
            issues.append(
                LintIssue(
                    rule="no_assertion",
                    severity="error",
                    message="JS/TS 脚本不包含 expect/assert 断言，不能充当通过证据",
                    line=1,
                )
            )
        if _TRIVIAL_JS_ASSERT_RE.search(content):
            issues.append(
                LintIssue(
                    rule="trivial_assertion",
                    severity="error",
                    message="JS/TS 脚本包含恒真断言（如 expect(true).toBe(true)）",
                    line=1,
                )
            )
        if _EMPTY_JS_CATCH_RE.search(content):
            issues.append(
                LintIssue(
                    rule="swallowed_exception",
                    severity="error",
                    message="JS/TS 脚本存在空 catch {} 块，可能静默吞掉异常",
                    line=1,
                )
            )
        return issues


_BUILTIN_LINTERS: tuple[ScriptLinter, ...] = (
    PythonScriptLinter(),
    JsTsScriptLinter(),
)


def has_two_stage_entrypoints(content: str) -> bool:
    """判断 Python 脚本是否在顶层定义了 setup() 与 verify() 两阶段入口。"""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    return _has_two_stage_functions(tree)


def lint_script(name: str, content: str) -> list[LintIssue]:
    """按文件扩展名调度对应安检器检查脚本内容。"""
    suffix = Path(name.strip()).suffix.lower() if name.strip() else ".py"
    for linter in _BUILTIN_LINTERS:
        if suffix in linter.extensions:
            return linter.lint(content)
    return []


def blocking_lint_errors(name: str, content: str) -> list[str]:
    """仅提取需要阻断执行或打回重写的高危错误消息列表。"""
    return [
        f"第{issue.line}行：{issue.message}" if issue.line else issue.message
        for issue in lint_script(name, content)
        if issue.severity == "error"
    ]


def assess_assertion_strength(case: Any) -> Literal["strong", "weak", "unverified"]:
    """确定性评定单条用例的断言强度（strong / weak / unverified）。"""
    method = str(getattr(case, "verify_method", "") or "").strip()
    if method == "manual" or method.startswith("manual:"):
        return "unverified"
    if not getattr(case, "expectation_verified", False) and getattr(case, "layer", "") != "L1":
        return "unverified"
    if method.startswith("http:"):
        raw = method[len("http:") :].strip()
        try:
            reqs = json.loads(raw)
        except json.JSONDecodeError:
            return "unverified"
        if not isinstance(reqs, list) or not reqs:
            return "unverified"
        assert_reqs = [
            r for r in reqs if isinstance(r, dict) and str(r.get("stage") or "assert") != "setup"
        ]
        if not assert_reqs:
            return "weak"
        has_status = False
        has_field_or_negative_status = False
        for req in assert_reqs:
            expects = req.get("expect")
            if not isinstance(expects, list):
                continue
            for exp in expects:
                if not isinstance(exp, dict):
                    continue
                path = str(exp.get("path") or "").strip()
                if path == "status":
                    has_status = True
                    val = exp.get("value")
                    if isinstance(val, int) and val >= 400:
                        has_field_or_negative_status = True
                elif path:
                    has_field_or_negative_status = True
        if has_status and has_field_or_negative_status:
            return "strong"
        return "weak"
    if method.startswith("script:"):
        content = str(getattr(case, "script_content", "") or "")
        if not content.strip() or not script_has_assertion(content):
            return "weak"
        issues = lint_script(str(getattr(case, "script_name", "") or ""), content)
        weak_codes = {"no_assertion", "trivial_assertion", "swallowed_exception"}
        if any(issue.rule in weak_codes for issue in issues):
            return "weak"
        return "strong"
    if method.startswith(("command:", "pytest:", "junit:", "playwright:")):
        if getattr(case, "coverage_kind", None) in {"file", "shared", "unverified"}:
            return "weak"
        return "strong"
    return "unverified"
