# 2026-09-29-A包QuerySpec查询

## 1. 本次实现概述

实现 A 包基于 `QuerySpec` 的只读索引查询机制，提供固定排序、游标分页和有界分页；索引缺失、损坏或版本不兼容时返回维护状态，不执行隐式全表扫描。

## 2. 新增/修改文件清单

- `src/aitest/infrastructure/file_store/index.py`
  - 新增 `IndexQueryResult`。
  - 实现 `FileQueryIndex.query_spec()`。
  - 保留 `query()` 兼容入口，但仍只读取维护索引。
- `docs/修改日志/LU/2026-09-29-A包QuerySpec查询.md`
  - 新增本次固定格式修改日志。

## 3. 落地的约束清单

- 使用 contracts 中已有 `QuerySpec` 类型。
- 支持 `aggregate_kind`、`record_id`、`revision` 过滤。
- 支持固定排序字段和升降序。
- 支持有界 `limit`，上限由 `QuerySpec` 的 500 约束保证。
- 支持游标分页，并返回 `next_cursor`。
- 索引不存在、损坏或版本不兼容时返回 `maintenance_required`。
- 禁止回退到记录文件或对象文件的隐式全表扫描。
- 查询接口只读，不提供业务记录修改或删除。

## 4. 重要备注

- `rebuild()` 是显式维护动作，不属于普通查询路径。
- 当前查询结果返回索引摘要字典，不负责生成业务结论。
- 尚未完成真实 Windows 文件系统的掉电、权限拒绝和索引替换故障注入验收。
- 未实现永久业务材料删除接口。

## 5. 变更摘要

- `index.py`：重写为有限索引查询实现。
- 新增日志文档：约 40 行。
