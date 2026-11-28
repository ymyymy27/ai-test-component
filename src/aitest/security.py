"""Redaction, path checks and generated-script screening."""

from __future__ import annotations

import ast
import fnmatch
import re
import urllib.parse
from pathlib import Path
from typing import Any

_DANGEROUS = (
    r"\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)\b",
    r"\brm\s+-[rR]\b[^\n]*\s+-[fF]\b",
    r"\brm\s+-[fF]\b[^\n]*\s+-[rR]\b",
    r"\brm\s+--recursive\b",
    r"\brm\s+--force\b",
    r"\bshutil\.rmtree\b",
    r"\bos\.remove\b",
    r"\bos\.unlink\b",
    r"\.unlink\s*\(",
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bDROP\s+DATABASE\b",
    r"\bDROP\s+TABLE\b",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bgit\s+push\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bformat\s+[a-z]:",
    r"\bshutdown\b",
    r"\bmkfs\b",
    r"\bRemove-Item\b[^\n]*-(?:Recurse|Force)\b",
    r"\bdel\s+/[sq]\b",
    r"\brd\s+/s\b",
    r"\bchmod\s+777\b",
    r"\bsudo\b",
    r"\b(?:system32|Windows\\System32)\b",
    r"\bC:\\Windows\b",
)

_SECRET_SEGMENTS = frozenset(
    {
        "key",
        "token",
        "secret",
        "password",
        "passwd",
        "passphrase",
        "credential",
        "credentials",
        "authorization",
        "cookie",
    }
)

_JOINED_SECRET_NAMES = frozenset(
    {
        "apikey",
        "accesskey",
        "privatekey",
        "secretkey",
        "clientsecret",
        "accesstoken",
        "authtoken",
        "refreshtoken",
        "dbpassword",
        "dbpasswd",
    }
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{12,}"),
    re.compile(r"(?i)\b[A-Za-z][A-Za-z0-9+.-]*://[^\s:@/]+:[^\s@/]+@"),
)


def _name_segments(name: str) -> list[str]:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    segments: list[str] = []
    for part in parts:
        if not part:
            continue
        segments.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part))
    return [segment.lower() for segment in segments]


_META_FIELD_SUFFIXES: tuple[str, ...] = (
    "_header",
    "_headers",
    "_selector",
    "_path",
    "_url",
    "_endpoint",
    "_field",
    "_fields",
    "_domain",
    "_type",
    "_env",
    "_env_var",
    "_header_name",
    "_key_name",
    "_name",
)


def is_secret_field(name: str) -> bool:
    """Return True for field names that may carry a credential body.

    A bare ``key`` segment is only a credential when it stands alone or follows
    a known modifier (``api_key``, ``signing.key``); mapping keys such as
    ``fields.key`` and metadata field names (``*_env``, ``*_header``, ``*_url``)
    must stay usable.
    """
    lowered = name.strip().lower()
    if any(
        lowered.endswith(suffix) and len(lowered) > len(suffix)
        for suffix in _META_FIELD_SUFFIXES
    ):
        return False
    segments = _name_segments(name)
    if "".join(segments) in _JOINED_SECRET_NAMES:
        return True
    for index, segment in enumerate(segments):
        if segment == "key":
            if len(segments) == 1 or name.isupper():
                return True
            if index > 0 and segments[index - 1] in {
                "api",
                "access",
                "secret",
                "private",
                "public",
                "signing",
                "encryption",
                "client",
                "model",
                "openai",
                "anthropic",
                "github",
                "aws",
            }:
                return True
            continue
        if segment in _SECRET_SEGMENTS:
            return True
    return False


def looks_like_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def secret_values_in(value: Any) -> list[str]:
    """Collect credential-looking string values from a config-like tree."""
    found: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(child, str) and is_secret_field(str(key)):
                    if child.strip():
                        found.append(child)
                else:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str) and looks_like_secret_value(item):
            found.append(item)

    walk(value)
    return found


def scan_script(text: str) -> list[str]:
    """Return matched dangerous patterns; empty list means allowed."""
    matches: list[str] = []
    for pattern in _DANGEROUS:
        if re.search(pattern, text, re.IGNORECASE):
            matches.append(pattern)
    return matches


