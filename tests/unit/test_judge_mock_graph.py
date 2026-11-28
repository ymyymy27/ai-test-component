"""第 2 步单测：大仓库 Mock 依赖子集采样（D5）与 code_graph 关联入口筛选。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aitest.ai_features import judge_mock


class _StubClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        del system
        self.calls.append(json.loads(user))
        return self.response


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_judge_mock_large_repo_300_plus_files_uses_dependency_subset(tmp_path: Path) -> None:
    """300+ 文件仓库中含大量无关前端组件时，按依赖子集采样精准判 real，不误触 limit_reached。"""
    _write(tmp_path, "frontend/package.json", '{"name": "web"}\n')
    for idx in range(280):
        _write(
            tmp_path,
            f"frontend/src/comp_{idx}.ts",
            f"export const label_{idx} = 'component {idx}';\n",
        )
    _write(tmp_path, "backend/pyproject.toml", "[project]\nname = 'api'\n")
    for idx in range(40):
        _write(tmp_path, f"backend/util_{idx}.py", f"def helper_{idx}(): return {idx}\n")

    # 仅 2 个业务文件与 1 个测试文件真正引用 redis
    _write(
        tmp_path,
        "backend/cache_store.py",
        "import redis\ndef get_client():\n    return redis.Redis(host='127.0.0.1', port=6379)\n",
    )
    _write(
        tmp_path,
        "backend/app.py",
        "from cache_store import get_client\ndef read_item():\n    return get_client().get('k')\n",
    )
    _write(
        tmp_path,
        "backend/tests/test_cache.py",
        "from cache_store import get_client\n"
        "def test_cache():\n    assert get_client() is not None\n",
    )

    client = _StubClient(
        {
            "dependencies": [
                {
                    "name": "redis",
                    "kind": "real",
                    "reason": "backend/cache_store.py 直接实例化真实 redis.Redis 客户端",
                    "evidence_refs": ["backend/cache_store.py"],
                }
            ]
        }
    )

    result = judge_mock(tmp_path, ["redis"], [], client=client)
    assert len(client.calls) == 1
    assert result[0]["kind"] == "real"
    assert result[0]["limit_reached"] is False
    assert "backend/cache_store.py" in result[0]["read_files"]
    # 无关前端组件不进入采样，确保总数远低于 240
    assert len(result[0]["read_files"]) < 20
    assert not any(f.startswith("frontend/src/comp_") for f in result[0]["read_files"])


def test_judge_mock_subset_still_fails_closed_when_related_subset_exceeds_budget(
    tmp_path: Path,
) -> None:
    """当实际引用目标依赖的子集本身超过 max_files 预算时，必须 fail-closed 标 unknown。"""
    for idx in range(8):
        _write(tmp_path, f"svc/mod_{idx}.py", "import redis\nr = redis.Redis()\n")

    client = _StubClient(
        {
            "dependencies": [
                {
                    "name": "redis",
                    "kind": "real",
                    "reason": "real",
                    "evidence_refs": ["svc/mod_0.py"],
                }
            ]
        }
    )
    result = judge_mock(tmp_path, ["redis"], [], client=client, max_files=4)
    assert client.calls == []
    assert result[0]["kind"] == "unknown"
    assert result[0]["limit_reached"] is True
    assert "达到读取上限" in result[0]["reason"]
