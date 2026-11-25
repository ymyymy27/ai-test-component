"""基于标准库 AST 的通用 Python Web 路由与请求体 Schema 解析器。

纯静态、无项目名硬编码，支持：
- 模块级与配置类常量跨文件求值（含 f-string 拼接与环境变量默认值，多轮不动点求解）；
- FastAPI / Starlette 多级嵌套 APIRouter 与 include_router 前缀传播（含动态加载插件路由根自传播）；
- Flask Blueprint 与 register_blueprint 嵌套前缀传播；
- Django urlpatterns 与 path / re_path / include 递归展开；
- Pydantic / SQLModel / TypedDict / dataclass 闭集请求体 Schema 提取与完备性标记。
"""

from __future__ import annotations

import ast
import re
import tomllib
from contextlib import suppress
from pathlib import Path
from typing import Any

_HTTP_DECORATOR_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_DJANGO_PARAM_RE = re.compile(r"<(?:[A-Za-z_]\w*:)?([A-Za-z_]\w*)>")
_REGEX_PARAM_RE = re.compile(r"\(\?P<([A-Za-z_]\w*)>[^)]*\)|\([^)]*\)")
_OPEN_BODY_TYPES = frozenset(
    {"Request", "WebSocket", "dict", "Dict", "Any", "Mapping", "MutableMapping"}
)


def join_route_parts(*parts: str) -> str:
    """合并多段路由前缀与子路径，保留单一前导斜杠并消除重复首尾前缀。"""
    cleaned: list[str] = []
    for raw in parts:
        piece = raw.strip()
        if not piece or piece == "/":
            continue
        stripped = "/" + piece.strip("/")
        if cleaned and cleaned[-1].endswith(stripped):
            continue
        cleaned.append(stripped)
    return "".join(cleaned) or "/"


def _normalize_django_subpath(raw: str) -> str:
    text = raw.strip().lstrip("^").rstrip("$")
    text = _DJANGO_PARAM_RE.sub(r"{\1}", text)
    text = _REGEX_PARAM_RE.sub(lambda m: f"{{{m.group(1) or 'id'}}}", text)
    return text.strip("/")


def _eval_expr(
    node: ast.AST | None,
    local_consts: dict[str, str],
    global_consts: dict[str, str],
) -> str | None:
    """保守求值 AST 字符串表达式：字面量、变量/属性常量、+ 拼接、f-string 与带默认值的 getenv。"""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in local_consts:
            return local_consts[node.id]
        return global_consts.get(node.id)
    if isinstance(node, ast.Attribute):
        full = _attr_dotted_name(node)
        if full and full in local_consts:
            return local_consts[full]
        if full and full in global_consts:
            return global_consts[full]
        attr = node.attr
        if attr in local_consts:
            return local_consts[attr]
        return global_consts.get(attr)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _eval_expr(node.left, local_consts, global_consts)
        right = _eval_expr(node.right, local_consts, global_consts)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                parts.append(item.value)
            elif isinstance(item, ast.FormattedValue):
                val = _eval_expr(item.value, local_consts, global_consts)
                if val is None:
                    return None
                parts.append(val)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.Call):
        callee = _attr_dotted_name(node.func) or ""
        if callee.endswith(("getenv", "environ.get")) and len(node.args) >= 2:
            return _eval_expr(node.args[1], local_consts, global_consts)
    return None


def _attr_dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _call_short_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _parse_trees(files_text: dict[Path, str]) -> dict[Path, ast.Module]:
    trees: dict[Path, ast.Module] = {}
    for path, text in files_text.items():
        with suppress(SyntaxError, ValueError):
            trees[path] = ast.parse(text, filename=str(path))
    return trees