def ensure_within(root: Path, target: Path) -> Path:
    root_resolved = root.resolve()
    target_resolved = target.resolve()
    if target_resolved != root_resolved and root_resolved not in target_resolved.parents:
        raise ValueError(f"路径越界：{target}")
    return target_resolved


_DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "*.keystore",
    ".npmrc",
    ".pypirc",
)

_METADATA_HOSTS = frozenset(
    {"169.254.169.254", "metadata.google.internal", "metadata.internal"}
)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def load_ignore_patterns(root: Path) -> list[str]:
    """合并 .gitignore、.aitestignore 与默认高危凭据文件排除规则。"""
    patterns: list[str] = list(_DEFAULT_IGNORE_PATTERNS)
    for filename in (".gitignore", ".aitestignore"):
        ignore_file = root / filename
        if not ignore_file.is_file():
            continue
        try:
            lines = ignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if line not in patterns:
                patterns.append(line)
    return patterns


def is_ignored_path(root: Path, path: Path, patterns: list[str]) -> bool:
    """判断路径是否命中忽略或高危凭据规则。"""
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return False
    if not rel or rel == ".":
        return False
    parts = rel.split("/")
    for raw_pattern in patterns:
        pat = raw_pattern.strip().replace("\\", "/")
        if not pat:
            continue
        is_dir_only = pat.endswith("/")
        pat = pat.lstrip("/").rstrip("/")
        if not pat:
            continue
        if "/" in pat:
            if is_dir_only:
                for idx in range(1, len(parts) + 1):
                    prefix = "/".join(parts[:idx])
                    if fnmatch.fnmatchcase(prefix, pat):
                        return True
            elif fnmatch.fnmatchcase(rel, pat):
                return True
        else:
            targets = parts if (is_dir_only or path.is_dir()) else [parts[-1], *parts[:-1]]
            if any(fnmatch.fnmatchcase(part, pat) for part in targets):
                return True
    return False


def _normalize_allowed_host(entry: str) -> str:
    text = entry.strip().lower()
    if not text:
        return ""
    if "://" in text:
        parsed = urllib.parse.urlsplit(text)
        return (parsed.hostname or "").lower().strip(".")
    if "/" in text:
        text = text.split("/", 1)[0]
    if text.startswith("[") and "]" in text:
        return text[1 : text.index("]")].strip(".")
    if text.count(":") == 1:
        text = text.split(":", 1)[0]
    return text.strip(".")


def validate_target_url(url: str, allowed_hosts: list[str] | None = None) -> tuple[bool, str]:
    """校验 HTTP 执行目标是否在允许范围内，阻止外发请求及云元数据探测。"""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False, f"URL 格式非法：{url}"
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return False, f"只支持 http/https 协议：{url}"
    if not parsed.hostname:
        return False, f"URL 缺少有效主机名：{url}"
    host = parsed.hostname.lower().strip(".")
    if host in _METADATA_HOSTS:
        return False, f"安全策略禁止访问云元数据地址：{host}"
    if host in _LOOPBACK_HOSTS:
        return True, ""
    for raw in allowed_hosts or []:
        if not isinstance(raw, str):
            continue
        candidate = _normalize_allowed_host(raw)
        if not candidate:
            continue
        if host == candidate or fnmatch.fnmatchcase(host, candidate):
            return True, ""
    return (
        False,
        f"目标主机 {host} 不在允许白名单内；请在配置 services 或 security.allowed_hosts 后重试",
    )


_STRONG_SECRET_PREFIXES: tuple[str, ...] = (
    "sk-",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "glpat-",
    "xox",
    "AKIA",
    "AIza",
    "npm_",
    "sk_live_",
    "eyJ",
)

_STRUCTURAL_META_KEYS: frozenset[str] = frozenset(
    {
        "case_id",
        "evidence_refs",
        "source_commit",
        "item_id",
        "acceptance_item_id",
        "acceptance_item_ids",
        "run_id",
        "plan_id",
        "task_id",
        "project_id",
        "issue_id",
        "report_id",
        "signature",
        "file",
        "path",
        "ref",
    }
)

