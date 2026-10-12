# 一期接口文档状态复核（2026-10-12）

文档基线 `90baba0360f378eb5feaa3a7aa8c25dc2064fed6`，取证产品源码仍为 `develop@845e697`。本轮只更新接口协作材料和唯一状态页，不修改 `docs/项目文档/`、产品源码、协议/Schema/夹具、验收结果或原确认结论。

## 1. 检查结论与修订

| 合同 | 文档版本 | 本轮修正 |
| --- | --- | --- |
| CORE-001 | 1.78 → 1.79 | 端口/默认 C 接线已存在；登记干净安装、未知恢复、C 核验/R 和 D 持久业务链阻断 |
| AB-001 | 1.38 → 1.39 | 来源/模型/发布/修订已接；DEC-010 采用保留外名的内部映射；补已实现 confirm_basis 的准确参数/来源索引，不新增协议行为 |
| BC-001 | 0.45 → 0.46 | 默认解析器/LOAD/执行端口已接；实际半程、R 保存与业务核验仍缺 |
| AC-001 | 1.4 → 1.5 | 检查点/永久对象/导入/独立查询保存已有实现；准确关联和未知活动受控恢复待完整验证 |
| BD-001 | 元数据 0.2 → 0.4 | 保留正文 0.3 历史确认；当前 confirm_basis 补遗漏的 basis_revision/受控挑战，替换不存在的 AB 8.17 引用；核心唯一统计与 D 未接投影边界纠正 |
| CD-001 | 1.17 → 1.18 | 适配器与夹具消费者已存在；D-10 证据/来源/当前性反例未修、R 恒空和生产报告消费未接 |

六份合同均补具体 blockers/next_owner/next_action，保持 reviewing 和原提供/消费实现状态。更新仅涉及文档状态，不提升协议主版本或更改记录语义，不需数据迁移；本轮是自动化源码核对，不能冒充提供方/消费方的新签署。

局部自动化合同/夹具/文件往返检查通过，合同全部场景的统一双方对拍未完成，`verification_status` 仍为 `not_run`。CD 原 `last_verified_commit=8d9883c` 明确仅为旧阶段引用，其他原 null 保留，不代填本轮整体对拍提交号。

## 2. 证据与实际验证

| 命令／批次 | 实际结果 | 证据 |
| --- | --- | --- |
| tests/contracts + tests/architecture + ExecutionFacts 真实文件往返 + 验收需求对账 | 280 passed，退出 0，11.61 秒；局部自动化，非完整产品对拍 | [原输出](interface-tests.log)、[JUnit](interface-tests.xml) |
| default_basis_confirmation + saved_case_basis_proof + approval_origin_boundary | 31 passed，退出 0，563.42 秒；准确依据、原意图/重启、受控挑战和角色边界 | [原输出](basis-interface.log)、[JUnit](basis-interface.xml) |
| generate_schemas.py | 退出 0 | [原输出](schemas.log) |
| git diff --exit-code -- src/aitest/contracts/schemas | 退出 0，生成物无差异 | [原输出](schema-diff.log) |
| scripts/check_docs_facts.py --full | 退出 0，数值断言 5 处／差异 0 | [原输出](docs-facts.log) |
| 六合同版本/状态/非空阻塞与台账/项目现状对账 | 通过；项目主责分册、代码和验收状态未变 | [contract-checks.json](contract-checks.json) |

完整 argv/退出码/时长在[results.json](results.json)。本轮未重复完整 Python 套件；前轮全量中止/专项/打包与新反例分别读[代码复核](../p1-code-review-20261012/README.md)。A-19/D-10 本轮只登记到接口交接边界，仍未修，已有夹具通过不能覆盖其失败反例。

## 3. 原文与确认历史

[documents-before.json](documents-before.json)记录原工作区接口 Markdown 的字节数、SHA-256 与 Git 基线，本机另有 `.runtime/interface-docs-before-90baba0.zip`。完整旧台账与四份交付说明进入[状态复核前快照](../../接口对接/归档/2026-10-12状态复核前/总台账-2.80.md)；原确认表/评审作者、日期和结论没有删改，review 文档增加历史生效说明，当前实现读 contract.md。

恢复示例：`git show 90baba0:docs/接口对接/进行中/BD-001-计划与依据展示/contract.md`。快照中的相对导航固定到原 Git 基线，不另立生效合同。逐合同版本/阻塞变更见[changes.json](changes.json)，现行导航见[接口台账](../../接口对接/README.md)。链接/锚点检查见[document-checks.json](document-checks.json)，后续不得把旧“未接线”日志重新写成当前待办。

## 4. 未验证

完整提供/消费方真实产物对拍、真实 Trae 业务 UI、实际业务对象/供应方故障、R 持久选择、未知发布人工恢复与物理掉电、全部 AC 真实证据均未由本轮取得。未移入 `已完成/`，未更新验收 verified，未改主责字段/公式；后续按具体 blockers 和负责人推进。
