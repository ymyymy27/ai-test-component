"""从扫描条目构造业务功能、测试意图、目标与缺口。"""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import replace

from aitest.application.project.understanding.shared import (
    _DIMENSION_ORDER,
    _EXTERNAL_TOKENS,
    _OBJECT_SKIP_TOKENS,
    _PERMISSION_TOKENS,
    _RISK_WORDS,
    _STATE_TOKENS,
    _WRITE_TOKENS,
    GOAL_CANDIDATE_LIMIT,
    SCANNER_REVISION,
    _call_name,
    _call_names,
    _dotted,
    _Entry,
    _Facts,
    _tokens,
)
from aitest.domain.planning.understanding import (
    BusinessFunction,
    BusinessFunctionOrigin,
    CapabilityGap,
    FunctionTrigger,
    GoalCandidate,
    ReviewState,
    TestIntent,
    TestIntentOrigin,
)


def _build_functions(
    entries: Sequence[_Entry],
    *,
    understanding_id: str,
    revision: int,
    source_snapshot_id: str,
) -> tuple[BusinessFunction, ...]:
    functions = [
        BusinessFunction(
            function_id=_function_id(entry.path, entry.qualname),
            name=entry.name,
            parent_function_id=None,
            kind=entry.kind,
            trigger=entry.trigger,
            business_object=entry.business_object,
            expected_outcome=entry.expected_outcome,
            conditions=entry.conditions,
            required_dimensions=entry.dimensions,
            source_refs=entry.source_refs,
            origin=BusinessFunctionOrigin.DETERMINISTIC,
            confidence=entry.confidence,
            review_state=ReviewState.UNREVIEWED,
            understanding_revision=revision,
            canonical_function_id=None,
            aliases=(),
            source_snapshot_id=source_snapshot_id,
            dimension_origins=tuple(
                (dimension, "rule", "") for dimension in entry.dimensions
            ),
        )
        for entry in entries
    ]
    return tuple(sorted(functions, key=lambda function: function.function_id))

def _build_intents(
    functions: Sequence[BusinessFunction], revision: int
) -> tuple[TestIntent, ...]:
    intents = [
        TestIntent(
            intent_id="intent-" + function.function_id.removeprefix("fn-"),
            statement=f"验证「{function.name}」的业务结果",
            risk=_risk_statement(function),
            function_ids=frozenset({function.function_id}),
            required_dimensions=function.required_dimensions,
            acceptance_condition=f"可观察：{function.expected_outcome}",
            rationale=(
                f"确定性扫描：{function.trigger.value} 入口 "
                f"{function.source_refs[0] if function.source_refs else 'source'}"
            ),
            origin=TestIntentOrigin.AI_SUGGESTED,
            confidence=function.confidence,
            understanding_revision=revision,
        )
        for function in functions
    ]
    return tuple(sorted(intents, key=lambda intent: intent.intent_id))

def _build_goals(
    intents: Sequence[TestIntent], functions: Sequence[BusinessFunction]
) -> tuple[GoalCandidate, ...]:
    by_id = {function.function_id: function for function in functions}
    scored: list[tuple[int, int, str, TestIntent, BusinessFunction]] = []
    for intent in intents:
        function = next(
            (by_id[function_id] for function_id in intent.function_ids if function_id in by_id),
            None,
        )
        if function is None:
            continue
        scored.append(
            (
                _goal_score(function),
                _trigger_priority(function.trigger),
                function.function_id,
                intent,
                function,
            )
        )
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return tuple(
        GoalCandidate(
            intent_id=intent.intent_id,
            statement=intent.statement,
            why=(
                f"入口 {function.trigger.value}；必要维度 "
                f"{', '.join(function.required_dimensions)}"
            ),
            function_ids=intent.function_ids,
            risk=intent.risk,
        )
        for _score, _priority, _identity, intent, function in scored[:GOAL_CANDIDATE_LIMIT]
    )

