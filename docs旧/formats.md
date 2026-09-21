# 数据格式

已实现的项目记录包含：

- `schema_version`
- 稳定业务编号
- `revision`
当前骨架实现 `aatp.project/1.0`。操作者和时间审计字段将在身份桥接落地时加入；完整 Schema 将按领域对象逐项加入 `src/ai_test/resources/schemas`。

## 任务记录

当前实现 `aatp.task/1.0`，记录任务目标、范围、输入输出、前置条件、负责人、验收人及一个或多个验收项。验收项至少包含稳定编号和可观察结果，`required=false` 表示非必测项。任务记录同样使用 `revision` 进行并发控制。

## Delivery Record

The current implementation uses `aatp.delivery/1.0`. A delivery is linked to one task and records the submitted version, completed and incomplete items, changed modules, API changes, run method, test data, dependencies, mocks, known issues, self-test evidence, submitter, and revision.

## Chunked Record Index

Workspace state uses `aatp.workspace-state/2.0`. Record pointers are stored in bounded shards under `indexes/<kind>/<shard>/`. A write updates only the affected record, one index chunk, and the state entry. Legacy `aatp.workspace-state/1.0` workspaces are migrated automatically on first use.