_CODE_META_KEYS: frozenset[str] = frozenset(
    {"verify_method", "script_content", "script_name"}
)

_AUTH_HEADER_LINE_RE = re.compile(
    r"(?im)((?:^|[\r\n])\s*(?:Authorization|Cookie|Set-Cookie|X-API-Key|X-Auth-Token|Proxy-Authorization|X-Token|Token)\s*:\s*[^\r\n]+)"
)
_URL_USERINFO_RE = re.compile(
    r"(?i)([A-Za-z][A-Za-z0-9+.-]*://[^\s:@/]+:)([^\s@/]+)(@)"
)
_KV_SECRET_PREFIX_RE = (
    r"""(["']?[A-Za-z0-9_.-]*(?:password|passwd|pwd|passphrase|secret|token|"""
    r"""api_key|apikey|access_key|auth|credential)[A-Za-z0-9_.-]*["']?\s*[:=]\s*["']?)"""
)


def _is_strong_secret(secret: str) -> bool:
    """高熵或长凭据全局脱敏；低熵短密码（如 test/1234）仅在认证上下文打码。"""
    if not secret:
        return False
    if secret.startswith(_STRONG_SECRET_PREFIXES):
        return True
    if len(secret) >= 12:
        return True
    if len(secret) >= 8:
        has_alpha = any(ch.isalpha() for ch in secret)
        has_digit = any(ch.isdigit() for ch in secret)
        has_symbol = any(not ch.isalnum() for ch in secret)
        return (has_alpha and has_digit) or (has_symbol and (has_alpha or has_digit))
    return False


def _redact_short_in_auth_context(text: str, short_secrets: list[str]) -> str:
    """仅在 URL 认证段、认证 Header 或敏感 KV 字段中替掉低熵短密码，避免误伤常规文本/ID。"""
    if not short_secrets:
        return text
    result = text

    def _mask_header(match: re.Match[str]) -> str:
        line = match.group(1)
        for sec in short_secrets:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(sec)}(?![A-Za-z0-9_])"
            )
            line = pattern.sub("***", line)
        return line

    def _mask_userinfo(match: re.Match[str]) -> str:
        pwd = match.group(2)
        for sec in short_secrets:
            if pwd == sec or sec in pwd:
                pwd = pwd.replace(sec, "***")
        return f"{match.group(1)}{pwd}{match.group(3)}"

    result = _AUTH_HEADER_LINE_RE.sub(_mask_header, result)
    result = _URL_USERINFO_RE.sub(_mask_userinfo, result)
    for sec in short_secrets:
        kv_re = re.compile(
            rf"(?i){_KV_SECRET_PREFIX_RE}{re.escape(sec)}(?=$|[\s\"',;&\r\n}}\]])"
        )
        result = kv_re.sub(r"\1***", result)
    return result


def _redact_with_mode(text: str, secrets: list[str], *, strong_only: bool = False) -> str:
    result = text
    ordered = sorted({secret for secret in secrets if secret}, key=len, reverse=True)
    strong = [s for s in ordered if _is_strong_secret(s)]
    short = [] if strong_only else [s for s in ordered if len(s) >= 4 and not _is_strong_secret(s)]
    for secret in strong:
        result = result.replace(secret, "***")
    if short:
        result = _redact_short_in_auth_context(result, short)
    for pattern in _SECRET_VALUE_PATTERNS:
        result = pattern.sub("***", result)
    return result


def redact(text: str, secrets: list[str]) -> str:
    return _redact_with_mode(text, secrets, strong_only=False)


_BINARY_PREFIXES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"PK\x03\x04",
    b"\x1a\x45\xdf\xa3",
    b"RIFF",
    b"%PDF-",
)