def _goal_score(function: BusinessFunction) -> int:
    dimensions = set(function.required_dimensions)
    score = 0
    if "permission" in dimensions:
        score += 30
    if "state_transition" in dimensions:
        score += 25
    if "persistence" in dimensions:
        score += 25
    if "dependency_failure" in dimensions:
        score += 15
    if "boundary" in dimensions:
        score += 5
    score += 10 if function.trigger is FunctionTrigger.HTTP else 0
    score += 5 if function.trigger is FunctionTrigger.CLI else 0
    tokens = set(_tokens(function.name))
    if tokens & _RISK_WORDS:
        score += 8
    return score

def _trigger_priority(trigger: FunctionTrigger) -> int:
    if trigger is FunctionTrigger.HTTP:
        return 3
    if trigger is FunctionTrigger.CLI:
        return 2
    return 1

def _risk_statement(function: BusinessFunction) -> str:
    dimensions = set(function.required_dimensions)
    if "permission" in dimensions:
        return "越权或未授权访问"
    if "state_transition" in dimensions and "persistence" in dimensions:
        return "数据写入错误或状态不一致"
    if "dependency_failure" in dimensions:
        return "外部依赖失败导致业务不可用"
    if "boundary" in dimensions:
        return "边界输入处理错误"
    return "核心业务结果错误"

def _derive_dimensions(
    *,
    name: str,
    path: str,
    trigger: FunctionTrigger,
    methods: Sequence[str],
    facts: _Facts,
) -> tuple[str, ...]:
    tokens: set[str] = set(_tokens(f"{name} {path}"))
    for call in facts.call_names:
        tokens.update(_tokens(call.replace(".", " ")))
    for decorator in facts.decorator_names:
        tokens.update(_tokens(decorator.replace(".", " ")))
    for parameter in facts.parameters:
        tokens.update(_tokens(parameter))
    dimensions = {"normal", "error"}
    path_parameter = trigger is FunctionTrigger.HTTP and ("{" in path or "<" in path)
    if facts.parameters or path_parameter:
        dimensions.add("boundary")
    method_tokens = {method.upper() for method in methods}
    write = bool(method_tokens & {"POST", "PUT", "PATCH", "DELETE"}) or bool(
        tokens & _WRITE_TOKENS
    )
    if write:
        dimensions.update({"state_transition", "persistence"})
    elif tokens & _STATE_TOKENS:
        dimensions.add("state_transition")
    if tokens & _PERMISSION_TOKENS:
        dimensions.add("permission")
    if tokens & _EXTERNAL_TOKENS:
        dimensions.add("dependency_failure")
    return tuple(dimension for dimension in _DIMENSION_ORDER if dimension in dimensions)

def _http_name(methods: Sequence[str], path: str, qualname: str) -> str:
    label = "/".join(methods) if methods else "HTTP"
    if path:
        return f"{label} {path}"
    return f"{label} {qualname}"

def _http_outcome(methods: Sequence[str], business_object: str) -> str:
    upper = {method.upper() for method in methods}
    if "DELETE" in upper:
        return f"删除{business_object}成功"
    if "POST" in upper:
        return f"创建或完成{business_object}操作并返回结果"
    if upper & {"PUT", "PATCH"}:
        return f"更新{business_object}成功"
    if upper & {"GET", "HEAD", "OPTIONS"}:
        return f"返回{business_object}信息"
    return f"{business_object}操作成功"

def _object_from_route(path: str) -> str:
    for segment in path.strip("/").split("/"):
        lowered = segment.lower()
        if not segment or segment.startswith(("{", "<", ":")):
            continue
        if lowered.isdigit() or (lowered.startswith("v") and lowered[1:].isdigit()):
            continue
        if lowered in {"api", "rest", "internal", "public"}:
            continue
        value = re.sub(r"[^\w ]", " ", segment.replace("-", " ").replace("_", " "))
        value = " ".join(value.split())
        if value:
            return value
    return ""

def _object_from_name(name: str) -> str:
    tokens = [token for token in _tokens(name) if token.lower() not in _OBJECT_SKIP_TOKENS]
    if not tokens:
        original = _tokens(name)
        return original[-1] if original else name
    return " ".join(tokens)

