# 待裁定事项

**当前无待裁定事项**（2026-10-09 建卡的 DEC-010/011/012 已由负责人授权代决并归档，见下表）。

| 编号 | 事项 | 提出方 | 受影响方 | 裁定结论 / 实现状态 |
| --- | --- | --- | --- | --- |
| [DEC-010](../归档/裁定/DEC-010-来源核验期望摘要字段命名与口径.md) | 来源核验期望侧字段命名与口径 | C | A、B、C、D | **方案 A**：不得复用 `BC-001` 的 `source_binding_digest`；`CD-001` 字段改名（破坏性，升版本＋重出 Schema）**未完成，已转记下一动作** |
| [DEC-011](../归档/裁定/DEC-011-AB-001四项start侧来源绑定语义.md) | `AB-001` 1.35 四项 start 侧语义 | A | A、B、C | **确认 1A/2A/3A/4A**；已实现（31 项回归）、未接线（前提：可信动作解析器） |
| [DEC-012](../归档/裁定/DEC-012-start来源失败错误码与原因标识登记.md) | start 前四类失败的对外错误码与原因标识 | A | A、C、D | **方案 A**：`SOURCE_BINDING_UNVERIFIED` ＋ 四类结构化 `reason` ＋ 既有 `source_unverified` 缺口；已登记，接线时使用 |

DEC-007/008/009 已于 2026-10-03 由负责人明确裁定并归档，未完成实现转入现行合同的状态与下一动作。

| 编号 | 事项 | 提出方 | 受影响方 | 状态 |
| --- | --- | --- | --- | --- |
| [DEC-007](../归档/裁定/DEC-007-规则与计划记录的修订配对.md) | `rule_draft` / `plan` 记录的「正文修订」与「仓储修订」是否必须一致 | B | A、C | **已裁定**：区分正文版本与仓储修订 |
| [DEC-008](../归档/裁定/DEC-008-验收范围标识是否冻结进PreparedRun.md) | `PreparedRun` 是否冻结验收范围的 `scope_id`（用于漂移核对） | B | C、D | **已裁定**：增加 scope_id，旧记录要求重新准备 |
| [DEC-009](../归档/裁定/DEC-009-源码快照的修订语义.md) | 源码快照的修订语义（`InputRevisions.snapshot_revision` 到底该是什么） | B | A、C、D | **已裁定**：直接比较 content_identity |

裁定后按第 7 节流程把结论回写对应合同，并把裁定记录移入 [`../归档/裁定/`](../归档/裁定/)。

## 已裁定并归档

最近三档（DEC-004／005／006）已于 2026-10-01 裁定并实现，裁定记录随同步完成的接口合同
移入 [`../归档/裁定/`](../归档/裁定/)，现行入口见 [`../README.md`](../README.md) 第 10.3 节。

| 编号 | 事项 | 裁定结论 | 实现状态 |
| --- | --- | --- | --- |
| [DEC-004](../归档/裁定/DEC-004-规则发布落盘类别.md) | `publish_rules()` 发布结果落到哪个记录类别 | **甲**：发布产生 `rule_version` 记录，`rule_draft` 只承载草稿 | **已实现**（PR #49）：`application/planning/publish.py` 的 `stage_record(aggregate_kind="rule_version")`；`tests/unit/test_publish_orchestration.py` 按 `rule_version` 读回 |
| [DEC-005](../归档/裁定/DEC-005-准备摘要的用例修订配对.md) | 准备输入摘要是否保留「用例 ID ↔ 修订」的配对 | **乙**：摘要仍只放标识；配对作为观察事实参与比对，变化即 `needs_reprepare` | **已实现**（PR #49）：`preparation.py` 的 `observed_case_revisions`（随记录落盘、可重建）+ `decide_preparation()` 比对；`tests/unit/test_prepare_run.py` 有换配对、重排归一与重建三条反例 |
| [DEC-006](../归档/裁定/DEC-006-模块源码路径字段.md) | `Module` 是否增加「模块 ↔ 源码路径」字段 | **甲**：`Module` 增可选 `source_paths`，空值表示"未登记"而非"影响全部" | **已实现**（PR #49 加字段、PR #50 落地映射）：`domain/project/context.py` 的 `source_paths`；`application/planning/regression_graph.py` 按登记路径匹配并给出 `unregistered_modules` |

新增跨合同冲突时，按上级 `README.md` 的裁定流程创建一事一档的 `DEC-*.md`；
项目文档只作为只读依据。职责和范围一旦裁定，应把未完成工作转入对应合同的实现状态与下一动作，
并在同步接口合同后将裁定记录移入 `../归档/裁定/`。

**同一事项只能有一个主责来源**：`归档/裁定/` 下的 `DEC-*.md` 是已裁定事项的唯一现行记录；
B 包自己的 `docs/文档-feix-a/B包/09-待解决问题清单.md` 只保留指向本目录或归档的索引，不重复定义。