def redact_bytes(data: bytes, secrets: list[str]) -> bytes:
    """Redact known secrets and token-like text; binary media is returned unchanged."""
    if not data or data.startswith(_BINARY_PREFIXES):
        return data
    for encoding in ("utf-8", "utf-16", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            # 按 NUL 分段分别脱敏，封堵在日志流中插入 \x00 绕过文本脱敏的漏洞。
            redacted = "\x00".join(redact(part, secrets) for part in text.split("\x00"))
        else:
            redacted = redact(text, secrets)
        if redacted == text:
            return data
        try:
            return redacted.encode(encoding)
        except UnicodeEncodeError:
            continue
    return data


def redact_tree(
    value: Any,
    secrets: list[str],
    *,
    _parent_key: str = "",
) -> Any:
    """Recursively redact string leaves before a JSON record is written."""
    if isinstance(value, dict):
        return {
            key: redact_tree(item, secrets, _parent_key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_tree(item, secrets, _parent_key=_parent_key) for item in value]
    if isinstance(value, str):
        key_lower = _parent_key.strip().lower()
        if key_lower in _STRUCTURAL_META_KEYS:
            return _redact_with_mode(value, secrets, strong_only=True)
        if _parent_key and is_secret_field(_parent_key):
            # 在已知秘密字段名上下文中，短密码同样直接掩码。
            result = _redact_with_mode(value, secrets, strong_only=False)
            for secret in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
                result = result.replace(secret, "***")
            return result
        if key_lower in _CODE_META_KEYS and key_lower == "script_name":
            return _redact_with_mode(value, secrets, strong_only=True)
        return redact(value, secrets)
    return value


_SCRIPT_ASSERTION_MARKERS = (
    "assert ",
    "assert(",
    "pytest.raises",
    "self.assert",
    "expect(",
    ".to_be(",
    ".to_have",
    "should(",
    "tobe(",
    "tothrow(",
    "toequal(",
)


def script_has_assertion(content: str) -> bool:
    """仅当生成脚本包含实际产品断言时，才视为有效验证证据。"""
    text = content.lower()
    return any(marker in text for marker in _SCRIPT_ASSERTION_MARKERS)


def _is_trivial_assert(node: ast.Assert) -> bool:
    """识别恒真或同值比较的退化断言。"""
    test = node.test
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and len(test.comparators) == 1
        and isinstance(test.ops[0], (ast.Eq, ast.Is))
    ):
        return ast.dump(test.left) == ast.dump(test.comparators[0])
    return False


def _assertion_profile(tree: ast.AST) -> tuple[int, int, int, set[int], bool]:
    """提取 Python 测试脚本中的断言数量、raises 次数、精确相等数与错误码集合。"""
    assert_count = 0
    raises_count = 0
    eq_count = 0
    error_in_codes: set[int] = set()
    has_trivial = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            assert_count += 1
            if _is_trivial_assert(node):
                has_trivial = True
            for inner in ast.walk(node.test):
                if not isinstance(inner, ast.Compare):
                    continue
                for op, comp in zip(inner.ops, inner.comparators, strict=False):
                    if isinstance(op, ast.Eq):
                        eq_count += 1
                    elif isinstance(op, ast.In) and isinstance(
                        comp, (ast.List, ast.Tuple, ast.Set)
                    ):
                        for el in comp.elts:
                            if (
                                isinstance(el, ast.Constant)
                                and isinstance(el.value, int)
                                and not isinstance(el.value, bool)
                                and el.value >= 400
                            ):
                                error_in_codes.add(el.value)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr == "raises":
                    raises_count += 1
                    assert_count += 1
                elif func.attr.startswith("assert"):
                    assert_count += 1
            elif isinstance(func, ast.Name) and (
                func.id.startswith("assert_") or func.id == "expect"
            ):
                assert_count += 1
    return assert_count, raises_count, eq_count, error_in_codes, has_trivial


def assertions_preserved(original: str, repaired: str) -> bool:
    """校验修复后的 Python 脚本未删减、退化或放宽原有断言强度。"""
    if not original.strip():
        return script_has_assertion(repaired)
    try:
        orig_tree = ast.parse(original)
    except SyntaxError:
        return script_has_assertion(repaired)
    try:
        rep_tree = ast.parse(repaired)
    except SyntaxError:
        return False
    o_asserts, o_raises, o_eq, o_codes, _ = _assertion_profile(orig_tree)
    r_asserts, r_raises, r_eq, r_codes, r_trivial = _assertion_profile(rep_tree)
    if r_trivial or r_asserts == 0:
        return False
    if r_asserts < o_asserts or r_raises < o_raises or r_eq < o_eq:
        return False
    return not (r_codes - o_codes)
