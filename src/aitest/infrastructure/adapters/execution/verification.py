"""同业务对象独立只读核验。

每一次核验都重新打开文件句柄（独立只读路径），流式重算 SHA256 与
期望值比对；不使用调用方的缓存句柄、内存副本或对象存储的自身读路径。
核验事实只陈述重新读取到的结果，不产生业务结论。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from aitest.contracts.verification import (
    VerificationFact,
    VerificationState,
)

_HASH_BLOCK = 64 * 1024


class IndependentFileVerifier:
    """VerificationPort 的本地只读实现。"""

    method = "fresh_file_read"

    def verify(self, path: Path, expected_sha256: str) -> VerificationFact:
        target = Path(path)
        if not target.exists():
            return VerificationFact(
                state=VerificationState.MISSING,
                method=self.method,
                sha256="",
                size=0,
                detail=f"对象不存在: {target}",
            )
        digest = hashlib.sha256()
        size = 0
        try:
            # 全新独立句柄：不接受任何已打开句柄或内存缓冲。
            with target.open("rb") as handle:
                while block := handle.read(_HASH_BLOCK):
                    digest.update(block)
                    size += len(block)
        except OSError as error:
            return VerificationFact(
                state=VerificationState.UNREADABLE,
                method=self.method,
                sha256="",
                size=0,
                detail=str(error),
            )
        observed = digest.hexdigest()
        if observed != expected_sha256:
            return VerificationFact(
                state=VerificationState.MISMATCH,
                method=self.method,
                sha256=observed,
                size=size,
                detail="重算摘要与期望不一致",
            )
        return VerificationFact(
            state=VerificationState.VERIFIED,
            method=self.method,
            sha256=observed,
            size=size,
        )


__all__ = ["IndependentFileVerifier"]
