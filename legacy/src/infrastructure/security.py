"""落盘前凭据安全底线（A-09）。

合同（一期架构02第13节 采集安全与原始证据语义）：

- 已解析凭据的**精确值**、策略明列模式与标准认证字段必须在**任何字节
  落盘之前**过滤；过滤前字节不得进入对象、spool、诊断、备份或业务记录；
- 流式采集的跨块匹配由未决尾部（只留在受控内存）保证，异常终止时
  未确认安全的尾部不落盘；
- 摘要、offset、length 只对过滤后字节计算；
- 无法安全处理的材料阻止保存（:class:`UnsafeMaterialError`），由调用方
  登记采集缺口，不把未过滤输出降级写盘。

保证范围仅限**已登记的已知凭据值**与 contracts.redaction 中的明列模式，
不承诺识别任意未知/变形秘密。注册表只存在于受控进程内存，从不序列化。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from typing import IO, Any

from aitest.contracts.redaction import SENSITIVE_KEYS, scrub_secret_text
from aitest.domain.json_material import decode_json

_REDACTION = "[REDACTED]"

#: 切点检查尾部窗口：取最后一个空白分隔段（指示词 + token 同段，
#: Bearer/sk-/ghp_ 等均以空白分隔）；超过窗口仍无分隔符的极端无空白
#: 流按"无法证明安全"整段保留，封口时统一过滤。
_PARTIAL_WINDOW = 4096

#: 已知值最短片段：切点回退时只有长度达到该值的"疑似凭据前缀"才保留。
_MIN_PARTIAL_PREFIX = 1


#: 切点处"疑似密钥半截"形态：指示词、键值对、Bearer/Basic、token 前缀
#: 可能正停在切点上（含指示词被切成 1-2 个字符、值刚开头）。命中则切点
#: 回退到整段半截起点，把它留在内存，下一块重新拼接后再判定。
def _flex(word: str) -> str:
    """把指示词中的 ``_`` 放宽为 ``[_-]``。"""
    return word.replace("_", "[_-]")


def _stems(words: tuple[str, ...], *, min_len: int = 2) -> list[str]:
    stems: set[str] = set()
    for word in words:
        for size in range(len(word), min_len - 1, -1):
            stems.add(_flex(word[:size]))
    return sorted(stems, key=len, reverse=True)


_KEY_WORDS = (
    "authorization",
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "api_key",
    "apikey",
)
_BEARER_WORDS = ("bearer", "basic")
#: ghp_/gho_/... 家族与 sk-/eyJ 的前缀（含 1 字符，宁可多留一个字节）。
_TOKEN_STEMS = (
    "s",
    "sk",
    "sk-",
    "g",
    "gh",
    "ghp",
    "ghp_",
    "e",
    "ey",
    "eyJ",
)
_KEY_ALT = "|".join(_stems(_KEY_WORDS))
_BEARER_ALT = "|".join(_stems(_BEARER_WORDS, min_len=1))
_TOKEN_ALT = "|".join(sorted(_TOKEN_STEMS, key=len, reverse=True))
#: 指示词前必须是字符串起点或非标识符字符，避免 "asking/status/hello"
#: 等普通词的后缀被误判为 sk-/s、eyJ/e 前缀。
_BOUNDARY = r"(?:^|(?<=[^A-Za-z0-9_]))"
_PARTIAL_SECRET_TAIL = re.compile(
    r"(?is)("
    rf"{_BOUNDARY}(?:{_KEY_ALT})\s*[:=]?\s*\S*"
    rf"|{_BOUNDARY}(?:{_BEARER_ALT})(?:\s+\S*)?"
    rf"|{_BOUNDARY}(?:{_TOKEN_ALT})[A-Za-z0-9_.\-]*(?:\.[A-Za-z0-9_.-]*){{0,2}}"
    r")\Z"
)

#: ASCII 空白分隔字节。
_WHITESPACE = frozenset(b" \t\r\n\v\f")


class UnsafeMaterialError(RuntimeError):
    """材料含未过滤凭据且当前路径无法安全替换；禁止落盘，需登记缺口。"""


class KnownSecretRegistry:
    """进程内已知凭据精确值登记表（线程安全，只存内存，绝不序列化）。"""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str) -> None:
        """登记非空精确值；凭据长度不能作为允许泄露的依据。"""
        if not isinstance(value, str) or not value:
            return
        with self._lock:
            self._values.add(value)

    def text_values(self) -> tuple[str, ...]:
        """已知值（长→短），长值优先替换避免短值截断长值。"""
        with self._lock:
            return tuple(sorted(self._values, key=len, reverse=True))

    def byte_values(self) -> tuple[bytes, ...]:
        return tuple(value.encode("utf-8") for value in self.text_values())

    def longest_length(self) -> int:
        values = self.text_values()
        return len(values[0]) if values else 0

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._values)

    def clear(self) -> None:
        """清空登记（测试隔离用；生产进程生命周期内不调用）。"""
        with self._lock:
            self._values.clear()


_GLOBAL_REGISTRY = KnownSecretRegistry()


def known_secrets() -> KnownSecretRegistry:
    """进程级登记：SecretPort 每解析出一个凭据即登记，供全部落盘路径共用。"""
    return _GLOBAL_REGISTRY


def scrub_text(
    text: str,
    registry: KnownSecretRegistry | None = None,
) -> tuple[str, bool]:
    """文本过滤：先精确值（长→短），再走明列模式。"""
    target = registry if registry is not None else _GLOBAL_REGISTRY
    values = target.text_values()
    changed = False
    if values:
        # 一次替换，避免短凭据再次匹配刚插入的脱敏标记。
        text, count = re.subn(
            "|".join(re.escape(value) for value in values), lambda _match: _REDACTION, text
        )
        changed = count > 0
    redacted, pattern_changed = scrub_secret_text(text)
    return redacted, changed or pattern_changed


def guard_bytes(
    content: bytes,
    registry: KnownSecretRegistry | None = None,
) -> tuple[bytes, bool]:
    """任意字节的落盘前过滤。

    精确值替换对二进制同样执行（字节级）；明列模式仅在字节为合法 UTF-8
    文本时适用，二进制证据不做猜测性改写。返回（过滤后字节，是否变更）。
    """
    target = registry if registry is not None else _GLOBAL_REGISTRY
    needles = target.byte_values()
    changed = False
    if needles:
        content, count = re.subn(
            b"|".join(re.escape(value) for value in needles),
            lambda _match: _REDACTION.encode("utf-8"),
            content,
        )
        changed = count > 0
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content, changed
    # 精确值已经处理，只对安全字节执行模式过滤。
    redacted, text_changed = scrub_secret_text(text)
    if text_changed:
        return redacted.encode("utf-8"), True
    return content, changed


def guard_value(
    value: Any,
    registry: KnownSecretRegistry | None = None,
) -> tuple[Any, bool]:
    """递归过滤业务结构（UOW payload 底线）：敏感键整体替换 + 值内凭据。"""
    target = registry if registry is not None else _GLOBAL_REGISTRY
    return _guard(value, target)


def _guard(value: Any, registry: KnownSecretRegistry) -> tuple[Any, bool]:
    if isinstance(value, Mapping):
        changed_any = False
        result: dict[str, Any] = {}
        for key, item in value.items():
            safe_key, key_changed = scrub_text(str(key), registry)
            if safe_key in result:
                raise UnsafeMaterialError("credential filtering creates ambiguous field names")
            changed_any = changed_any or key_changed
            normalized = str(key).lower().replace("-", "_")
            if {str(key).lower(), normalized} & SENSITIVE_KEYS:
                result[safe_key] = _REDACTION
                changed_any = changed_any or item != _REDACTION
                continue
            guarded, changed = _guard(item, registry)
            result[safe_key] = guarded
            changed_any = changed_any or changed
        return result, changed_any
    if isinstance(value, (list, tuple)):
        changed_any = False
        items: list[Any] = []
        for item in value:
            guarded, changed = _guard(item, registry)
            items.append(guarded)
            changed_any = changed_any or changed
        return items, changed_any
    if isinstance(value, str):
        return scrub_text(value, registry)
    return value, False


#: 疑似结构化材料的起始字符；只有这两种前缀才按 JSON 严格解码。
_JSON_STRUCTURE_PREFIXES = ("{", "[")


def guard_json_text(
    text: str,
    registry: KnownSecretRegistry | None = None,
) -> tuple[str, bool] | None:
    """疑似结构化文本的**严格解码 + 解码后递归过滤**；非结构化返回 ``None``。

    落盘前/出站前的已知凭据过滤必须覆盖**编码变体**（一期架构02第13节）：
    JSON 键或值中的 ``\\uXXXX`` 转义会让同一凭据以不同字节出现，因此不能只在
    原文本上匹配。本函数：

    - 只有 ``{``/``[`` 开头的文本按结构化处理，其余返回 ``None``，由调用方
      走纯文本底线；
    - 疑似结构化但无法严格解码（重复键、NaN/Infinity/溢出数值、孤立代理字符、
      破损结构、深度耗尽），或过滤后键名碰撞时抛
      :class:`UnsafeMaterialError`：登记材料缺口，不得当作完整投影；
    - 在**解码后的键与值**上应用 :func:`guard_value` 的同一底线（已登记精确值、
      敏感键、明列模式），命中即重新序列化为安全文本并返回 ``(安全文本, True)``；
    - 未命中时返回 ``(原文本, False)``，保留原始字节（含原转义、原空白），
      不对合法未变材料制造新摘要。
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in _JSON_STRUCTURE_PREFIXES:
        return None
    target = registry if registry is not None else _GLOBAL_REGISTRY
    try:
        parsed = decode_json(text)
    except (ValueError, RecursionError) as error:
        raise UnsafeMaterialError(
            "疑似结构化材料无法严格解码，登记材料缺口"
        ) from error
    if not isinstance(parsed, (dict, list)):
        return None
    try:
        guarded, changed = _guard(parsed, target)
        serialized = json.dumps(guarded, ensure_ascii=False) if changed else text
    except (RecursionError, TypeError, ValueError, UnsafeMaterialError) as error:
        raise UnsafeMaterialError(
            "疑似结构化材料无法安全过滤（键名碰撞或结构超限），登记材料缺口"
        ) from error
    # 结构化过滤后再走一次文本底线：已登记值可能出现在 JSON 数字等非字符串位置。
    safe, text_changed = scrub_text(serialized, target)
    return safe, changed or text_changed


#: 未决尾部的最小保留；默认 0：无已登记凭据时保留完全由尾部模式检查
#: 决定（正常文本零扣留，疑似半截 token 回退），有已登记凭据时至少保留
#: 最长凭据长度，保证精确值不可能跨块。
_MIN_CARRY = 0


def _utf8_cut_point(data: bytes, cut: int) -> int:
    """把 cut 回退到 UTF-8 字符边界：落在多字节字符中部时整字符留给尾部。"""
    while 0 < cut < len(data) and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return cut


class StreamSecretFilter:
    """流式落盘前过滤器：跨块凭据由内存未决尾部覆盖。

    ``feed`` 返回允许写盘的**已过滤**字节。切点逐字节回退直到前缀尾部
    不再包含任何"疑似密钥半截"：(1) Bearer/Basic/键值指示词及其刚开头的
    值；(2) ``sk-``/``ghp_``/JWT 前缀；(3) 非空的已知凭据前缀；
    (4) 多字节 UTF-8 字符中部。因此完整凭据不可能跨相邻两次 feed 漏出。
    未决尾部只在受控内存，``abort`` 丢弃，``flush`` 在封口时统一过滤。
    """

    def __init__(self, registry: KnownSecretRegistry | None = None) -> None:
        self._registry = registry if registry is not None else _GLOBAL_REGISTRY
        self._carry = b""
        self._changed = False

    @property
    def changed(self) -> bool:
        """Whether an emitted prefix or flushed tail has required filtering."""
        return self._changed

    @property
    def pending_bytes(self) -> int:
        """Unresolved bytes held in memory, so collectors can enforce their own budget."""
        return len(self._carry)

    def _carry_limit(self) -> int:
        return max(_MIN_CARRY, self._registry.longest_length())

    def _safe_cut(self, combined: bytes) -> int:
        cut = len(combined) - min(len(combined), self._carry_limit())
        needles = self._registry.byte_values()
        while cut > 0:
            cut = _utf8_cut_point(combined, cut)
            if cut == 0:
                break
            # 模式半截（指示词/token 前缀）与已知凭据前缀可能同时命中，
            # 取更早的回退位置：已知值本身可能以普通字符开头（如 zz-…），
            # 只回退到内部"secret"子串会把已知值切成两半而漏匹配。
            back_to = -1
            window_start = _utf8_cut_point(combined, max(0, cut - _PARTIAL_WINDOW))
            window = combined[window_start:cut].decode("utf-8", errors="ignore")
            match = _PARTIAL_SECRET_TAIL.search(window)
            if match is not None:
                back_to = window_start + len(window[: match.start(1)].encode("utf-8"))
            for needle in needles:
                limit = min(len(needle), cut)
                for length in range(limit, _MIN_PARTIAL_PREFIX - 1, -1):
                    if combined[cut - length : cut] == needle[:length]:
                        back_to = min(back_to, cut - length) if back_to >= 0 else cut - length
                        break
            if back_to >= 0:
                cut = back_to
                continue
            # 超长无空白段（>窗口）无法排除巨型 token 跨块：回退到上一个
            # 空白分隔符；整段流都无空白则留给封口统一判定。
            if cut - window_start >= _PARTIAL_WINDOW and not any(
                byte in _WHITESPACE for byte in combined[window_start:cut]
            ):
                previous = cut - _PARTIAL_WINDOW - 1
                while previous >= 0 and combined[previous] not in _WHITESPACE:
                    previous -= 1
                cut = previous + 1 if previous >= 0 else 0
                continue
            break
        return cut

    def feed(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""
        combined = self._carry + chunk
        cut = self._safe_cut(combined)
        prefix, self._carry = combined[:cut], combined[cut:]
        if not prefix:
            return b""
        guarded, changed = guard_bytes(prefix, self._registry)
        self._changed = self._changed or changed
        return guarded

    def flush(self) -> bytes:
        if not self._carry:
            return b""
        guarded, changed = guard_bytes(self._carry, self._registry)
        self._changed = self._changed or changed
        self._carry = b""
        return guarded

    def abort(self) -> None:
        """异常终止：未确认安全的尾部绝不落盘。"""
        self._carry = b""


def copy_unchanged_safe_bytes(
    source: IO[bytes],
    destination: IO[bytes] | None = None,
    *,
    registry: KnownSecretRegistry | None = None,
    block_size: int = 64 * 1024,
    max_pending_bytes: int = 1024 * 1024,
) -> tuple[str, int]:
    """Scan/copy immutable material without changing its claimed byte identity.

    Only filtered emissions may reach the destination. Any required redaction
    rejects the copy; the caller must discard unpublished temporary material.
    This protects source snapshots and backup/restore with the same policy.
    """
    if block_size < 1 or max_pending_bytes < 1:
        raise ValueError("safe copy bounds must be positive")
    stream = StreamSecretFilter(registry)
    digest, size = hashlib.sha256(), 0
    try:
        while chunk := source.read(block_size):
            safe = stream.feed(chunk)
            if stream.pending_bytes > max_pending_bytes:
                raise UnsafeMaterialError("未决敏感片段超出安全内存范围，拒绝复制")
            if stream.changed:
                raise UnsafeMaterialError("材料含敏感材料，拒绝改变原始字节身份")
            if destination is not None:
                destination.write(safe)
            digest.update(safe)
            size += len(safe)
        tail = stream.flush()
        if stream.changed:
            raise UnsafeMaterialError("材料含敏感材料，拒绝改变原始字节身份")
        if destination is not None:
            destination.write(tail)
        digest.update(tail)
        size += len(tail)
        return digest.hexdigest(), size
    finally:
        stream.abort()


__all__ = [
    "KnownSecretRegistry",
    "StreamSecretFilter",
    "UnsafeMaterialError",
    "guard_bytes",
    "copy_unchanged_safe_bytes",
    "guard_value",
    "known_secrets",
    "scrub_text",
]
