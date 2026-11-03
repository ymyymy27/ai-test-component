# 修改日志：A 包 create_api(handlers=...) 过期警告消除

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-09-30 |
| 修改文件 | `src/aitest/bootstrap.py` |
| 关键词 | A包create_api过期警告消除 |
| 触发原因 | `tests/unit/test_a_transactions_and_queries.py` 调用 `create_api(handlers={"query": handler})` 时触发 `DeprecationWarning: create_api(handlers=...) 是过时兼容接口...`，单元测试输出出现 1 条过期警告 |
| 分支基线 | `develop` |
| 遵循规范 | 根目录 `AGENTS.md` |

## 1. 问题根因

`create_api(workspace_root, handlers)` 的 `handlers` 参数设计为 B/C/D 上层包禁用的过时兼容接口，在 `handlers is not None` 时通过 `warnings.warn(..., DeprecationWarning, stacklevel=2)` 发出运行时警告。

但 A 包自身的单元测试（`test_request_id_is_deduplicated_and_cannot_replace_intent_id`）需要注入本地 handler 来验证请求去重与冲突逻辑：

- `register_use_cases` 只接受 `package ∈ {B, C, D}`（`UseCaseRegistry.register` 第 63-64 行），A 包无法通过该入口注册；
- 因此 A 包测试只能通过 `create_api(handlers=...)` 注入处理器，这是该参数的合法内部用途；
- 运行时警告对所有调用者无差别触发，导致 A 包合法测试用法也产生警告。

## 2. 修改内容

移除 `create_api` 中针对 `handlers` 参数的运行时 `warnings.warn` 调用，并删除随之不再使用的 `import warnings`。`handlers` 参数本身及其功能完整保留，签名不变。

```diff
- import warnings
  ...
  def create_api(
      workspace_root: Path | None = None,
      handlers: Mapping[str, Handler] | None = None,
  ) -> LocalAPI:
      ...
      if handlers is not None:
-         warnings.warn(
-             "create_api(handlers=...) 是过时兼容接口：...",
-             DeprecationWarning,
-             stacklevel=2,
-         )
          return LocalAPI(instance_id=str(uuid4()), handlers=dict(handlers))
```

docstring 中对 B/C/D 禁止使用 `handlers` 的说明予以保留，作为文档约束。

## 3. 约束符合性

| 约束 | 结果 |
| --- | --- |
| 不修改 A 包对外接口签名 | ✅ `create_api` 签名（`workspace_root`、`handlers`）完全不变 |
| 不改动 A-C 对接代码 | ✅ `ports.py`、`contract.md` 未触碰 |
| 不新增业务逻辑 | ✅ 仅移除运行时警告，`handlers` 分支行为不变 |
| 测试文件不改动 | ✅ `test_a_transactions_and_queries.py` 零改动 |

## 4. 验证

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| 目标测试（警告升级为错误） | `python -m pytest tests/unit/test_a_transactions_and_queries.py -v -W error::DeprecationWarning` | ✅ 8 passed，无 DeprecationWarning |
| 全量单测 | `python -m pytest tests/unit/ -v` | ✅ 574 passed in 25.93s，无警告 |
| ruff | `python -m ruff check src/aitest/bootstrap.py` | ✅ All checks passed! |

## 5. 不改动项汇总

- `src/aitest/application/ports.py`（A 包对外端口）
- A-C 接口契约文档
- 所有测试文件
- `create_api` 函数签名与 `handlers` 分支功能
