"""确定性 AST 扫描：把源码转换为项目理解条目。"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import PurePosixPath

from aitest.application.project.understanding.catalog import (
    _build_functions,
    _build_goals,
    _build_intents,
    _class_facts,
    _derive_dimensions,
    _framework_gap,
    _function_facts,
    _http_name,
    _http_outcome,
    _merge_entries,
    _non_python_gap,
    _object_from_name,
    _object_from_route,
)
from aitest.application.project.understanding.codec import (
    _require_text,
    understanding_content_digest,
)
from aitest.application.project.understanding.errors import ProjectUnderstandingError
from aitest.application.project.understanding.shared import (
    _IGNORED_DIRECTORY_NAMES,
    _NON_PYTHON_SOURCE_SUFFIXES,
    _PLACEHOLDER_DIGEST,
    _SUPPORTED_FRAMEWORKS,
    _TEST_DIRECTORY_NAMES,
    _UNSUPPORTED_FRAMEWORKS,
    _Entry,
    _Facts,
    _first_string,
    _is_main_guard,
    _keyword_string,
    _line,
    _looks_like_route_decorator,
    _PublicCandidate,
    _root_parts,
    _route_spec,
    _RouteSpec,
)
from aitest.domain.planning.understanding import (
    BusinessFunctionCatalog,
    BusinessFunctionKind,
    CapabilityGap,
    FunctionTrigger,
    ProjectSignalOrigin,
    ProjectSignals,
    ProjectUnderstanding,
)


def scan_sources(
    *,
    files: Mapping[str, bytes | str],
    source_snapshot_id: str,
    understanding_id: str,
    revision: int,
    scope: str,
    selected_paths: Sequence[str] = (),
    all_paths: Sequence[str] | None = None,
) -> ProjectUnderstanding:
    """Build one understanding from already-read source bytes.

    ``files`` holds Python source bytes; ``all_paths`` is the complete selected
    file list (non-Python files are read only as paths, never as content).
    When ``all_paths`` is omitted the keys of ``files`` are used.
    """
    _require_text(source_snapshot_id, "source_snapshot_id")
    _require_text(understanding_id, "understanding_id")
    _require_text(scope, "scope")
    if type(revision) is not int or revision < 1:
        raise ProjectUnderstandingError("project understanding revision must be >= 1")
    selection = _selection(selected_paths)
    declared = tuple(all_paths) if all_paths is not None else tuple(files)
    for path in (*declared, *files):
        _require_safe_relative(path)
    normalised_files = {
        _normalise_relative(path): content for path, content in files.items()
    }
    paths = tuple(
        sorted({_normalise_relative(path) for path in (*declared, *normalised_files)})
    )
    selected = tuple(path for path in paths if _matches_selection(path, selection))

    entries: list[_Entry] = []
    unknown: list[str] = []
    gaps: list[CapabilityGap] = []
    frameworks: set[str] = set()
    stack: set[str] = set()
    entry_files: set[str] = set()
    python_files: list[str] = []
    for relative in selected:
        if _ignored_path(relative):
            continue
        if not relative.lower().endswith(".py"):
            suffix = PurePosixPath(relative).suffix.lower()
            if suffix in _NON_PYTHON_SOURCE_SUFFIXES and not _is_test_path(relative):
                gaps.append(_non_python_gap(relative, suffix))
            continue
        python_files.append(relative)
        raw = normalised_files.get(relative)
        if raw is None:
            raise ProjectUnderstandingError(f"Python source bytes are missing: {relative}")
        try:
            tree = ast.parse(raw, filename=relative)
        except (SyntaxError, ValueError, TypeError, RecursionError) as error:
            line = getattr(error, "lineno", None)
            location = f"{relative}:{line}" if type(line) is int else relative
            unknown.append(f"{location}: Python 源码无法解析（{type(error).__name__}）")
            continue
        if _is_test_path(relative):
            continue
        scanner = _ModuleScanner(relative)
        scanner.visit(tree)
        scanner.finish()
        entries.extend(scanner.entries)
        unknown.extend(scanner.unknown)
        gaps.extend(scanner.gaps)
        frameworks.update(scanner.frameworks)
        stack.update(scanner.stack)
        if scanner.entries:
            entry_files.add(relative)

    merged = _merge_entries(entries)
    merged.sort(key=lambda entry: (entry.path, entry.qualname, entry.name))
    functions = _build_functions(
        merged,
        understanding_id=understanding_id,
        revision=revision,
        source_snapshot_id=source_snapshot_id,
    )
    intents = _build_intents(functions, revision)
    goals = _build_goals(intents, functions)
    if python_files:
        stack.add("python")
    stack.update(frameworks)
    capabilities = sorted({entry.trigger.value for entry in merged})
    risk_signals = sorted(
        {
            dimension
            for entry in merged
            for dimension in entry.dimensions
            if dimension not in {"normal", "error"}
        }
    )
    if any(gap.missing_capability.startswith("scan:non_python") for gap in gaps):
        capabilities.append("non_python_source")
    capabilities = sorted(set(capabilities))
    evidence = sorted(
        {
            ref
            for entry in merged
            for ref in entry.source_refs
        }
        | {ref for gap in gaps for ref in gap.source_refs}
    )
    confidence: dict[str, float] = {}
    for name in sorted(stack):
        confidence[name] = 0.9
    for name in capabilities:
        confidence.setdefault(name, 0.8)
    for name in risk_signals:
        confidence.setdefault(name, 0.7)
    signals = ProjectSignals(
        detected_stack=tuple(sorted(stack)),
        entry_candidates=tuple(sorted(entry_files)),
        capabilities=tuple(capabilities),
        risk_signals=tuple(risk_signals),
        confidence=confidence,
        evidence_refs=tuple(evidence),
        origin=ProjectSignalOrigin.DETERMINISTIC,
    )
    if not merged:
        if not python_files:
            unknown.append(f"{scope}: 未发现可扫描的 Python 文件")
        elif not unknown:
            unknown.append(f"{scope}: 扫描完成但未识别到任何业务入口")
    unique_unknown = tuple(sorted(set(unknown)))
    unique_gaps = tuple(
        sorted({gap.gap_id: gap for gap in gaps}.values(), key=lambda gap: gap.gap_id)
    )
    placeholder = ProjectUnderstanding(
        understanding_id=understanding_id,
        revision=revision,
        source_snapshot_id=source_snapshot_id,
        scope=scope,
        project_signals=signals,
        catalog=BusinessFunctionCatalog(
            understanding_id=understanding_id,
            revision=revision,
            functions=functions,
        ),
        test_intents=intents,
        goal_candidates=goals,
        capability_gaps=unique_gaps,
        unknown_areas=unique_unknown,
        digest=_PLACEHOLDER_DIGEST,
    )
    return replace(placeholder, digest=understanding_content_digest(placeholder))

class _ModuleScanner(ast.NodeVisitor):
    """One-file deterministic scan; no imports are executed and no model is called."""

    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.entries: list[_Entry] = []
        self.unknown: list[str] = []
        self.gaps: list[CapabilityGap] = []
        self.frameworks: set[str] = set()
        self.stack: set[str] = set()
        self.scope: list[str] = []
        self.public: list[_PublicCandidate] = []
        self.click_modules: set[str] = set()
        self.click_names: set[str] = set()
        self.click_groups: set[str] = set()
        self.has_urlpatterns = False
        self.has_main_guard = False
        self._gap_ids: set[str] = set()

    # -- imports -------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._import_name(alias.name, alias.asname)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._import_name(module, None)
        root = module.split(".")[0].lower()
        if root == "click":
            for alias in node.names:
                if alias.name in {"command", "group"}:
                    self.click_names.add(alias.asname or alias.name)
        if node.level == 0 and module.split(".")[0].lower() == "click" and any(
            alias.name == "*" for alias in node.names
        ):
            self.click_names.update({"command", "group"})
        self.generic_visit(node)

    def _import_name(self, name: str, asname: str | None) -> None:
        root = name.split(".")[0].lower()
        if not root:
            return
        if root in _SUPPORTED_FRAMEWORKS:
            self.frameworks.add(root)
            self.stack.add(root)
        if root == "argparse":
            self.stack.add("argparse")
        if root == "click":
            self.frameworks.add("click")
            self.stack.add("click")
            self.click_modules.add(asname or "click")
        if root in _UNSUPPORTED_FRAMEWORKS:
            self._add_framework_gap(root)

    # -- functions / classes ------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._qualname(node.name)
        if not self.scope and not node.name.startswith("_"):
            self.public.append(_PublicCandidate(node, qualname, True))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = self._qualname(node.name)
        facts = _function_facts(node)
        for decorator in node.decorator_list:
            route_shape = _looks_like_route_decorator(decorator)
            if route_shape:
                spec = _route_spec(decorator)
                if spec is None:
                    self.unknown.append(
                        f"{self.relative}:{_line(decorator)}: 无法识别的路由装饰器"
                    )
                    continue
                self._add_http_entry(qualname, spec, facts)
                continue
            click = self._click_spec(decorator)
            if click is not None:
                command, is_group = click
                if is_group:
                    self.click_groups.add(node.name)
                self._add_click_entry(
                    qualname, command or node.name, facts, _line(decorator)
                )
        if not self.scope and not node.name.startswith("_"):
            self.public.append(_PublicCandidate(node, qualname, False))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "urlpatterns":
                self.has_urlpatterns = True
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if not self.scope and _is_main_guard(node.test):
            self.has_main_guard = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_parser":
            command = _first_string(node) or _keyword_string(node, "name")
            if command is None:
                self.unknown.append(
                    f"{self.relative}:{_line(node)}: argparse add_parser 缺少可解析的命令名"
                )
            else:
                self._add_argparse_entry(node, command)
        self.generic_visit(node)

    # -- finish --------------------------------------------------------

    def finish(self) -> None:
        if self.has_urlpatterns and (
            "django" in self.frameworks or self.relative.endswith("urls.py")
        ):
            self._add_framework_gap("django")
        if self.entries:
            return
        for candidate in self.public:
            self._add_fallback_entry(candidate)

    # -- entry builders -------------------------------------------------

    def _add_http_entry(
        self,
        qualname: str,
        spec: _RouteSpec,
        facts: _Facts,
    ) -> None:
        if spec.path is None:
            self.unknown.append(
                f"{self.relative}:{spec.line}: 路由装饰器缺少可解析的路径"
            )
        path = spec.path or ""
        methods = spec.methods
        name = _http_name(methods, path, qualname)
        business_object = _object_from_route(path) or _object_from_name(qualname)
        dimensions = _derive_dimensions(
            name=name,
            path=path,
            trigger=FunctionTrigger.HTTP,
            methods=methods,
            facts=facts,
        )
        self.entries.append(
            _Entry(
                trigger=FunctionTrigger.HTTP,
                kind=BusinessFunctionKind.OPERATION,
                path=path,
                qualname=qualname,
                name=name,
                business_object=business_object,
                expected_outcome=_http_outcome(methods, business_object),
                conditions=tuple(f"http_method={method}" for method in methods),
                methods=methods,
                dimensions=dimensions,
                source_refs=(f"{self.relative}:{spec.line}",),
                confidence=0.9,
                explicit=True,
            )
        )

    def _add_click_entry(
        self,
        qualname: str,
        command: str,
        facts: _Facts,
        line: int,
    ) -> None:
        name = command
        path = f"cli:{command}"
        dimensions = _derive_dimensions(
            name=name,
            path=path,
            trigger=FunctionTrigger.CLI,
            methods=(),
            facts=facts,
        )
        self.entries.append(
            _Entry(
                trigger=FunctionTrigger.CLI,
                kind=BusinessFunctionKind.OPERATION,
                path=path,
                qualname=qualname,
                name=name,
                business_object=_object_from_name(command),
                expected_outcome="命令执行成功并返回结果",
                conditions=(f"cli_command={command}",),
                methods=(),
                dimensions=dimensions,
                source_refs=(f"{self.relative}:{line}",),
                confidence=0.9,
                explicit=True,
            )
        )

    def _add_argparse_entry(self, node: ast.Call, command: str) -> None:
        qualname = ".".join((*self.scope, command)) if self.scope else command
        path = f"cli:{command}"
        facts = _Facts(
            parameters=(),
            call_names=(),
            decorator_names=(),
            is_async=False,
        )
        dimensions = _derive_dimensions(
            name=command,
            path=path,
            trigger=FunctionTrigger.CLI,
            methods=(),
            facts=facts,
        )
        self.entries.append(
            _Entry(
                trigger=FunctionTrigger.CLI,
                kind=BusinessFunctionKind.OPERATION,
                path=path,
                qualname=qualname,
                name=command,
                business_object=_object_from_name(command),
                expected_outcome="命令执行成功并返回结果",
                conditions=(f"cli_command={command}",),
                methods=(),
                dimensions=dimensions,
                source_refs=(f"{self.relative}:{_line(node)}",),
                confidence=0.9,
                explicit=True,
            )
        )

    def _add_fallback_entry(self, candidate: _PublicCandidate) -> None:
        node = candidate.node
        facts = _class_facts(node) if isinstance(node, ast.ClassDef) else _function_facts(node)
        trigger = (
            FunctionTrigger.CLI
            if not candidate.is_class
            and (node.name == "main" or self.has_main_guard)
            else FunctionTrigger.LIBRARY
        )
        kind = (
            BusinessFunctionKind.CAPABILITY
            if candidate.is_class
            else BusinessFunctionKind.OPERATION
        )
        path = f"module:{candidate.qualname}"
        name = candidate.qualname
        dimensions = _derive_dimensions(
            name=name,
            path=path,
            trigger=trigger,
            methods=(),
            facts=facts,
        )
        self.entries.append(
            _Entry(
                trigger=trigger,
                kind=kind,
                path=path,
                qualname=candidate.qualname,
                name=name,
                business_object=_object_from_name(candidate.qualname),
                expected_outcome=(
                    "命令执行成功并返回结果"
                    if trigger is FunctionTrigger.CLI
                    else "返回预期业务结果"
                ),
                conditions=(),
                methods=(),
                dimensions=dimensions,
                source_refs=(f"{self.relative}:{_line(node)}",),
                confidence=0.5,
                explicit=False,
            )
        )

    # -- helpers --------------------------------------------------------

    def _add_framework_gap(self, framework: str) -> None:
        gap = _framework_gap(self.relative, framework)
        if gap.gap_id not in self._gap_ids:
            self._gap_ids.add(gap.gap_id)
            self.gaps.append(gap)

    def _qualname(self, name: str) -> str:
        return ".".join((*self.scope, name)) if self.scope else name

    def _click_spec(self, decorator: ast.expr) -> tuple[str | None, bool] | None:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            roles = _root_parts(target)
            root = roles[0].lower() if roles else ""
            attribute = target.attr.lower()
            if attribute not in {"command", "group"}:
                return None
            known_module = root in {name.lower() for name in self.click_modules}
            known_group = attribute == "command" and root in self.click_groups
            if not (known_module or known_group):
                return None
            is_group = attribute == "group"
        elif isinstance(target, ast.Name):
            if target.id not in self.click_names:
                return None
            is_group = target.id.lower() == "group"
        else:
            return None
        explicit: str | None = None
        if isinstance(decorator, ast.Call):
            explicit = _first_string(decorator) or _keyword_string(decorator, "name")
        return explicit, is_group

def _selection(values: Sequence[str]) -> tuple[str, ...]:
    selection: list[str] = []
    for value in values:
        _require_text(value, "selected_path")
        selection.append(_normalise_relative(value).rstrip("/"))
    return tuple(dict.fromkeys(selection))

def _matches_selection(relative: str, selection: Sequence[str]) -> bool:
    return not selection or any(
        item in {".", ""} or relative == item or relative.startswith(item + "/")
        for item in selection
    )

def _normalise_relative(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ProjectUnderstandingError("source path must be a nonempty string")
    return path.replace("\\", "/")

def _require_safe_relative(path: str) -> None:
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ProjectUnderstandingError("source path must be a nonempty string")
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise ProjectUnderstandingError(f"source path escapes its snapshot root: {path}")

def _ignored_path(relative: str) -> bool:
    parts = relative.lower().split("/")
    return any(part in _IGNORED_DIRECTORY_NAMES for part in parts[:-1])

def _is_test_path(relative: str) -> bool:
    parts = relative.lower().split("/")
    if any(part in _TEST_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")