def _merge_entries(entries: Iterable[_Entry]) -> list[_Entry]:
    merged: dict[tuple[str, str], _Entry] = {}
    for entry in entries:
        key = (entry.path, entry.qualname)
        current = merged.get(key)
        if current is None:
            merged[key] = entry
            continue
        methods = tuple(sorted(set(current.methods) | set(entry.methods)))
        conditions = tuple(sorted(set(current.conditions) | set(entry.conditions)))
        dimensions = tuple(
            dimension
            for dimension in _DIMENSION_ORDER
            if dimension in set(current.dimensions) | set(entry.dimensions)
        )
        source_refs = tuple(sorted(set(current.source_refs) | set(entry.source_refs)))
        name = (
            _http_name(methods, current.path, current.qualname)
            if current.trigger is FunctionTrigger.HTTP
            else current.name
        )
        merged[key] = replace(
            current,
            methods=methods,
            conditions=conditions,
            dimensions=dimensions,
            source_refs=source_refs,
            name=name,
            expected_outcome=(
                _http_outcome(methods, current.business_object)
                if current.trigger is FunctionTrigger.HTTP
                else current.expected_outcome
            ),
            confidence=max(current.confidence, entry.confidence),
            explicit=current.explicit or entry.explicit,
        )
    return list(merged.values())

def _function_id(path: str, qualname: str) -> str:
    raw = f"{path}\x1f{qualname}".encode()
    return "fn-" + hashlib.sha256(raw).hexdigest()[:16]

def _framework_gap(relative: str, framework: str) -> CapabilityGap:
    return _gap(
        scope=relative,
        missing_capability=f"scan:{framework}",
        reason=f"当前确定性扫描器不识别 {framework} 的入口声明",
        source_refs=(relative,),
    )

def _non_python_gap(relative: str, suffix: str) -> CapabilityGap:
    return _gap(
        scope=relative,
        missing_capability="scan:non_python",
        reason=f"当前扫描器只解析 Python AST，无法识别 {suffix} 文件中的业务入口",
        source_refs=(relative,),
    )

def _gap(
    *, scope: str, missing_capability: str, reason: str, source_refs: tuple[str, ...]
) -> CapabilityGap:
    raw = f"{scope}\x1f{missing_capability}\x1f{SCANNER_REVISION}".encode()
    return CapabilityGap(
        gap_id="gap-" + hashlib.sha256(raw).hexdigest()[:16],
        scope=scope,
        missing_capability=missing_capability,
        capability_revision=SCANNER_REVISION,
        reason=reason,
        workaround="人工补充业务功能清单",
        blocks_full=True,
        source_refs=source_refs,
    )

def _function_facts(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Facts:
    arguments = node.args
    parameters = tuple(
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *([arguments.vararg] if arguments.vararg is not None else []),
            *([arguments.kwarg] if arguments.kwarg is not None else []),
        )
    )
    calls = _call_names(node.body)
    calls += _call_names([*arguments.defaults, *arguments.kw_defaults])
    calls += tuple(
        _call_name(dec.func)
        for dec in node.decorator_list
        if isinstance(dec, ast.Call)
    )
    return _Facts(
        parameters=parameters,
        call_names=tuple(dict.fromkeys(call for call in calls if call)),
        decorator_names=tuple(_dotted(dec) for dec in node.decorator_list),
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )

def _class_facts(node: ast.ClassDef) -> _Facts:
    parameters: list[str] = []
    calls: list[str] = []
    decorators: list[str] = []
    is_async = False
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts = _function_facts(child)
            parameters.extend(facts.parameters)
            calls.extend(facts.call_names)
            decorators.extend(facts.decorator_names)
            is_async = is_async or facts.is_async
    return _Facts(
        parameters=tuple(dict.fromkeys(parameters)),
        call_names=tuple(dict.fromkeys(calls)),
        decorator_names=tuple(dict.fromkeys(decorators)),
        is_async=is_async,
    )
