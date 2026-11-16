"""模块常量与扫描数据模型。"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from aitest.domain.planning.understanding import (
    BusinessFunctionKind,
    FunctionTrigger,
)

PROJECT_UNDERSTANDING_AGGREGATE: Final = "project_understanding"

PROJECT_UNDERSTANDING_SCHEMA: Final = "aitest.project-understanding/1.0"

SCANNER_REVISION: Final = "python-ast-scanner/1"

GOAL_CANDIDATE_LIMIT: Final = 5

_DIGEST_PREFIX: Final = "sha256:"

_PLACEHOLDER_DIGEST: Final = _DIGEST_PREFIX + ("0" * 64)

_DIMENSION_ORDER: Final = (
    "normal",
    "boundary",
    "error",
    "permission",
    "state_transition",
    "persistence",
    "dependency_failure",
    "concurrency",
)

_HTTP_METHOD_DECORATORS: Final = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options"}
)

_ROUTE_DECORATORS: Final = frozenset({"route", "api_route"})

_ROUTE_ROOTS: Final = frozenset({"app", "router", "api", "bp", "blueprint"})

_SUPPORTED_FRAMEWORKS: Final = frozenset({"fastapi", "flask", "click"})

_UNSUPPORTED_FRAMEWORKS: Final = frozenset(
    {
        "aiohttp",
        "bottle",
        "django",
        "falcon",
        "litestar",
        "pyramid",
        "sanic",
        "starlette",
        "tornado",
    }
)

_NON_PYTHON_SOURCE_SUFFIXES: Final = frozenset(
    {
        ".bat",
        ".c",
        ".cc",
        ".cmd",
        ".cpp",
        ".cs",
        ".go",
        ".graphql",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".m",
        ".mm",
        ".php",
        ".proto",
        ".ps1",
        ".pyi",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)

_IGNORED_DIRECTORY_NAMES: Final = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)

_TEST_DIRECTORY_NAMES: Final = frozenset({"test", "tests", "__tests__"})

_WRITE_TOKENS: Final = frozenset(
    {
        "add",
        "approve",
        "archive",
        "cancel",
        "close",
        "commit",
        "create",
        "delete",
        "disable",
        "enable",
        "insert",
        "migrate",
        "patch",
        "persist",
        "publish",
        "put",
        "refund",
        "register",
        "reject",
        "remove",
        "restore",
        "save",
        "ship",
        "submit",
        "transition",
        "update",
        "upload",
        "write",
    }
)

_STATE_TOKENS: Final = frozenset(
    {
        "activate",
        "archive",
        "cancel",
        "close",
        "deactivate",
        "disable",
        "enable",
        "pay",
        "publish",
        "refund",
        "reject",
        "restore",
        "ship",
        "state",
        "status",
        "submit",
        "transition",
    }
)

_PERMISSION_TOKENS: Final = frozenset(
    {
        "admin",
        "authorize",
        "auth",
        "current_user",
        "guard",
        "jwt",
        "login",
        "permission",
        "protected",
        "requires",
        "role",
        "token",
    }
)

_EXTERNAL_TOKENS: Final = frozenset(
    {
        "aiohttp",
        "boto3",
        "dependency",
        "external",
        "grpc",
        "http_client",
        "httpx",
        "kafka",
        "pika",
        "redis",
        "remote",
        "requests",
        "socket",
        "third_party",
        "upstream",
        "urllib",
        "webhook",
    }
)

_RISK_WORDS: Final = frozenset(
    {
        "account",
        "auth",
        "cancel",
        "delete",
        "invoice",
        "money",
        "order",
        "pay",
        "payment",
        "permission",
        "refund",
        "role",
        "user",
    }
)

_OBJECT_SKIP_TOKENS: Final = frozenset(
    {
        "add",
        "api",
        "build",
        "calculate",
        "compute",
        "create",
        "delete",
        "do",
        "fetch",
        "find",
        "get",
        "handle",
        "health",
        "list",
        "load",
        "main",
        "make",
        "parse",
        "process",
        "read",
        "register",
        "remove",
        "run",
        "save",
        "search",
        "send",
        "serve",
        "set",
        "show",
        "start",
        "stop",
        "submit",
        "update",
        "v1",
        "v2",
        "v3",
        "validate",
    }
)

@dataclass(frozen=True, slots=True)
class _RouteSpec:
    path: str | None
    methods: tuple[str, ...]
    line: int

@dataclass(frozen=True, slots=True)
class _Facts:
    parameters: tuple[str, ...]
    call_names: tuple[str, ...]
    decorator_names: tuple[str, ...]
    is_async: bool

@dataclass(frozen=True, slots=True)
class _Entry:
    trigger: FunctionTrigger
    kind: BusinessFunctionKind
    path: str
    qualname: str
    name: str
    business_object: str
    expected_outcome: str
    conditions: tuple[str, ...]
    methods: tuple[str, ...]
    dimensions: tuple[str, ...]
    source_refs: tuple[str, ...]
    confidence: float
    explicit: bool

@dataclass(frozen=True, slots=True)
class _PublicCandidate:
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    qualname: str
    is_class: bool


def _call_names(nodes: Iterable[ast.AST | None]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        if node is None:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _call_name(child.func)
            if name:
                names.append(name)
            for argument in child.args:
                argument_name = _call_name(argument)
                if argument_name:
                    names.append(argument_name)
            for keyword in child.keywords:
                keyword_name = _call_name(keyword.value)
                if keyword_name:
                    names.append(keyword_name)
    return names

def _call_name(node: ast.expr) -> str:
    return _dotted(node)

def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    if isinstance(node, ast.Subscript):
        return _dotted(node.value)
    return ""

def _root_parts(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_root_parts(node.value), node.attr)
    if isinstance(node, ast.Call):
        return _root_parts(node.func)
    if isinstance(node, ast.Subscript):
        return _root_parts(node.value)
    return ()

def _looks_like_route_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not isinstance(target, ast.Attribute):
        return False
    return target.attr.lower() in _HTTP_METHOD_DECORATORS | _ROUTE_DECORATORS

def _route_spec(decorator: ast.expr) -> _RouteSpec | None:
    call = decorator if isinstance(decorator, ast.Call) else None
    target = call.func if call is not None else decorator
    if not isinstance(target, ast.Attribute):
        return None
    attribute = target.attr.lower()
    if attribute not in _HTTP_METHOD_DECORATORS | _ROUTE_DECORATORS:
        return None
    parts = tuple(part.lower() for part in _root_parts(target))
    if not any(
        part in _ROUTE_ROOTS
        or part.endswith("router")
        or part.endswith("_app")
        or part.endswith("app")
        or part.endswith("blueprint")
        for part in parts
    ):
        return None
    path: str | None = None
    methods: tuple[str, ...]
    if attribute in _HTTP_METHOD_DECORATORS:
        methods = (attribute.upper(),)
    else:
        methods = _methods_keyword(call) if call is not None else ()
        if not methods:
            methods = ("GET",)
    if call is not None:
        path = _first_string(call) or _keyword_string(call, "path") or _keyword_string(
            call, "rule"
        )
    return _RouteSpec(path=path, methods=methods, line=_line(decorator))

def _methods_keyword(call: ast.Call | None) -> tuple[str, ...]:
    if call is None:
        return ()
    for keyword in call.keywords:
        if keyword.arg != "methods":
            continue
        value = keyword.value
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            methods = tuple(
                element.value.upper()
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
            return tuple(dict.fromkeys(methods))
    return ()

def _first_string(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None

def _keyword_string(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None

def _is_main_guard(node: ast.expr) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    if len(node.ops) != 1 or not isinstance(node.comparators[0], ast.Constant):
        return False
    left = node.left
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(node.ops[0], ast.Eq)
        and node.comparators[0].value == "__main__"
    )

def _line(node: ast.AST) -> int:
    line = getattr(node, "lineno", None)
    return line if type(line) is int and line > 0 else 0

def _tokens(value: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("_", " ")
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", text) if token]
