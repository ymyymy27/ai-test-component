"""OS 按用途凭据解析；无明文工作空间回退。

只允许两类来源：

- 进程环境变量，但**引用名到环境变量名的映射必须显式登记**，不遍历环境；
- Windows 凭据管理器（Generic 凭据），通过 ctypes ``CredReadW`` 读取，
  非 Windows 或读取失败时报告不可用。

凭据绝不写入工作空间文件、日志、检查点或命令行；解析结果只在受控
内存中通过 :class:`ResolvedSecret` 显式 reveal。
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from ctypes import wintypes
from typing import Protocol, runtime_checkable

from aitest.contracts.secrets import KNOWN_PURPOSES, ResolvedSecret

from .security import known_secrets

#: 凭据目标名允许的片段：字母数字、._-/；不允许反斜杠、空白与控制字符，
#: 避免凭据管理器目标注入与 str.format 风格的模板注入。
_TARGET_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")
_MAX_TARGET_LENGTH = 512


class SecretUnavailable(RuntimeError):
    """用途未登记、引用不可解析或所有来源均不可用。"""


@runtime_checkable
class SecretProvider(Protocol):
    """单个凭据来源。"""

    name: str

    @property
    def available(self) -> bool: ...

    def resolve(self, *, purpose: str, reference: str) -> str: ...


class EnvironmentSecretProvider:
    """从显式登记的环境变量名解析，不读取未登记变量。"""

    name = "environment"

    def __init__(self, mapping: dict[tuple[str, str], str]) -> None:
        # (purpose, reference) -> 环境变量名
        self._mapping = dict(mapping)

    @property
    def available(self) -> bool:
        return True

    def resolve(self, *, purpose: str, reference: str) -> str:
        env_name = self._mapping.get((purpose, reference))
        if env_name is None:
            raise SecretUnavailable(f"环境变量未登记: {purpose}/{reference}")
        value = os.environ.get(env_name)
        if value is None:
            raise SecretUnavailable(f"环境变量不存在: {env_name}")
        return value


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialProvider:
    """从 Windows 凭据管理器读取 Generic 凭据；其他系统不可用。"""

    name = "windows_credential_manager"

    _CRED_TYPE_GENERIC = 1
    _ERROR_NOT_FOUND = 1168
    _TARGET_PREFIX = "aitest/"

    def __init__(self) -> None:
        self._supported = sys.platform.startswith("win")

    @property
    def available(self) -> bool:
        return self._supported

    @classmethod
    def _build_target(cls, purpose: str, reference: str) -> str:
        """显式拼接目标名并校验片段，禁止模板注入/控制字符/超长目标。"""
        for component in (purpose, reference):
            if not component or not _TARGET_COMPONENT_RE.fullmatch(component):
                raise SecretUnavailable(
                    "凭据用途或引用含非法字符（仅允许字母数字、._、-、/）"
                )
        target = f"{cls._TARGET_PREFIX}{purpose}/{reference}"
        if len(target) > _MAX_TARGET_LENGTH:
            raise SecretUnavailable("凭据目标名超长")
        return target

    def resolve(self, *, purpose: str, reference: str) -> str:
        if not self._supported:
            raise SecretUnavailable("Windows 凭据管理器在当前平台不可用")
        target = self._build_target(purpose, reference)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        # 显式声明原型：BOOL 为 32 位，指针参数不能依赖 ctypes 的默认推导，
        # 否则在 64 位解释器上存在高位截断的静态风险。
        advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CredentialW)),
        ]
        advapi32.CredReadW.restype = wintypes.BOOL
        advapi32.CredFree.argtypes = [ctypes.c_void_p]
        advapi32.CredFree.restype = None
        credential_pointer = ctypes.POINTER(_CredentialW)()
        read = advapi32.CredReadW(
            ctypes.c_wchar_p(target),
            wintypes.DWORD(self._CRED_TYPE_GENERIC),
            wintypes.DWORD(0),
            ctypes.byref(credential_pointer),
        )
        if not read:
            error_code = ctypes.get_last_error()
            if error_code == self._ERROR_NOT_FOUND:
                raise SecretUnavailable(f"凭据管理器中不存在: {target}")
            raise SecretUnavailable(
                f"凭据管理器读取失败: {target} (winerror={error_code})"
            )
        try:
            credential = credential_pointer.contents
            if not credential.CredentialBlobSize or not credential.CredentialBlob:
                raise SecretUnavailable(f"凭据为空: {target}")
            raw = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SecretUnavailable(
                    f"凭据字节不是合法 UTF-8 文本: {target}"
                ) from error
        finally:
            advapi32.CredFree(ctypes.cast(credential_pointer, ctypes.c_void_p))


class SecretManager:
    """按用途聚合来源的 SecretPort 实现；无文件回退。"""

    def __init__(self, providers: tuple[SecretProvider, ...] | list[SecretProvider]) -> None:
        self._providers = tuple(providers)

    @classmethod
    def default(cls) -> SecretManager:
        """一期默认来源：环境变量 + Windows 凭据管理器。"""
        return cls(
            (
                EnvironmentSecretProvider({}),
                WindowsCredentialProvider(),
            )
        )

    def register_env_mapping(self, mapping: dict[tuple[str, str], str]) -> None:
        """为环境变量来源补充 (purpose, reference) -> env name 映射。"""
        provider = self._find_environment_provider()
        provider._mapping.update(mapping)

    def resolve(self, reference: str, *, purpose: str) -> ResolvedSecret:
        """按用途解析引用；依次尝试可用来源，不做工作空间文件回退。"""
        if purpose not in KNOWN_PURPOSES:
            raise SecretUnavailable(f"未知凭据用途: {purpose}")
        failures: list[str] = []
        for provider in self._providers:
            if not provider.available:
                continue
            try:
                value = provider.resolve(purpose=purpose, reference=reference)
            except SecretUnavailable as error:
                failures.append(f"{provider.name}: {error}")
                continue
            # A-09：凭据一进入受控进程即登记精确值，objects/spool/业务记录
            # 的落盘前底线据此替换；注册表只存在于内存，绝不序列化。
            known_secrets().register(value)
            return ResolvedSecret(
                purpose=purpose, reference=reference, source=provider.name, _value=value
            )
        raise SecretUnavailable(
            f"无法解析 {purpose}/{reference}；尝试: {failures}"
        )

    def has_secret(self, reference: str, *, purpose: str) -> bool:
        """能力探测：不返回值，只报告能否解析。"""
        try:
            secret = self.resolve(reference, purpose=purpose)
        except SecretUnavailable:
            return False
        secret.clear()
        return True

    # ----- 内部 -------------------------------------------------------

    def _find_environment_provider(self) -> EnvironmentSecretProvider:
        for provider in self._providers:
            if isinstance(provider, EnvironmentSecretProvider):
                return provider
        raise SecretUnavailable("未配置环境变量来源")


__all__ = [
    "EnvironmentSecretProvider",
    "SecretManager",
    "SecretProvider",
    "SecretUnavailable",
    "WindowsCredentialProvider",
]
