# 待裁定事项

**当前没有待裁定事项。** 本目录为空是正常状态，不是缺失。

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
