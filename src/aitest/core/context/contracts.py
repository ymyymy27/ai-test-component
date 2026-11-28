"""后端接口路由（AST/多框架/OpenAPI）与前端调用/静态资源通用解析模块。"""

from __future__ import annotations

import contextlib
import json
import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aitest.core.context.py_routes import extract_python_api_routes, join_route_parts
from aitest.core.repo_index import DEFAULT_SKIP_DIRS
from aitest.security import is_ignored_path, load_ignore_patterns

_FE_IGNORE_DIRS = DEFAULT_SKIP_DIRS | {"tests", "test"}
_FE_CALL_SKIP = frozenset(
    {"if", "for", "while", "switch", "catch", "return", "function", "class", "new", "typeof"}
)
_FE_NON_HTTP_METHODS = frozenset(
    {
        "startswith",
        "endswith",
        "includes",
        "indexof",
        "lastindexof",
        "match",
        "matchall",
        "replace",
        "replaceall",
        "split",
        "slice",
        "substring",
        "substr",
        "test",
        "exec",
        "search",
        "join",
        "resolve",
        "normalize",
        "push",
        "pop",
        "shift",
        "unshift",
        "concat",
        "filter",
        "find",
        "some",
        "every",
        "map",
        "foreach",
        "redirect",
        "rewrite",
        "navigate",
        "pushstate",
        "replacestate",
        "setattribute",
        "getattribute",
        "addeventlistener",
        "log",
        "info",
        "warn",
        "error",
        "debug",
        "assert",
    }
)
_FE_NON_HTTP_RECEIVERS = frozenset(
    {
        "headers",
        "searchparams",
        "params",
        "cookies",
        "map",
        "set",
        "localstorage",
        "sessionstorage",
        "console",
        "router",
        "route",
        "store",
        "cache",
        "pathname",
        "url",
    }
)
_FE_EXPLICIT_HTTP_CLIENTS = frozenset(
    {"fetch", "$fetch", "usefetch", "axios", "ky", "http", "request", "requestclient", "apiclient"}
)
_FETCH_LIKE_CLIENTS = frozenset({"fetch", "$fetch", "usefetch", "ky"})
# WHATWG Fetch RequestInit 保留字段：绝不把 HTTP 控制选项当成业务 JSON 请求体字段
_REQUEST_INIT_KEYS = frozenset(
    {
        "method",
        "headers",
        "body",
        "mode",
        "credentials",
        "cache",
        "redirect",
        "referrer",
        "referrerPolicy",
        "referrerpolicy",
        "integrity",
        "keepalive",
        "signal",
        "priority",
        "next",
        "duplex",
        "window",
        "timeout",
        "params",
        "query",
    }
)

_HTTP_VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _normalize_compare_path(raw_path: str) -> str:
    text = raw_path.strip().split("?", 1)[0].split("#", 1)[0]
    if not text.startswith("/"):
        text = "/" + text
    text = re.sub(r"(?:\$\{[^}]+\}|\{[^}]+\}|<[^>]+>)", "{param}", text)
    text = re.sub(r"(?<=/):[A-Za-z_][A-Za-z0-9_]*", "{param}", text)
    if len(text) > 1:
        text = text.rstrip("/")
    return text


def _extract_server_base_path(spec: dict[str, Any]) -> str:
    """提取 OAS 2.0 `basePath` 或 OAS 3.x `servers[0].url` 中的基础路径前缀。"""
    raw_base = spec.get("basePath")
    if isinstance(raw_base, str) and raw_base.strip():
        cleaned = raw_base.strip()
        if not cleaned.startswith("/"):
            cleaned = "/" + cleaned
        return "" if cleaned == "/" else cleaned.rstrip("/")
    servers = spec.get("servers")
    if isinstance(servers, list):
        for srv in servers:
            if not isinstance(srv, dict):
                continue
            raw_url = str(srv.get("url") or "").strip()
            if not raw_url:
                continue
            path_part = urlsplit(raw_url).path if "://" in raw_url else raw_url.split("?", 1)[0]
            if not path_part or path_part == "/":
                continue
            if not path_part.startswith("/"):
                path_part = "/" + path_part
            return path_part.rstrip("/")
    return ""