def _collect_constants(
    trees: dict[Path, ast.Module],
) -> tuple[dict[Path, dict[str, str]], dict[str, str]]:
    """跨文件收集路径/版本类字符串常量，支持多轮不动点展开引用。"""
    per_file: dict[Path, dict[str, str]] = {path: {} for path in trees}
    global_consts: dict[str, str] = {}

    def record(path: Path, name: str, val: str) -> None:
        if "\n" in val or len(val) > 160:
            return
        per_file[path][name] = val
        # 全局索引仅收高信号全大写/前缀命名，或首次出现的值，避免常见小写字段污染
        if name.isupper() or "prefix" in name.lower() or "path" in name.lower():
            global_consts.setdefault(name, val)

    for _round in range(3):
        changed = False
        for path, tree in trees.items():
            local_map = per_file[path]
            for node in ast.walk(tree):
                target_names: list[str] = []
                value_node: ast.AST | None = None
                if isinstance(node, ast.Assign):
                    value_node = node.value
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            target_names.append(tgt.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    value_node = node.value
                    target_names.append(node.target.id)
                if not target_names or value_node is None:
                    continue
                val = _eval_expr(value_node, local_map, global_consts)
                if val is None or not val.startswith("/"):
                    continue
                for name in target_names:
                    if local_map.get(name) != val:
                        record(path, name, val)
                        changed = True
        if not changed:
            break
    return per_file, global_consts


def _build_py_module_map(root: Path, files_text: dict[Path, str]) -> dict[str, Path]:
    mod_map: dict[str, Path] = {}
    ambiguous: set[str] = set()
    for path in files_text:
        rel_parts = list(path.relative_to(root).with_suffix("").parts)
        if rel_parts and rel_parts[-1] == "__init__":
            rel_parts = rel_parts[:-1]
        if not rel_parts:
            continue
        for start in range(min(4, len(rel_parts))):
            key = ".".join(rel_parts[start:])
            if key in mod_map and mod_map[key] != path:
                ambiguous.add(key)
            else:
                mod_map[key] = path
    for key in ambiguous:
        mod_map.pop(key, None)
    return mod_map


def _resolve_module_path(
    mod_expr: str,
    level: int,
    cur_file: Path,
    root: Path,
    mod_map: dict[str, Path],
) -> tuple[Path | None, str]:
    cur_rel_parts = list(cur_file.relative_to(root).parent.parts)
    if level > 0:
        up = max(0, len(cur_rel_parts) - (level - 1))
        rest = [p for p in mod_expr.split(".") if p] if mod_expr else []
        resolved_name = ".".join(cur_rel_parts[:up] + rest)
        return mod_map.get(resolved_name), resolved_name
    target = mod_map.get(mod_expr)
    if target is not None:
        return target, mod_expr
    if "." in mod_expr:
        parts = mod_expr.split(".")
        for start in range(1, len(parts)):
            cand_name = ".".join(parts[start:])
            cand = mod_map.get(cand_name)
            if cand is not None:
                return cand, cand_name
    return None, mod_expr


def _collect_imports(
    trees: dict[Path, ast.Module],
    root: Path,
    mod_map: dict[str, Path],
) -> dict[Path, dict[str, tuple[Path, str]]]:
    """解析每个文件局部标识符对应的 (目标文件, 目标符号名)，并折叠 2 层 __init__ 重导出。"""
    raw_imports: dict[Path, dict[str, tuple[Path, str]]] = {}
    for path, tree in trees.items():
        mapping: dict[str, tuple[Path, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base_mod = node.module or ""
                target_file, resolved_mod = _resolve_module_path(
                    base_mod, node.level, path, root, mod_map
                )
                for alias in node.names:
                    orig = alias.name
                    if orig == "*":
                        continue
                    local_name = alias.asname or orig
                    sub_key = f"{resolved_mod}.{orig}" if resolved_mod else orig
                    sub_file = mod_map.get(sub_key)
                    if sub_file is not None and (
                        target_file is None or target_file.name == "__init__.py"
                    ):
                        mapping[local_name] = (sub_file, "router")
                    elif target_file is not None:
                        mapping[local_name] = (target_file, orig)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target_file, _ = _resolve_module_path(alias.name, 0, path, root, mod_map)
                    if target_file is not None:
                        local_name = alias.asname or alias.name.split(".")[-1]
                        mapping[local_name] = (target_file, "router")
        raw_imports[path] = mapping

    # 展开 __init__.py 导出链路（最多 2 跳），解决包内再导出真实符号名的问题
    for _depth in range(2):
        for _path, mapping in raw_imports.items():
            for local_name, (tgt_file, tgt_sym) in list(mapping.items()):
                if tgt_file in raw_imports and tgt_sym in raw_imports[tgt_file]:
                    next_file, next_sym = raw_imports[tgt_file][tgt_sym]
                    mapping[local_name] = (next_file, next_sym)
    return raw_imports


def _extract_schemas(trees: dict[Path, ast.Module]) -> dict[str, tuple[set[str], bool]]:
    """提取 Pydantic / SQLModel / TypedDict / dataclass 类定义字段及是否允许 extra。"""
    bases_raw: dict[str, list[str]] = {}
    own_fields: dict[str, set[str]] = {}
    allows_extra: dict[str, bool] = {}

    model_markers = {"BaseModel", "SQLModel", "TypedDict", "Schema", "BaseSchema", "SchemaBase"}

    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            has_dataclass = any(
                _call_short_name(dec.func if isinstance(dec, ast.Call) else dec) == "dataclass"
                for dec in node.decorator_list
            )
            base_names = [_call_short_name(b) for b in node.bases if _call_short_name(b)]
            is_model = has_dataclass or any(
                b in model_markers or b.endswith(("Schema", "Model", "Base")) for b in base_names
            )
            if not is_model:
                continue
            bases_raw[node.name] = base_names
            fields: set[str] = set()
            extra_open = False
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fname = stmt.target.id
                    if fname == "model_config":
                        if "allow" in ast.dump(stmt.value or ast.Constant("")):
                            extra_open = True
                        continue
                    if not fname.startswith("_"):
                        fields.add(fname)
                elif isinstance(stmt, ast.ClassDef) and stmt.name == "Config":
                    for sub in stmt.body:
                        if isinstance(sub, ast.Assign):
                            for t in sub.targets:
                                if (
                                    isinstance(t, ast.Name)
                                    and t.id == "extra"
                                    and isinstance(sub.value, ast.Constant)
                                    and str(sub.value.value).lower() == "allow"
                                ):
                                    extra_open = True
            own_fields[node.name] = fields
            allows_extra[node.name] = extra_open

    resolved: dict[str, tuple[set[str], bool]] = {}

    def resolve_cls(name: str, visiting: frozenset[str]) -> tuple[set[str], bool]:
        if name in resolved:
            return resolved[name]
        if name in visiting or name not in own_fields:
            return set(), False
        acc = set(own_fields[name])
        open_flag = allows_extra.get(name, False)
        nxt = visiting | {name}
        for bname in bases_raw.get(name, []):
            bfields, bopen = resolve_cls(bname, nxt)
            acc.update(bfields)
            open_flag = open_flag or bopen
        resolved[name] = (acc, open_flag)
        return resolved[name]

    for cls_name in own_fields:
        resolve_cls(cls_name, frozenset())
    return resolved


def _infer_auth_from_text(snippet: str, default_auth: str = "public") -> str:
    lowered = snippet.lower()
    if "superuser" in lowered or "is_admin" in lowered or "require_admin" in lowered:
        return "superuser"
    if (
        "current_user" in lowered
        or "get_current_user" in lowered
        or "dependsjwtauth" in lowered
        or "dependsrbac" in lowered
        or "login_required" in lowered
    ):
        return "user"
    return default_auth


def _kw_arg(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _route_path_params(route_path: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\{([A-Za-z_]\w*)\}", route_path)}


def _extract_fn_body_fields(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    full_path: str,
    schemas: dict[str, tuple[set[str], bool]],
) -> tuple[list[str], bool]:
    """提取路由函数签名所声明的请求体 Schema 字段及完备性标记。"""
    path_params = _route_path_params(full_path)
    all_args = list(fn.args.posonlyargs) + list(fn.args.args) + list(fn.args.kwonlyargs)
    if fn.args.kwarg is not None:
        return [], False

    fields: set[str] = set()
    matched_schema = False
    open_body = False
    scalar_types = {
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "UploadFile",
        "BackgroundTasks",
        "Response",
    }

    for arg in all_args:
        if arg.arg in {"self", "cls"} or arg.arg in path_params:
            continue
        ann = arg.annotation
        if ann is None:
            continue
        type_name = _call_short_name(ann)
        if not type_name and isinstance(ann, ast.Subscript):
            type_name = _call_short_name(ann.value)
        if type_name in _OPEN_BODY_TYPES:
            open_body = True
            continue
        if type_name in schemas:
            cls_fields, extra_open = schemas[type_name]
            fields.update(cls_fields)
            matched_schema = True
            if extra_open:
                open_body = True
            continue
        if isinstance(ann, ast.Subscript):
            # 如 Body(...) 或 Optional[MySchema]
            inner_names = [
                _call_short_name(n)
                for n in ast.walk(ann.slice)
                if isinstance(n, (ast.Name, ast.Attribute))
            ]
            for iname in inner_names:
                if iname in _OPEN_BODY_TYPES:
                    open_body = True
                elif iname in schemas:
                    cls_fields, extra_open = schemas[iname]
                    fields.update(cls_fields)
                    matched_schema = True
                    if extra_open:
                        open_body = True
        elif type_name in scalar_types:
            continue

    if not matched_schema or open_body or not fields:
        return [], False
    return sorted(fields), True


def _extract_django_routes(
    root: Path,
    trees: dict[Path, ast.Module],
    imports_by_file: dict[Path, dict[str, tuple[Path, str]]],
) -> list[dict[str, Any]]:
    """递归解析 Django urlpatterns 中的 path/re_path/include 路由声明。"""
    url_patterns_by_file: dict[Path, list[tuple[str, str, int, Path | None]]] = {}
    for path, tree in trees.items():
        if "url" not in path.stem.lower():
            continue
        entries: list[tuple[str, str, int, Path | None]] = []
        imports = imports_by_file.get(path, {})
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "urlpatterns" for t in node.targets):
                continue
            if not isinstance(node.value, (ast.List, ast.Tuple)):
                continue
            for elt in node.value.elts:
                if not isinstance(elt, ast.Call):
                    continue
                cname = _call_short_name(elt.func)
                if cname not in {"path", "re_path", "url"} or not elt.args:
                    continue
                first = elt.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                sub = _normalize_django_subpath(first.value)
                inc_target: Path | None = None
                if len(elt.args) >= 2 and isinstance(elt.args[1], ast.Call):
                    inc_call = elt.args[1]
                    if _call_short_name(inc_call.func) == "include" and inc_call.args:
                        inc_arg = inc_call.args[0]
                        if isinstance(inc_arg, ast.Constant) and isinstance(inc_arg.value, str):
                            # include("app.urls")
                            mod_str = inc_arg.value
                            for p in trees:
                                rel_mod = ".".join(p.relative_to(root).with_suffix("").parts)
                                if rel_mod == mod_str or rel_mod.endswith(f".{mod_str}"):
                                    inc_target = p
                                    break
                        elif isinstance(inc_arg, ast.Name) and inc_arg.id in imports:
                            inc_target = imports[inc_arg.id][0]
                entries.append((sub, path.relative_to(root).as_posix(), elt.lineno, inc_target))
        if entries:
            url_patterns_by_file[path] = entries

    if not url_patterns_by_file:
        return []

    referenced = {
        inc_tgt
        for entries in url_patterns_by_file.values()
        for _, _, _, inc_tgt in entries
        if inc_tgt is not None
    }
    roots = [p for p in url_patterns_by_file if p not in referenced] or list(url_patterns_by_file)
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def walk(url_file: Path, prefix: str, visited: frozenset[Path]) -> None:
        if url_file in visited or url_file not in url_patterns_by_file:
            return
        nxt = visited | {url_file}
        for sub, rel_posix, lineno, inc_tgt in url_patterns_by_file[url_file]:
            full = join_route_parts(prefix, sub) if (prefix or sub) else "/"
            if inc_tgt is not None:
                walk(inc_tgt, "" if full == "/" else full, nxt)
            else:
                key = ("ANY", full)
                if key not in seen:
                    seen.add(key)
                    routes.append(
                        {
                            "method": "ANY",
                            "path": full,
                            "auth": "public",
                            "file": rel_posix,
                            "line": str(lineno),
                            "fields_complete": False,
                            "source_kind": "django",
                        }
                    )

    for rfile in sorted(roots, key=str):
        walk(rfile, "", frozenset())
    return routes


def _plugin_toml_prefix_for_file(
    route_file: Path,
    root: Path,
    toml_cache: dict[Path, tuple[Path, dict[str, Any]] | None],
    global_consts: dict[str, str],
) -> str:
    """从最近的 plugin.toml 清单读取插件路由前缀，并与 api/vN 子包结构通用拼接。"""
    resolved_root = root.resolve()
    found: tuple[Path, dict[str, Any]] | None = None
    if route_file in toml_cache:
        found = toml_cache[route_file]
    else:
        cur = route_file.parent
        for _depth in range(6):
            cand = cur / "plugin.toml"
            if cand.is_file():
                with suppress(OSError, tomllib.TOMLDecodeError):
                    parsed = tomllib.loads(cand.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(parsed, dict):
                        found = (cur, parsed)
                        break
            if cur.resolve() == resolved_root or cur.parent == cur:
                break
            cur = cur.parent
        toml_cache[route_file] = found
    if found is None:
        return ""

    plugin_root, data = found
    api_section = data.get("api")
    if not isinstance(api_section, dict):
        return ""
    stem = route_file.stem
    mod_prefix = ""
    if isinstance(api_section.get(stem), dict):
        raw = api_section[stem].get("prefix")
        if isinstance(raw, str):
            mod_prefix = raw.strip()
    elif isinstance(api_section.get("prefix"), str):
        mod_prefix = str(api_section["prefix"]).strip()
    else:
        sub_tables = [
            v
            for v in api_section.values()
            if isinstance(v, dict) and isinstance(v.get("prefix"), str)
        ]
        if len(sub_tables) == 1:
            mod_prefix = str(sub_tables[0]["prefix"]).strip()
    if not mod_prefix:
        return ""

    rel_parts = list(route_file.relative_to(plugin_root).parts[:-1])
    # 从目录结构抽出通用 api/<version>/<subdomains>（如 api/v1/sys -> /api/v1 + /sys）
    base_parts: list[str] = []
    if "api" in rel_parts:
        api_idx = rel_parts.index("api")
        tail = rel_parts[api_idx:]
        if len(tail) >= 2 and re.fullmatch(r"v\d+", tail[1], re.IGNORECASE):
            ver_tag = f"/{tail[1].lower()}"
            matched_const = next(
                (v for v in global_consts.values() if v.rstrip("/").endswith(ver_tag)),
                f"/api{ver_tag}",
            )
            base_parts.append(matched_const)
            base_parts.extend(tail[2:])
        else:
            base_parts.extend(tail)
    elif rel_parts:
        base_parts.extend(rel_parts)

    return join_route_parts(*base_parts, mod_prefix)


def extract_python_api_routes(
    root: Path,
    files_text: dict[Path, str],
    *,
    max_routes: int = 2000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """用 Python AST 提取 FastAPI / Flask / Django 路由与 Meta 信息（含 has_dynamic_loader）。"""
    trees = _parse_trees(files_text)
    if not trees:
        return [], {"has_dynamic_loader": False}

    per_file_consts, global_consts = _collect_constants(trees)
    mod_map = _build_py_module_map(root, files_text)
    imports_by_file = _collect_imports(trees, root, mod_map)
    schemas = _extract_schemas(trees)

    # 1. 检测通用动态插件加载器（importlib.import_module + include_router/register_blueprint）
    has_importlib = False
    has_router_mount = False
    # (path, var_name) -> (own_prefix, default_auth)
    file_routers: dict[Path, dict[str, tuple[str, str]]] = {path: {} for path in trees}
    # (child_file, child_sym) -> [(parent_file, parent_var, mount_prefix)]
    parents: dict[tuple[Path, str], list[tuple[Path, str, str]]] = {}

    for path, tree in trees.items():
        local_consts = per_file_consts.get(path, {})
        imports = imports_by_file.get(path, {})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                dotted = _attr_dotted_name(node.func) or ""
                short = _call_short_name(node.func)
                if (
                    dotted.endswith(("import_module", "iter_modules", "load_plugin_routers"))
                    or short == "__import__"
                ):
                    has_importlib = True
                if (
                    short in {"include_router", "register_blueprint"}
                    and isinstance(node.func, ast.Attribute)
                ):
                    has_router_mount = True
                    parent_var = _call_short_name(node.func.value)
                    if not parent_var or not node.args:
                        continue
                    child_node = node.args[0]
                    pref_node = _kw_arg(node, "prefix") or _kw_arg(node, "url_prefix")
                    mount_prefix = (
                        _eval_expr(pref_node, local_consts, global_consts) or ""
                    ).strip().rstrip("/")
                    child_file: Path | None = None
                    child_sym = ""
                    if isinstance(child_node, ast.Name):
                        tgt = imports.get(child_node.id)
                        if tgt is not None:
                            child_file, child_sym = tgt
                        elif child_node.id in file_routers[path]:
                            child_file, child_sym = path, child_node.id
                    elif isinstance(child_node, ast.Attribute):
                        base_name = _call_short_name(child_node.value)
                        tgt = imports.get(base_name)
                        if tgt is not None:
                            child_file = tgt[0]
                            child_sym = child_node.attr
                    if child_file is not None and child_sym:
                        parents.setdefault((child_file, child_sym), []).append(
                            (path, parent_var, mount_prefix)
                        )
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                cname = _call_short_name(node.value.func)
                if cname in {"APIRouter", "FastAPI", "Blueprint", "Flask"}:
                    pref_node = _kw_arg(node.value, "prefix") or _kw_arg(node.value, "url_prefix")
                    own_prefix = (
                        _eval_expr(pref_node, local_consts, global_consts) or ""
                    ).strip().rstrip("/")
                    auth = _infer_auth_from_text(ast.dump(node.value), "public")
                    for target_expr in node.targets:
                        if isinstance(target_expr, ast.Name):
                            file_routers[path][target_expr.id] = (own_prefix, auth)

    memo: dict[tuple[Path, str], list[str]] = {}
    toml_cache: dict[Path, tuple[Path, dict[str, Any]] | None] = {}

    def resolve_prefixes(
        node: tuple[Path, str],
        visiting: frozenset[tuple[Path, str]],
    ) -> list[str]:
        if node in memo:
            return memo[node]
        if node in visiting:
            return [""]
        nxt = visiting | {node}
        fpath, var_name = node
        own_prefix = file_routers.get(fpath, {}).get(var_name, ("", "public"))[0]
        p_edges = parents.get(node, [])
        if not p_edges:
            if not own_prefix:
                toml_pref = _plugin_toml_prefix_for_file(fpath, root, toml_cache, global_consts)
                if toml_pref:
                    memo[node] = [toml_pref]
                    return memo[node]
            # 自注册路由根（含动态插件独立的 router.py 根）保留自身前缀供子路由继承
            memo[node] = [own_prefix]
            return memo[node]
        resolved: list[str] = []
        for p_file, p_var, m_pref in p_edges:
            for p_full in resolve_prefixes((p_file, p_var), nxt):
                combined = join_route_parts(p_full, m_pref, own_prefix)
                val = "" if combined == "/" else combined
                if val not in resolved:
                    resolved.append(val)
        memo[node] = resolved or [own_prefix]
        return memo[node]

    all_router_nodes = set(parents.keys()) | {
        (fpath, rvar) for fpath, defs in file_routers.items() for rvar in defs
    }
    for rnode in sorted(all_router_nodes, key=lambda x: (str(x[0]), x[1])):
        resolve_prefixes(rnode, frozenset())

    # 2. 提取全部函数装饰器路由（FastAPI + Flask）
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(trees, key=str):
        tree = trees[path]
        local_consts = per_file_consts.get(path, {})
        rel_posix = path.relative_to(root).as_posix()
        defs = file_routers.get(path, {})
        default_auth = next(iter(defs.values()))[1] if defs else "public"

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                router_var = _call_short_name(dec.func.value)
                dec_attr = dec.func.attr.lower()
                if dec_attr in _HTTP_DECORATOR_METHODS:
                    methods = [dec_attr.upper()]
                elif dec_attr in {"route", "api_route"}:
                    m_arg = _kw_arg(dec, "methods")
                    methods = []
                    if isinstance(m_arg, (ast.List, ast.Tuple, ast.Set)):
                        for elt in m_arg.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                upper = elt.value.upper()
                                if upper.lower() in _HTTP_DECORATOR_METHODS:
                                    methods.append(upper)
                    if not methods:
                        methods = ["GET"]
                else:
                    continue

                if not router_var:
                    continue
                raw_sub_path = ""
                if dec.args:
                    evaluated = _eval_expr(dec.args[0], local_consts, global_consts)
                    if evaluated is None:
                        continue
                    raw_sub_path = evaluated
                else:
                    path_kw = _kw_arg(dec, "path") or _kw_arg(dec, "rule")
                    if path_kw is not None:
                        evaluated = _eval_expr(path_kw, local_consts, global_consts)
                        if evaluated is None:
                            continue
                        raw_sub_path = evaluated

                prefixes = memo.get((path, router_var)) or [
                    defs.get(router_var, ("", default_auth))[0]
                ]
                auth = _infer_auth_from_text(
                    ast.dump(dec) + " " + ast.dump(node.args),
                    defs.get(router_var, ("", default_auth))[1],
                )
                sc_node = _kw_arg(dec, "status_code")
                status_code = (
                    str(sc_node.value)
                    if isinstance(sc_node, ast.Constant) and isinstance(sc_node.value, int)
                    else ""
                )
                line_no = dec.lineno

                for prefix in prefixes:
                    full_path = (
                        join_route_parts(prefix, raw_sub_path)
                        if (prefix or raw_sub_path)
                        else "/"
                    )
                    fn_fields, fields_complete = _extract_fn_body_fields(node, full_path, schemas)
                    for method in methods:
                        key = (method, full_path)
                        if key in seen:
                            continue
                        seen.add(key)
                        entry: dict[str, Any] = {
                            "method": method,
                            "path": full_path,
                            "auth": auth,
                            "file": rel_posix,
                            "line": str(line_no),
                            "fields_complete": fields_complete,
                            "source_kind": "python_ast",
                        }
                        if fn_fields:
                            entry["fields"] = ",".join(fn_fields)
                        if status_code:
                            entry["status_code"] = status_code
                        routes.append(entry)
                        if len(routes) >= max_routes:
                            return routes, {
                                "has_dynamic_loader": has_importlib and has_router_mount
                            }

    for dj in _extract_django_routes(root, trees, imports_by_file):
        key = (str(dj["method"]), str(dj["path"]))
        if key not in seen:
            seen.add(key)
            routes.append(dj)
            if len(routes) >= max_routes:
                break

    return routes, {"has_dynamic_loader": has_importlib and has_router_mount}