def _resolve_ref_dict(spec: dict[str, Any], node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cur: Any = spec
        for part in ref[2:].split("/"):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
                break
        if isinstance(cur, dict):
            return cur
    return node


def _resolve_schema_details(
    spec: dict[str, Any],
    node: Any,
    visiting: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """递归解析 OpenAPI Schema，提取属性名、必填项、字段 format 与是否开放。"""
    resolved = _resolve_ref_dict(spec, node) if isinstance(node, dict) else None
    if not isinstance(resolved, dict):
        return {"fields": set(), "required": set(), "formats": {}, "is_open": False}

    fields: set[str] = set()
    required: set[str] = set()
    formats: dict[str, str] = {}
    is_open = bool(resolved.get("additionalProperties") is True)

    ref = node.get("$ref") if isinstance(node, dict) else None
    next_visiting = visiting | ({ref} if isinstance(ref, str) else set())
    if isinstance(ref, str) and ref in visiting:
        return {"fields": fields, "required": required, "formats": formats, "is_open": is_open}

    for combo_key in ("allOf", "oneOf", "anyOf"):
        combo = resolved.get(combo_key)
        if isinstance(combo, list):
            for item in combo:
                sub = _resolve_schema_details(spec, item, next_visiting)
                fields.update(sub["fields"])
                required.update(sub["required"])
                formats.update(sub["formats"])
                is_open = is_open or sub["is_open"]

    raw_req = resolved.get("required")
    if isinstance(raw_req, list):
        for rname in raw_req:
            if isinstance(rname, str) and rname.strip():
                required.add(rname.strip())

    props = resolved.get("properties")
    if isinstance(props, dict):
        for prop_name, prop_schema in props.items():
            if not isinstance(prop_name, str) or not prop_name.strip():
                continue
            clean_name = prop_name.strip()
            fields.add(clean_name)
            p_resolved = _resolve_ref_dict(spec, prop_schema)
            if isinstance(p_resolved, dict):
                fmt = p_resolved.get("format") or p_resolved.get("type")
                if isinstance(fmt, str) and fmt.strip():
                    formats[clean_name] = fmt.strip()

    return {"fields": fields, "required": required, "formats": formats, "is_open": is_open}


def _resolve_json_ref(
    spec: dict[str, Any],
    node: Any,
    visiting: frozenset[str] = frozenset(),
) -> tuple[set[str], bool]:
    """递归展开 OpenAPI Schema（支持 $ref、allOf/oneOf/anyOf），返回属性名集合与是否封闭。"""
    details = _resolve_schema_details(spec, node, visiting)
    return details["fields"], bool(details["is_open"])


def _parse_yaml_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text or text in {"~", "null", "Null", "NULL"}:
        return None
    if text in {"true", "True", "TRUE"}:
        return True
    if text in {"false", "False", "FALSE"}:
        return False
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(part) for part in inner.split(",")]
    return text


def _load_spec_data(path: Path, max_bytes: int) -> dict[str, Any] | None:
    """读取 JSON 或 YAML OpenAPI 文档（无 PyYAML 时回退轻量缩进 YAML 解析）。"""
    try:
        if path.stat().st_size > max_bytes:
            return None
        raw_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if path.suffix.lower() == ".json":
        try:
            loaded = json.loads(raw_text)
            return loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            return None
    with contextlib.suppress(Exception):
        import importlib

        yaml_mod = importlib.import_module("yaml")
        loaded = yaml_mod.safe_load(raw_text)
        return loaded if isinstance(loaded, dict) else None

    # 轻量标准库缩进 YAML 剖析（覆盖常见 OpenAPI paths/servers/components）
    tokens: list[tuple[int, str]] = []
    for line in raw_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        clean = re.sub(r"\s+#.*$", "", line)
        indent = len(clean) - len(clean.lstrip(" "))
        tokens.append((indent, clean.strip()))
    if not tokens:
        return None

    def _parse_block(idx: int, min_indent: int) -> tuple[Any, int]:
        if idx >= len(tokens):
            return {}, idx
        if tokens[idx][1].startswith("- "):
            arr: list[Any] = []
            list_indent = tokens[idx][0]
            while (
                idx < len(tokens)
                and tokens[idx][0] == list_indent
                and tokens[idx][1].startswith("- ")
            ):
                item_text = tokens[idx][1][2:].strip()
                idx += 1
                if ":" in item_text and not item_text.startswith(("'", '"', "{", "[")):
                    k, _sep, v = item_text.partition(":")
                    k_name = k.strip().strip('"').strip("'")
                    item_dict: dict[str, Any] = {
                        k_name: _parse_yaml_scalar(v) if v.strip() else None
                    }
                    if idx < len(tokens) and tokens[idx][0] > list_indent:
                        sub, idx = _parse_block(idx, list_indent + 1)
                        if isinstance(sub, dict):
                            if item_dict[next(iter(item_dict))] is None:
                                item_dict[next(iter(item_dict))] = sub
                            else:
                                item_dict.update(sub)
                    arr.append(item_dict)
                elif item_text:
                    arr.append(_parse_yaml_scalar(item_text))
                elif idx < len(tokens) and tokens[idx][0] > list_indent:
                    sub, idx = _parse_block(idx, list_indent + 1)
                    arr.append(sub)
            return arr, idx

        mapping: dict[str, Any] = {}
        map_indent = tokens[idx][0]
        while idx < len(tokens) and tokens[idx][0] >= min_indent:
            cur_indent, text = tokens[idx]
            if cur_indent != map_indent or text.startswith("- "):
                break
            if ":" not in text:
                idx += 1
                continue
            key_raw, _sep, val_raw = text.partition(":")
            key = key_raw.strip().strip('"').strip("'")
            idx += 1
            if val_raw.strip():
                mapping[key] = _parse_yaml_scalar(val_raw)
            elif idx < len(tokens) and tokens[idx][0] > map_indent:
                child, idx = _parse_block(idx, map_indent + 1)
                mapping[key] = child
            else:
                mapping[key] = {}
        return mapping, idx

    parsed, _ = _parse_block(0, 0)
    return parsed if isinstance(parsed, dict) else None


def parse_openapi_spec(
    data: dict[str, Any],
    *,
    source: str,
    confidence: str = "openapi_file",
) -> list[dict[str, Any]]:
    """解析 OAS 2.0/3.x 文档（servers/basePath/security/参数/表单/包装响应）。"""
    paths = data.get("paths")
    if not isinstance(paths, dict):
        return []
    base_prefix = _extract_server_base_path(data)
    global_security = data.get("security")
    http_methods = {"get", "post", "put", "patch", "delete"}
    routes: list[dict[str, Any]] = []

    for raw_route_path, ops in sorted(paths.items()):
        if not isinstance(raw_route_path, str) or not isinstance(ops, dict):
            continue
        full_path = (
            join_route_parts(base_prefix, raw_route_path)
            if base_prefix and not raw_route_path.startswith(base_prefix)
            else (raw_route_path if raw_route_path.startswith("/") else f"/{raw_route_path}")
        )
        raw_pp = ops.get("parameters")
        path_params_raw: list[Any] = raw_pp if isinstance(raw_pp, list) else []

        for method, detail in sorted(ops.items()):
            if not isinstance(method, str) or method.lower() not in http_methods:
                continue
            if not isinstance(detail, dict):
                continue

            sec = detail.get("security") if "security" in detail else global_security
            auth = "user" if isinstance(sec, list) and len(sec) > 0 else "public"

            fields: set[str] = set()
            required_fields: set[str] = set()
            formats: dict[str, str] = {}
            content_types: list[str] = []
            query_params: list[str] = []
            path_params: list[str] = []
            header_params: list[str] = []
            is_open = False

            # 1. 参数清单（OAS 2.0 & 3.x：query/path/header/formData/body）
            raw_op = detail.get("parameters")
            op_params: list[Any] = raw_op if isinstance(raw_op, list) else []
            for p_raw in [*path_params_raw, *op_params]:
                param = _resolve_ref_dict(data, p_raw)
                if not isinstance(param, dict):
                    continue
                p_in = str(param.get("in") or "").lower()
                p_name = str(param.get("name") or "").strip()
                p_req = bool(param.get("required"))
                p_schema = param.get("schema") if isinstance(param.get("schema"), dict) else param
                if p_in == "body":
                    s_info = _resolve_schema_details(data, p_schema)
                    fields.update(s_info["fields"])
                    required_fields.update(s_info["required"])
                    formats.update(s_info["formats"])
                    is_open = is_open or bool(s_info["is_open"])
                    if "application/json" not in content_types:
                        content_types.append("application/json")
                elif p_in == "formdata" and p_name:
                    fields.add(p_name)
                    if p_req:
                        required_fields.add(p_name)
                    if "application/x-www-form-urlencoded" not in content_types:
                        content_types.append("application/x-www-form-urlencoded")
                elif p_in == "query" and p_name:
                    query_params.append(p_name)
                elif p_in == "path" and p_name:
                    path_params.append(p_name)
                elif p_in == "header" and p_name:
                    header_params.append(p_name)

            # 2. OAS 3.x requestBody（JSON / form-urlencoded / multipart）
            req_body = _resolve_ref_dict(data, detail.get("requestBody"))
            if isinstance(req_body, dict):
                content = req_body.get("content")
                if isinstance(content, dict):
                    for ctype in (
                        "application/json",
                        "application/x-www-form-urlencoded",
                        "multipart/form-data",
                    ):
                        media = content.get(ctype)
                        if not isinstance(media, dict):
                            continue
                        if ctype not in content_types:
                            content_types.append(ctype)
                        s_info = _resolve_schema_details(data, media.get("schema"))
                        fields.update(s_info["fields"])
                        required_fields.update(s_info["required"])
                        formats.update(s_info["formats"])
                        is_open = is_open or bool(s_info["is_open"])

            entry: dict[str, Any] = {
                "method": method.upper(),
                "path": full_path,
                "auth": auth,
                "file": source,
                "line": "1",
                "source_kind": "openapi",
                "confidence": confidence,
                "fields_complete": bool(fields) and not is_open,
            }
            if fields:
                entry["fields"] = ",".join(sorted(fields)[:128])
            if required_fields:
                entry["required_fields"] = sorted(required_fields)[:64]
            if formats:
                entry["field_formats"] = dict(sorted(formats.items())[:64])
            if content_types:
                entry["content_types"] = content_types
            if query_params:
                entry["query_params"] = sorted(dict.fromkeys(query_params))
            if path_params:
                entry["path_params"] = sorted(dict.fromkeys(path_params))
            if header_params:
                entry["header_params"] = sorted(dict.fromkeys(header_params))

            # 3. 成功响应字段及 {code, data} 包装识别
            responses = detail.get("responses")
            if isinstance(responses, dict):
                codes = sorted(
                    code for code in responses if isinstance(code, str) and code.startswith("2")
                )
                if codes:
                    entry["status_code"] = codes[0]
                    resp_obj = _resolve_ref_dict(data, responses.get(codes[0]))
                    resp_schema = None
                    if isinstance(resp_obj, dict):
                        resp_content = resp_obj.get("content")
                        if isinstance(resp_content, dict) and isinstance(
                            resp_content.get("application/json"), dict
                        ):
                            resp_schema = resp_content["application/json"].get("schema")
                        elif isinstance(resp_obj.get("schema"), dict):
                            resp_schema = resp_obj.get("schema")
                    if resp_schema is not None:
                        r_info = _resolve_schema_details(data, resp_schema)
                        resp_fields = set(r_info["fields"])
                        resp_resolved = _resolve_ref_dict(data, resp_schema)
                        if isinstance(resp_resolved, dict) and isinstance(
                            resp_resolved.get("properties"), dict
                        ):
                            data_prop = resp_resolved["properties"].get("data")
                            if data_prop is not None:
                                inner = _resolve_schema_details(data, data_prop)
                                for sub_f in inner["fields"]:
                                    resp_fields.add(f"data.{sub_f}")
                        if resp_fields:
                            entry["response_fields"] = sorted(resp_fields)[:64]

            routes.append(entry)
    return routes


def _openapi_routes(root: Path, *, max_bytes: int = 2 * 1024 * 1024) -> list[dict[str, Any]]:
    """静态解析项目内的 openapi/swagger.{json,yaml,yml}，支持 OAS 2.0/3.x。"""
    spec_names = (
        "openapi.json",
        "swagger.json",
        "openapi.yaml",
        "openapi.yml",
        "swagger.yaml",
        "swagger.yml",
    )
    candidates: list[Path] = []
    for name in spec_names:
        if (root / name).is_file():
            candidates.append(root / name)
    with suppress(OSError):
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            for name in spec_names:
                target = child / name
                if target.is_file():
                    candidates.append(target)

    routes: list[dict[str, Any]] = []
    for spec_path in candidates:
        data = _load_spec_data(spec_path, max_bytes)
        if not isinstance(data, dict):
            continue
        rel_spec = spec_path.relative_to(root).as_posix()
        routes.extend(parse_openapi_spec(data, source=rel_spec, confidence="openapi_file"))
    return routes


def _http_handler_routes(files_text: dict[Path, str]) -> list[dict[str, Any]]:
    """提取继承 BaseHTTPRequestHandler / SimpleHTTPRequestHandler 的路由。"""
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    handler_re = re.compile(r"def\s+do_(GET|POST|PUT|PATCH|DELETE)\s*\(")
    eq_re = re.compile(r"==\s*[\"'](/[^\"']+)[\"']")
    sw_re = re.compile(r"\.startswith\(\s*[\"'](/[^\"']+)[\"']\s*\)")
    rx_re = re.compile(r"re\.(?:fullmatch|match)\(\s*r?[\"']\^?(/[^\"']+)\$?[\"']")

    for text in files_text.values():
        if (
            "BaseHTTPRequestHandler" not in text
            and "SimpleHTTPRequestHandler" not in text
        ):
            continue
        matches = list(handler_re.finditer(text))
        for idx, match in enumerate(matches):
            method = match.group(1).upper()
            end_pos = (
                matches[idx + 1].start()
                if idx + 1 < len(matches)
                else len(text)
            )
            body = text[match.start() : min(end_pos, match.start() + 4000)]
            found_paths: list[str] = []
            for m in eq_re.finditer(body):
                found_paths.append(m.group(1))
            for m in sw_re.finditer(body):
                raw = m.group(1)
                norm = raw.rstrip("/") + "/{id}" if raw != "/" and raw.endswith("/") else raw
                found_paths.append(norm)
            for m in rx_re.finditer(body):
                raw = m.group(1).rstrip("$")
                norm = re.sub(r"\([^)]+\)|\[\^/\]\+", "{id}", raw)
                found_paths.append(norm)
            for route_path in found_paths:
                key = (method, route_path)
                if key in seen:
                    continue
                seen.add(key)
                routes.append(
                    {
                        "method": method,
                        "path": route_path,
                        "auth": "public",
                        "fields_complete": False,
                        "source_kind": "http_handler",
                    }
                )
                if len(routes) >= 240:
                    return routes
    return routes


def _balanced_group(text: str, open_idx: int, open_ch: str, close_ch: str, limit: int = 900) -> str:
    """截取从 open_idx 开始的一对平衡括号内部文本（忽略引号与注释内符号）。"""
    depth = 0
    quote = ""
    escaped = False
    end = min(len(text), open_idx + limit)
    for idx in range(open_idx, end):
        ch = text[idx]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : idx]
    return ""


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """按顶层分隔符切分表达式参数，忽略括号与引号内分隔符。"""
    parts: list[str] = []
    depth = 0
    quote = ""
    escaped = False
    start = 0
    for idx, ch in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth > 0:
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_object_keys(obj_body: str) -> list[str]:
    """解析 JS/TS 对象字面量顶层键名（支持 key: val 与 ES6 简写）。"""
    keys: list[str] = []
    for item in _split_top_level(obj_body, ","):
        cleaned = re.sub(r"//[^\n]*|/\*.*?\*/", "", item, flags=re.DOTALL).strip()
        if not cleaned or cleaned.startswith("..."):
            continue
        match = re.match(r"^[\"']?([A-Za-z_][\w$]*)[\"']?\s*(?::|$)", cleaned)
        if match:
            name = match.group(1)
            if name not in keys:
                keys.append(name)
    return keys[:32]


def _collect_ts_zod_schemas(files_text: dict[Path, str]) -> dict[str, tuple[set[str], bool]]:
    """收集跨 TS/JS 文件声明的封闭 z.object({...}) Schema 及其字段集合。"""
    zod_re = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*z\.object\(\s*\{")
    schemas: dict[str, tuple[set[str], bool]] = {}
    for text in files_text.values():
        for match in zod_re.finditer(text):
            name = match.group(1)
            obj_body = _balanced_group(text, match.end() - 1, "{", "}", limit=4000)
            if not obj_body:
                continue
            fields = set(_extract_object_keys(obj_body))
            tail = text[match.end() + len(obj_body) : match.end() + len(obj_body) + 80]
            is_open = ".passthrough(" in tail or ".catchall(" in tail
            if fields:
                schemas[name] = (fields, is_open)
    return schemas


def _ts_handler_body_fields(
    handler_text: str,
    schemas: dict[str, tuple[set[str], bool]],
) -> tuple[list[str], bool]:
    """检查 TS/JS 路由处理体是否显式绑定封闭 Zod Schema（同时兼容多态早退分支属性）。"""
    matched_fields: set[str] = set()
    used_closed_schema = False
    for m in re.finditer(
        r"(?:\bvalidateBody\([^,]+,\s*([A-Za-z_]\w*)|\b([A-Za-z_]\w*)\.(?:safeParse|parse)\()",
        handler_text,
    ):
        sname = m.group(1) or m.group(2)
        if sname and sname in schemas:
            sfields, is_open = schemas[sname]
            if is_open:
                return [], False
            matched_fields.update(sfields)
            used_closed_schema = True
    for m in re.finditer(r"z\.object\(\s*\{", handler_text):
        obj_body = _balanced_group(handler_text, m.end() - 1, "{", "}", limit=2500)
        if obj_body:
            tail = handler_text[m.end() + len(obj_body) : m.end() + len(obj_body) + 60]
            if ".passthrough(" in tail or ".catchall(" in tail:
                return [], False
            matched_fields.update(_extract_object_keys(obj_body))
            used_closed_schema = True
    if not used_closed_schema or not matched_fields:
        return [], False

    # 若函数体先以 let raw = await req.json() 读入并在分支中访问 raw.<prop>，一并纳入允许字段
    skip_js_attrs = {
        "then",
        "catch",
        "finally",
        "toString",
        "valueOf",
        "hasOwnProperty",
        "length",
        "map",
        "filter",
        "forEach",
        "keys",
        "values",
        "entries",
        "trim",
    }
    json_vars = set(
        re.findall(r"([A-Za-z_$][\w$]*)\s*=\s*await\s+[A-Za-z_$][\w$.]*\.json\(", handler_text)
    )
    json_vars.update({"req.body", "request.body"})
    for vname in json_vars:
        for attr_m in re.finditer(rf"\b{re.escape(vname)}\.([A-Za-z_]\w*)", handler_text):
            prop = attr_m.group(1)
            if prop not in skip_js_attrs:
                matched_fields.add(prop)
        if "." not in vname:
            for dest_m in re.finditer(
                rf"(?:const|let|var)\s*\{{([^}}]+)\}}\s*=\s*{re.escape(vname)}\b",
                handler_text,
            ):
                for raw_item in dest_m.group(1).split(","):
                    cleaned = raw_item.strip().split(":", 1)[0].split("=", 1)[0].strip()
                    if re.fullmatch(r"[A-Za-z_]\w*", cleaned):
                        matched_fields.add(cleaned)

    return sorted(matched_fields), True


def _nextjs_app_routes(
    root: Path,
    *,
    max_routes: int,
    schemas: dict[str, tuple[set[str], bool]] | None = None,
) -> list[dict[str, Any]]:
    """提取 Next.js App Router (app/**/api/**/route.{ts,tsx,js,jsx,mjs}) 路由。"""
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    handler_re = re.compile(
        r"\bexport\s+(?:async\s+)?(?:function|const|let)\s+(GET|POST|PUT|PATCH|DELETE)\b"
    )
    ignore_patterns = load_ignore_patterns(root)
    ts_files: dict[Path, str] = {}
    route_paths: list[Path] = []
    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _FE_IGNORE_DIRS
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            if name.endswith((".ts", ".tsx", ".js", ".mjs")) and not name.endswith(".d.ts"):
                with suppress(OSError):
                    if path.stat().st_size <= 256 * 1024:
                        ts_files[path] = path.read_text(encoding="utf-8", errors="replace")
            if re.fullmatch(r"route\.(?:ts|tsx|js|jsx|mjs)", name):
                route_paths.append(path)

    all_schemas = schemas if schemas is not None else _collect_ts_zod_schemas(ts_files)
    for path in route_paths:
        rel_posix = path.relative_to(root).as_posix()
        parts = list(path.relative_to(root).parts[:-1])
        if "app" not in parts or "api" not in parts:
            continue
        app_idx = len(parts) - 1 - parts[::-1].index("app")
        route_segs: list[str] = []
        for seg in parts[app_idx + 1 :]:
            if seg.startswith("(") and seg.endswith(")"):
                continue
            norm_seg = re.sub(r"\[\[?\.\.\.([^\]/]+)\]?\]", r"{\1}", seg)
            norm_seg = re.sub(r"\[([^\]/]+)\]", r"{\1}", norm_seg)
            route_segs.append(norm_seg)
        if "api" not in route_segs:
            continue
        route_path = "/" + "/".join(route_segs)
        text = ts_files.get(path)
        if text is None:
            continue
        lowered = text.lower()
        auth = "public"
        if "superuser" in lowered or "requireadmin" in lowered or "isadmin" in lowered:
            auth = "superuser"
        elif any(
            tok in lowered
            for tok in ("requireauth", "getsession", "verifyauth", "authorize", "requirerole")
        ):
            auth = "user"
        matches = list(handler_re.finditer(text))
        for idx, match in enumerate(matches):
            method = match.group(1).upper()
            end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            fn_body = text[match.start() : end_pos]
            key = (method, route_path)
            if key in seen:
                continue
            seen.add(key)
            line = text.count("\n", 0, match.start()) + 1
            fields, complete = _ts_handler_body_fields(fn_body, all_schemas)
            entry: dict[str, Any] = {
                "method": method,
                "path": route_path,
                "auth": auth,
                "file": rel_posix,
                "line": str(line),
                "fields_complete": complete,
                "confidence": "heuristic",
                "source_kind": "nextjs_app",
            }
            if fields:
                entry["fields"] = ",".join(fields)
            routes.append(entry)
            if len(routes) >= max_routes:
                return routes
    return routes


def _nextjs_pages_routes(root: Path, *, max_routes: int) -> list[dict[str, Any]]:
    """提取 Next.js Pages Router (pages/api/**/*.{ts,tsx,js,jsx,mjs}) 路由。"""
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    method_check_re = re.compile(
        r"\breq(?:uest)?\.method\s*===?\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']",
        re.I,
    )
    ignore_patterns = load_ignore_patterns(root)

    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _FE_IGNORE_DIRS
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            if not name.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")) or name.endswith(".d.ts"):
                continue
            if name.startswith("_"):
                continue
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            parts = list(path.relative_to(root).with_suffix("").parts)
            if "pages" not in parts or "api" not in parts:
                continue
            pages_idx = len(parts) - 1 - parts[::-1].index("pages")
            segs = parts[pages_idx + 1 :]
            if not segs or segs[0] != "api":
                continue
            if segs[-1] == "index" and len(segs) > 1:
                segs = segs[:-1]
            norm_segs: list[str] = []
            for seg in segs:
                s = re.sub(r"\[\[?\.\.\.([^\]/]+)\]?\]", r"{\1}", seg)
                s = re.sub(r"\[([^\]/]+)\]", r"{\1}", s)
                norm_segs.append(s)
            route_path = "/" + "/".join(norm_segs)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel_posix = path.relative_to(root).as_posix()
            explicit_methods = {m.group(1).upper() for m in method_check_re.finditer(text)}
            methods = sorted(explicit_methods) if explicit_methods else ["ANY"]
            for method in methods:
                key = (method, route_path)
                if key in seen:
                    continue
                seen.add(key)
                routes.append(
                    {
                        "method": method,
                        "path": route_path,
                        "auth": "public",
                        "file": rel_posix,
                        "line": "1",
                        "fields_complete": False,
                        "source_kind": "nextjs_pages",
                    }
                )
                if len(routes) >= max_routes:
                    return routes
    return routes


def _resolve_ts_import(
    src_file: Path,
    spec: str,
    root: Path,
    files_text: dict[Path, str],
) -> Path | None:
    if not spec.startswith("."):
        return None
    base = (src_file.parent / spec).resolve()
    candidates = [base]
    for ext in (".ts", ".js", ".mjs", ".cjs"):
        candidates.append(base.with_suffix(ext))
        candidates.append(base / f"index{ext}")
    for cand in candidates:
        if cand in files_text and cand.is_relative_to(root.resolve()):
            return cand
    return None


def _js_backend_routes(
    root: Path,
    *,
    max_routes: int,
    schemas: dict[str, tuple[set[str], bool]] | None = None,
) -> list[dict[str, Any]]:
    """提取 Node.js (Express / Fastify / Koa / NestJS) 后端路由及跨文件 use 前缀。"""
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    route_re = re.compile(
        r"\b(?:app|router|fastify|server)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']*)[\"']",
        re.IGNORECASE,
    )
    use_re = re.compile(
        r"\b(?:app|router)\.use\(\s*[\"'](/[^\"']*)[\"']\s*,\s*([A-Za-z_]\w*)",
        re.IGNORECASE,
    )
    ts_import_re = re.compile(
        r"import\s+(?:(\w+)|\{\s*([^}]+)\s*\})\s+from\s+[\"']([^\"']+)[\"']"
    )
    cjs_req_re = re.compile(
        r"(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*[\"']([^\"']+)[\"']\s*\)"
    )
    nest_ctrl_re = re.compile(r"@Controller\(\s*(?:[\"']([^\"']*)[\"'])?\s*\)")
    nest_method_re = re.compile(
        r"@(Get|Post|Put|Patch|Delete)\(\s*(?:[\"']([^\"']*)[\"'])?\s*\)",
        re.IGNORECASE,
    )
    backend_markers = {"server", "backend", "api", "routes", "routers", "controllers"}
    skip_fe = {"frontend", "front", "web", "client", "ui", "components", "views", "pages"}
    ignore_patterns = load_ignore_patterns(root)
    files_text: dict[Path, str] = {}

    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _FE_IGNORE_DIRS
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            if not name.endswith((".js", ".ts", ".mjs", ".cjs")):
                continue
            if name.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts", ".d.ts")):
                continue
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            rel_parts = {p.lower() for p in path.relative_to(root).parts[:-1]}
            if (rel_parts & skip_fe) and not (rel_parts & backend_markers):
                continue
            try:
                if path.stat().st_size > 256 * 1024:
                    continue
                files_text[path] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

    all_schemas = schemas if schemas is not None else _collect_ts_zod_schemas(files_text)
    mounted_by_file: dict[Path, list[str]] = {}
    mounted_by_stem: dict[str, str] = {}

    for path, text in files_text.items():
        var_to_file: dict[str, Path] = {}
        for m in ts_import_re.finditer(text):
            tgt = _resolve_ts_import(path, m.group(3), root, files_text)
            if tgt is None:
                continue
            if m.group(1):
                var_to_file[m.group(1)] = tgt
            elif m.group(2):
                for item in m.group(2).split(","):
                    alias = item.strip().split(" as ")[-1].strip()
                    if alias:
                        var_to_file[alias] = tgt
        for m in cjs_req_re.finditer(text):
            tgt = _resolve_ts_import(path, m.group(2), root, files_text)
            if tgt is not None:
                var_to_file[m.group(1)] = tgt
        for m in use_re.finditer(text):
            prefix = m.group(1).rstrip("/")
            target_var = m.group(2)
            if target_var in var_to_file:
                mounted_by_file.setdefault(var_to_file[target_var], []).append(prefix)
            elif prefix and target_var not in mounted_by_stem:
                mounted_by_stem[target_var] = prefix

    for path, text in files_text.items():
        rel_posix = path.relative_to(root).as_posix()
        prefixes = mounted_by_file.get(path) or (
            [mounted_by_stem[path.stem]] if path.stem in mounted_by_stem else [""]
        )
        for match in route_re.finditer(text):
            method = match.group(1).upper()
            raw_path = match.group(2).strip()
            if not raw_path.startswith("/") and not any(prefixes):
                continue
            line = text.count("\n", 0, match.start()) + 1
            call_window = text[match.start() : min(len(text), match.start() + 900)]
            fields, complete = _ts_handler_body_fields(call_window, all_schemas)
            for pref in prefixes:
                full_path = (
                    join_route_parts(pref, raw_path)
                    if (pref and (not raw_path.startswith(pref) or pref != raw_path))
                    else (
                        raw_path
                        if raw_path.startswith("/")
                        else join_route_parts(pref, raw_path)
                    )
                )
                key = (method, full_path)
                if key in seen:
                    continue
                seen.add(key)
                entry: dict[str, Any] = {
                    "method": method,
                    "path": full_path,
                    "auth": "public",
                    "file": rel_posix,
                    "line": str(line),
                    "fields_complete": complete,
                    "confidence": "heuristic",
                    "source_kind": "node_express",
                }
                if fields:
                    entry["fields"] = ",".join(fields)
                routes.append(entry)
                if len(routes) >= max_routes:
                    return routes

        # NestJS @Controller + @Get/@Post...
        ctrl_m = nest_ctrl_re.search(text)
        if ctrl_m:
            ctrl_pref = (ctrl_m.group(1) or "").strip()
            for nm in nest_method_re.finditer(text):
                method = nm.group(1).upper()
                sub = (nm.group(2) or "").strip()
                full_path = join_route_parts(ctrl_pref, sub)
                key = (method, full_path)
                if key in seen:
                    continue
                seen.add(key)
                line = text.count("\n", 0, nm.start()) + 1
                routes.append(
                    {
                        "method": method,
                        "path": full_path,
                        "auth": "public",
                        "file": rel_posix,
                        "line": str(line),
                        "fields_complete": False,
                        "source_kind": "nestjs",
                    }
                )
                if len(routes) >= max_routes:
                    return routes
    return routes


def _go_java_backend_routes(root: Path, *, max_routes: int) -> list[dict[str, Any]]:
    """通用轻量提取 Go (Gin/Echo) 与 Java/Kotlin (Spring Boot) 后端路由。"""
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    go_group_re = re.compile(r"([A-Za-z_]\w*)\s*:?=\s*[A-Za-z_]\w*\.Group\(\s*[\"']([^\"']+)[\"']")
    go_route_re = re.compile(
        r"\b([A-Za-z_]\w*)\.(GET|POST|PUT|PATCH|DELETE)\(\s*[\"']([^\"']*)[\"']"
    )
    spring_cls_re = re.compile(r"@RequestMapping\(\s*(?:value\s*=\s*)?[\"']([^\"']*)[\"']")
    spring_m_re = re.compile(
        r"@(Get|Post|Put|Patch|Delete)Mapping\(\s*(?:(?:value|path)\s*=\s*)?(?:[\"']([^\"']*)[\"'])?\s*\)",
        re.IGNORECASE,
    )
    ignore_patterns = load_ignore_patterns(root)

    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _FE_IGNORE_DIRS
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            suffix = Path(name).suffix.lower()
            if suffix not in {".go", ".java", ".kt"} or name.endswith("_test.go"):
                continue
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            try:
                if path.stat().st_size > 256 * 1024:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel_posix = path.relative_to(root).as_posix()

            if suffix == ".go":
                groups = {m.group(1): m.group(2) for m in go_group_re.finditer(text)}
                for gm in go_route_re.finditer(text):
                    var_name, method, sub = gm.group(1), gm.group(2).upper(), gm.group(3)
                    full = join_route_parts(groups.get(var_name, ""), sub)
                    key = (method, full)
                    if key in seen:
                        continue
                    seen.add(key)
                    routes.append(
                        {
                            "method": method,
                            "path": full,
                            "auth": "public",
                            "file": rel_posix,
                            "line": str(text.count("\n", 0, gm.start()) + 1),
                            "fields_complete": False,
                            "source_kind": "go_web",
                        }
                    )
                    if len(routes) >= max_routes:
                        return routes
            else:
                cls_m = spring_cls_re.search(text)
                base_pref = cls_m.group(1) if cls_m else ""
                for sm in spring_m_re.finditer(text):
                    method = sm.group(1).upper()
                    sub = sm.group(2) or ""
                    full = join_route_parts(base_pref, sub)
                    key = (method, full)
                    if key in seen:
                        continue
                    seen.add(key)
                    routes.append(
                        {
                            "method": method,
                            "path": full,
                            "auth": "public",
                            "file": rel_posix,
                            "line": str(text.count("\n", 0, sm.start()) + 1),
                            "fields_complete": False,
                            "source_kind": "spring_boot",
                        }
                    )
                    if len(routes) >= max_routes:
                        return routes
    return routes


def _api_routes_with_meta(
    root: Path,
    *,
    max_routes: int = 2000,
    max_py_files: int = 1500,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """提取全语言后端路由与元数据（如动态插件加载器探测结果）。"""
    files_text: dict[Path, str] = {}
    ignore = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "tests",
        "test",
        "".join(("mig", "rations")),
        "locale",
        "docs",
        "storybook",
        "fixtures",
        "static",
        "media",
        "coverage",
    }
    ignore_patterns = load_ignore_patterns(root)
    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in ignore
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            try:
                if path.stat().st_size > 256 * 1024:
                    continue
                files_text[path] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(files_text) >= max_py_files:
                break
        if len(files_text) >= max_py_files:
            break

    py_routes, py_meta = extract_python_api_routes(root, files_text, max_routes=max_routes)
    routes: list[dict[str, Any]] = list(py_routes)
    by_norm_key: dict[tuple[str, str], dict[str, Any]] = {
        (str(r["method"]), _normalize_compare_path(str(r["path"]))): r for r in routes
    }

    for extra_list in (
        _http_handler_routes(files_text),
        _nextjs_app_routes(root, max_routes=max_routes),
        _nextjs_pages_routes(root, max_routes=max_routes),
        _js_backend_routes(root, max_routes=max_routes),
        _go_java_backend_routes(root, max_routes=max_routes),
    ):
        for r in extra_list:
            key = (str(r["method"]), _normalize_compare_path(str(r["path"])))
            if key in by_norm_key:
                continue
            by_norm_key[key] = r
            routes.append(r)
            if len(routes) >= max_routes:
                for item in routes:
                    if isinstance(item, dict) and "confidence" not in item:
                        item["confidence"] = "heuristic"
                return routes, py_meta

    for item in routes:
        if isinstance(item, dict) and "confidence" not in item:
            item["confidence"] = "heuristic"

    # 源码路由优先于 OpenAPI 文档：OpenAPI 若存在对应源码路由，只附加为辅助字段
    for oa in _openapi_routes(root):
        key = (str(oa["method"]), _normalize_compare_path(str(oa["path"])))
        existing = by_norm_key.get(key)
        if existing is not None:
            if not existing.get("fields_complete") and oa.get("fields"):
                existing["openapi_fields"] = str(oa["fields"])
            if not existing.get("status_code") and oa.get("status_code"):
                existing["status_code"] = str(oa["status_code"])
            continue
        by_norm_key[key] = oa
        routes.append(oa)
        if len(routes) >= max_routes:
            break

    return routes, py_meta


def _api_routes(root: Path, *, max_routes: int = 2000) -> list[dict[str, Any]]:
    """确定性提取全语言多级后端路由契约（默认高上限避免大项目截断）。"""
    routes, _meta = _api_routes_with_meta(root, max_routes=max_routes)
    return routes


_LOCAL_FE_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
)


def _strip_local_origin(value: str) -> str | None:
    """把 http(s)://本机主机[:端口]/path 归一为 /path；外部域名返回 None。

    前端与后端分离开发（Flask + Vite）时常把完整本机 URL 写进常量，
    这类调用与同源相对路径等价；第三方外链不能当作本地后端契约输入。
    """
    if not value.startswith(("http://", "https://")):
        return value
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if host not in _LOCAL_FE_HOSTS:
        return None
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def _normalize_fe_const(value: str) -> str:
    """归一化 JS 常量值：本机绝对 URL 转路径，模板占位转 {param}。"""
    stripped = _strip_local_origin(value)
    if stripped is None:
        return ""
    return re.sub(r"\$\{[^}]+\}", "{param}", stripped)


def _normalize_fe_base_url(value: str) -> str:
    """归一化 axios.create baseURL，支持 `${VAR}/api` 这类模板前缀。

    仅保留可确定的前缀：去掉开头的模板变量后必须以 `/` 开头；中间还有
    无法解析的模板变量时放弃（返回空），避免把猜测出来的前缀拼进调用路径。
    """
    text = value.strip()
    while text.startswith("${") and "}" in text:
        text = text[text.index("}") + 1 :]
    stripped = _strip_local_origin(text)
    if stripped is None or not stripped.startswith("/"):
        return ""
    if "${" in stripped:
        return ""
    if stripped == "/":
        return "/"
    return stripped.rstrip("/")


def _resolve_base_url_expr(
    expr: str,
    const_map: dict[str, str],
    const_history: dict[str, list[tuple[int, str]]] | None,
    pos: int,
) -> str:
    """解析 axios.create 的 baseURL 表达式：字面量、模板、常量、拼接与 || 兜底。"""
    text = expr.strip()
    if not text:
        return ""
    for separator in ("||", "??"):
        if separator in text:
            return _resolve_base_url_expr(
                text.rsplit(separator, 1)[-1], const_map, const_history, pos
            )
    if "+" in text:
        resolved = _resolve_fe_path(
            text, const_map, True, const_history=const_history, pos=pos
        )
        if resolved:
            return _normalize_fe_base_url(resolved)
    quoted = re.search(r"[\"'`]([^\"'`]*)[\"'`]", text)
    if quoted:
        return _normalize_fe_base_url(quoted.group(1))
    const_value = _const_value(text, const_map, const_history, pos)
    if const_value:
        return _normalize_fe_base_url(const_value)
    resolved = _resolve_fe_path(
        text, const_map, True, const_history=const_history, pos=pos
    )
    return _normalize_fe_base_url(resolved) if resolved else ""


def _const_value(
    name: str,
    const_map: dict[str, str],
    const_history: dict[str, list[tuple[int, str]]] | None,
    pos: int,
) -> str:
    """优先取调用点之前最近一次赋值，避免同名常量在方法之间复用时错配。"""
    if const_history is not None and pos >= 0:
        value = ""
        for at, candidate in const_history.get(name) or []:
            if at > pos:
                break
            value = candidate
        if value:
            return value
    return const_map.get(name, "")


def _resolve_fe_path(
    expr: str,
    const_map: dict[str, str],
    allow_any_root: bool,
    *,
    const_history: dict[str, list[tuple[int, str]]] | None = None,
    pos: int = -1,
) -> str:
    """将 JS/TS 中的 URL 字符串字面量、模板字符串或常量拼接归一为路径。"""
    raw = expr.strip()
    if not raw:
        return ""
    if raw.startswith("`") and raw.endswith("`") and len(raw) >= 2:
        inner = re.sub(r"\$\{[^}]+\}", "{param}", raw[1:-1])
        path = _strip_local_origin(inner.split("?", 1)[0])
        if path is None:
            return ""
        if (
            path.startswith("/")
            and not path.startswith("//")
            and (allow_any_root or path.startswith("/api"))
        ):
            return path[:120]
        return ""
    segments = _split_top_level(raw, "+")
    if len(segments) > 1:
        resolved: list[str] = []
        used_const = False
        for seg in segments:
            seg_s = seg.strip()
            if (seg_s.startswith(("'", '"')) and seg_s.endswith(("'", '"'))) and len(seg_s) >= 2:
                resolved.append(seg_s[1:-1])
            elif seg_s in const_map and (
                const_value := _const_value(seg_s, const_map, const_history, pos)
            ):
                resolved.append(const_value)
                used_const = True
            else:
                resolved.append("{param}")
        path = _strip_local_origin("".join(resolved).split("?", 1)[0])
        if path is None:
            return ""
        if (
            path.startswith("/")
            and not path.startswith("//")
            and (allow_any_root or used_const or path.startswith("/api"))
        ):
            return path[:120]
        return ""
    if (raw.startswith(("'", '"')) and raw.endswith(("'", '"'))) and len(raw) >= 2:
        path = _strip_local_origin(raw[1:-1].split("?", 1)[0])
        if path is None:
            return ""
        if (
            path.startswith("/")
            and not path.startswith("//")
            and (allow_any_root or path.startswith("/api"))
        ):
            return path[:120]
        return ""
    if raw in const_map:
        const_value = _const_value(raw, const_map, const_history, pos)
        path = _strip_local_origin(const_value.split("?", 1)[0])
        if path is not None and path.startswith("/") and not path.startswith("//"):
            return path[:120]
    return ""


def _is_non_http_call(caller: str) -> bool:
    """判断函数/方法调用是否属于字符串、集合、路由或日志等非 HTTP 调用。"""
    lower = caller.lower()
    if lower in _FE_NON_HTTP_METHODS:
        return True
    if "." not in lower:
        return False
    receiver, method_name = lower.rsplit(".", 1)
    if method_name in _FE_NON_HTTP_METHODS:
        return True
    receiver_head = receiver.rsplit(".", 1)[-1]
    http_verbs = {"get", "post", "put", "patch", "delete", "has", "set"}
    return receiver_head in _FE_NON_HTTP_RECEIVERS and method_name in http_verbs


def _is_explicit_http_client(caller: str) -> bool:
    """判断是否为明确的 HTTP 请求调用，避免把纯路径前缀字符串判断误识别为请求。"""
    lower = caller.lower()
    if lower in _FE_EXPLICIT_HTTP_CLIENTS:
        return True
    if "." not in lower:
        return False
    receiver, method_name = lower.rsplit(".", 1)
    if method_name in {"get", "post", "put", "patch", "delete", "request"}:
        return True
    return any(tok in receiver for tok in ("axios", "http", "request", "client", "api", "fetch"))


def _frontend_api_calls(
    root: Path,
    *,
    max_files: int = 2000,
    max_calls: int = 1000,
) -> list[dict[str, Any]]:
    """静态提取前端 JS/TS/HTML 中发起的后端 API 调用路径、方法与请求体字段。"""
    const_re = re.compile(
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[\"'`]"
        r"((?:https?://|/)[^\"'`\s]*)[\"'`]"
    )
    client_base_re = re.compile(
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*axios\.create\(\s*\{[^}]*?"
        r"\bbaseURL\s*:\s*((?:\$\{[^}\n]*\}|[^,}\n])+)",
        re.DOTALL,
    )
    call_re = re.compile(r"\b([A-Za-z_$][\w$.]*)\s*(?:<[^()\n;{}]{1,120}>\s*)?\(")
    method_field_re = re.compile(r"\bmethod\s*:\s*([^,\n}]+)")
    obj_url_re = re.compile(r"\burl\s*:\s*([\"'`][^\"'`\n]+[\"'`])")
    stringify_re = re.compile(r"JSON\.stringify\s*\(\s*\{")
    calls: dict[tuple[str, str], dict[str, Any]] = {}
    scanned = 0
    ignore_patterns = load_ignore_patterns(root)

    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _FE_IGNORE_DIRS
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            if Path(name).suffix.lower() not in {".js", ".ts", ".tsx", ".jsx", ".vue", ".html"}:
                continue
            if name.endswith((".test.ts", ".test.js", ".spec.ts", ".spec.js")):
                continue
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            rel_parts = [part.lower() for part in path.relative_to(root).parts[:-1]]
            backend_dirs = {
                "server",
                "backend",
                "routers",
                "controllers",
                "middleware",
                "dal",
                "database",
                "alembic",
                "monitor-types",
            }
            frontend_dirs = {"frontend", "front", "web", "client", "ui", "views", "pages"}
            if (set(rel_parts) & backend_dirs) and not (set(rel_parts) & frontend_dirs):
                continue
            if (
                name.startswith("route.") or ("api" in rel_parts and "pages" in rel_parts)
            ) and ("app" in rel_parts or "pages" in rel_parts):
                continue
            try:
                if path.stat().st_size > 128 * 1024:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # NestJS / 后端装饰器控制器文件不作为前端调用文件扫描
            if name.endswith((".controller.ts", ".controller.js")) or re.search(
                r"@Controller\s*\(", text
            ):
                continue
            scanned += 1
            if scanned > max_files:
                break
            rel_posix = path.relative_to(root).as_posix()
            const_map: dict[str, str] = {}
            const_history: dict[str, list[tuple[int, str]]] = {}
            for match in const_re.finditer(text):
                normalized = _normalize_fe_const(match.group(2))
                if not normalized:
                    continue
                const_map[match.group(1)] = normalized
                const_history.setdefault(match.group(1), []).append(
                    (match.start(), normalized)
                )

            client_base_map: dict[str, str] = {}
            for match in client_base_re.finditer(text):
                normalized = _resolve_base_url_expr(
                    match.group(2), const_map, const_history, match.start()
                )
                if normalized:
                    client_base_map[match.group(1).lower()] = normalized
            for match in call_re.finditer(text):
                prefix = text[max(0, match.start() - 16) : match.start()]
                if prefix.rstrip().endswith("function"):
                    continue
                caller = match.group(1)
                lower_caller = caller.lower()
                preceding = text[match.start() - 1] if match.start() > 0 else ""
                if preceding == "@":
                    # 跳过 TS/JS 装饰器调用（如 @Controller/@Get），防止误判为前端请求
                    continue
                if caller in _FE_CALL_SKIP or _is_non_http_call(caller):
                    continue
                http_verbs_lower = {"get", "post", "put", "patch", "delete"}
                is_method_http = (
                    "." in lower_caller and lower_caller.rsplit(".", 1)[1] in http_verbs_lower
                ) or (preceding == "." and lower_caller in http_verbs_lower)
                base_client_name = (
                    lower_caller.rsplit(".", 1)[0]
                    if "." in lower_caller
                    else lower_caller
                )
                is_direct_http = (
                    caller.lower() in _FETCH_LIKE_CLIENTS
                    or base_client_name in client_base_map
                    or "axios" in lower_caller
                    or is_method_http
                )
                open_paren = match.end() - 1
                raw_args = _balanced_group(text, open_paren, "(", ")", limit=900)
                if not raw_args:
                    continue
                args = _split_top_level(raw_args, ",")
                if not args:
                    continue
                norm_path = _resolve_fe_path(
                    args[0],
                    const_map,
                    allow_any_root=is_direct_http,
                    const_history=const_history,
                    pos=match.start(),
                )
                if not norm_path and args[0].lstrip().startswith("{"):
                    url_m = obj_url_re.search(args[0])
                    if url_m:
                        norm_path = _resolve_fe_path(
                            url_m.group(1),
                            const_map,
                            allow_any_root=is_direct_http,
                            const_history=const_history,
                            pos=match.start(),
                        )
                if not norm_path:
                    continue
                # 拼接 axios.create({ baseURL }) 客户端前缀
                client_prefix = client_base_map.get(base_client_name, "")
                if client_prefix and not norm_path.startswith(client_prefix):
                    norm_path = join_route_parts(client_prefix, norm_path)

                if (
                    len(norm_path) > 1
                    and norm_path.endswith("/")
                    and (
                        norm_path in {"/api/", "/api/v1/", "/api/auth/"}
                        or not _is_explicit_http_client(caller)
                    )
                ):
                    continue

                # 解析 HTTP 方法集合（支持三元表达式提取多动词，自定义无 method 封装标 ANY）
                http_methods: list[str] = []
                if is_method_http:
                    verb = lower_caller.rsplit(".", 1)[1] if "." in lower_caller else lower_caller
                    http_methods = [verb.upper()]
                else:
                    mf = method_field_re.search(raw_args)
                    if mf:
                        found = [
                            m.upper()
                            for m in re.findall(
                                r"[\"'](GET|POST|PUT|PATCH|DELETE)[\"']",
                                mf.group(1),
                                re.IGNORECASE,
                            )
                        ]
                        http_methods = list(dict.fromkeys(found))
                    if not http_methods:
                        if (
                            lower_caller in _FE_EXPLICIT_HTTP_CLIENTS
                            or base_client_name in client_base_map
                            or "axios" in lower_caller
                        ):
                            http_methods = ["GET"]
                        else:
                            # 自定义封装函数未显式声明 HTTP 动词时标 ANY，仅验证路径存在性
                            http_methods = ["ANY"]

                # 提取请求体字段：对 fetch 类或 Axios RequestConfig ({ data: {...} }) 做精确解包
                body_keys: list[str] = []
                js_match = stringify_re.search(raw_args)
                if js_match:
                    obj_body = _balanced_group(
                        raw_args, js_match.end() - 1, "{", "}", limit=500
                    )
                    body_keys = _extract_object_keys(obj_body)
                elif (
                    lower_caller not in _FETCH_LIKE_CLIENTS
                    and len(args) >= 2
                    and args[1].lstrip().startswith("{")
                ):
                    obj_body = _balanced_group(
                        args[1], args[1].find("{"), "{", "}", limit=500
                    )
                    raw_keys = _extract_object_keys(obj_body)
                    config_marker_keys = {
                        "method",
                        "headers",
                        "body",
                        "data",
                        "params",
                        "timeout",
                        "signal",
                    }
                    if set(raw_keys) & config_marker_keys:
                        # 识别 Axios { data: { ... } } 内联体并仅提取内层业务键
                        nested_data = re.search(r"\b(?:data|body)\s*:\s*\{", obj_body)
                        if nested_data:
                            inner = _balanced_group(
                                obj_body, nested_data.end() - 1, "{", "}", limit=500
                            )
                            body_keys = _extract_object_keys(inner)
                    else:
                        body_keys = raw_keys
                elif args[0].lstrip().startswith("{"):
                    data_m = re.search(r"\b(?:data|body)\s*:\s*\{", args[0])
                    if data_m:
                        obj_body = _balanced_group(args[0], data_m.end() - 1, "{", "}", limit=500)
                        body_keys = _extract_object_keys(obj_body)
                if body_keys:
                    body_keys = [k for k in body_keys if k not in _REQUEST_INIT_KEYS]

                for http_method in http_methods:
                    key = (http_method, norm_path)
                    existing = calls.get(key)
                    if existing is None:
                        entry: dict[str, Any] = {
                            "source": rel_posix,
                            "method": http_method,
                            "path": norm_path,
                        }
                        if body_keys:
                            entry["body_keys"] = body_keys
                        calls[key] = entry
                        if len(calls) >= max_calls:
                            return list(calls.values())
                    elif body_keys and not existing.get("body_keys"):
                        existing["body_keys"] = body_keys
        if scanned > max_files:
            break
    return list(calls.values())


def _nearest_fe_project_roots(html_dir: Path, root: Path) -> list[Path]:
    """从 HTML 目录向上寻找最近包含前端构建/清单文件（package.json / vite.config）的根目录。"""
    roots: list[Path] = []
    cur = html_dir
    resolved_root = root.resolve()
    for _depth in range(4):
        if (
            (cur / "package.json").is_file()
            or (cur / "vite.config.ts").is_file()
            or (cur / "vite.config.js").is_file()
        ):
            roots.append(cur)
        if cur.resolve() == resolved_root:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return roots


def _frontend_assets(
    root: Path,
    *,
    max_assets: int = 500,
    max_html: int = 200,
) -> list[dict[str, Any]]:
    """提取 HTML 中引用的本地 CSS/JS 资源清单并核对是否在所在前端根/public/static 中存在。"""
    link_re = re.compile(r"<link\b[^>]*?\bhref\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
    script_re = re.compile(r"<script\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
    skip_prefixes = ("http://", "https://", "//", "data:", "javascript:", "mailto:", "#")
    assets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    html_count = 0
    ignore_patterns = load_ignore_patterns(root)

    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _FE_IGNORE_DIRS
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            if Path(name).suffix.lower() != ".html":
                continue
            html_path = base_path / name
            if is_ignored_path(root, html_path, ignore_patterns):
                continue
            try:
                if html_path.stat().st_size > 256 * 1024:
                    continue
                html = html_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            html_count += 1
            rel_posix = html_path.relative_to(root).as_posix()
            fe_roots = _nearest_fe_project_roots(html_path.parent, root)
            candidates = [
                ("link", m.group(1).strip()) for m in link_re.finditer(html)
            ] + [
                ("script", m.group(1).strip()) for m in script_re.finditer(html)
            ]
            for tag, raw_ref in candidates:
                if not raw_ref or raw_ref.lower().startswith(skip_prefixes):
                    continue
                if any(ch in raw_ref for ch in ("{", "}", "$", "%")):
                    continue
                key = (rel_posix, raw_ref)
                if key in seen:
                    continue
                seen.add(key)
                no_query = raw_ref.split("?", 1)[0].split("#", 1)[0]
                clean = no_query.lstrip("/")
                search_bases = [
                    html_path.parent,
                    html_path.parent / "public",
                    html_path.parent / "static",
                    *fe_roots,
                    *(r / "public" for r in fe_roots),
                    *(r / "static" for r in fe_roots),
                    root,
                    root / "static",
                    root / "public",
                ]
                exists = bool(clean and any((b / clean).is_file() for b in search_bases))
                assets.append(
                    {
                        "source": rel_posix,
                        "tag": tag,
                        "href": raw_ref,
                        "exists_in_repo": exists,
                    }
                )
                if len(assets) >= max_assets:
                    return assets
            if html_count >= max_html:
                return assets
    return assets
