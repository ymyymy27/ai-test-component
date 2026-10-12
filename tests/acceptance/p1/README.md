# 一期验收登记

`status.json` 是唯一逐项验收台账，scenario/expected/related_frs 逐字来自需求文档，verified 需非空 evidence.path 和真实环境验证。本清单按 2026-10-12 的现有台账整理人读索引，没有修改结果或把组件通过转成验收。

## 阅读与准备

先读[项目现状](../../../docs/现状.md)、[一期需求第 5 节](../../../docs/项目文档/一期/需求文档/01-需求文档.md#5-验收场景)、[当前工程对照](../../../docs/一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)。每项的 prerequisites、artifact_versions、blockers、source_evidence 和 unverified_reason 直接读机器台账。旧“端口无签名／未实现存储／仅内存模型／无可信授权”的文字清单已过时，不再复制。

真实 Trae 只读入口有[步骤模板](../../../docs/validation/p1-trae-entry-20261011/README.md)，完整业务验收还须覆盖计划/授权/运行中修订、独立核验、报告/问题/导出、人工/导入、恢复和模型不可用。准备脚本或普通浏览器测试不能作为完整 AC 的证据。

## 按牵头包索引

| AC | 牵头 | 登记结果 | 关联 FR |
| --- | --- | --- | --- |
| P1-AC01 | B | not_verified | P1-FR02、P1-FR06 |
| P1-AC02 | D | untested | P1-FR11、P1-FR14 |
| P1-AC03 | B | not_verified | P1-FR01、P1-FR03 |
| P1-AC04 | C | untested | P1-FR08、P1-FR10、P1-FR11 |
| P1-AC05 | C | untested | P1-FR10、P1-FR12 |
| P1-AC06 | C | untested | P1-FR12、P1-FR10 |
| P1-AC07 | C | untested | P1-FR11、P1-FR14 |
| P1-AC08 | C | untested | P1-FR13、P1-FR15 |
| P1-AC09 | C | untested | P1-FR09、P1-FR11 |
| P1-AC10 | D | untested | P1-FR09、P1-FR15 |
| P1-AC11 | D | untested | P1-FR15 |
| P1-AC12 | B | not_verified | P1-FR03、P1-FR05 |
| P1-AC13 | C | untested | P1-FR08、P1-FR09、P1-FR14 |
| P1-AC14 | D | untested | P1-FR15、P1-FR14 |
| P1-AC15 | D | untested | P1-FR09、P1-FR15 |
| P1-AC16 | D | untested | P1-FR14 |
| P1-AC17 | B | not_verified | P1-FR04、P1-FR05、P1-FR06 |
| P1-AC18 | D | untested | P1-FR07、P1-FR08、P1-FR14 |
| P1-AC19 | C | untested | P1-FR09、P1-FR07、P1-FR14 |
| P1-AC20 | B | blocked | P1-FR06、P1-FR07、P1-FR03、P1-FR14 |
| P1-AC21 | D | untested | P1-FR07、P1-FR14、P1-FR06 |
| P1-AC22 | D | untested | P1-FR07、P1-FR14、P1-FR12 |
| P1-AC23 | D | untested | P1-FR07、P1-FR08、P1-FR10、P1-FR14 |
| P1-AC24 | C | untested | P1-FR04、P1-FR06、P1-FR11、P1-FR13、P1-FR14 |
| P1-AC25 | C | untested | P1-FR01、P1-FR08、P1-FR10、P1-FR14 |
| P1-AC26 | D | untested | P1-FR17、P1-FR07 |
| P1-AC27 | D | untested | P1-FR17、P1-FR14 |
| P1-AC28 | A | untested | P1-FR16、P1-FR14 |
| P1-AC29 | D | untested | P1-FR14 |
| P1-AC30 | B | not_verified | P1-FR01、P1-FR04、P1-FR06、P1-FR08、P1-FR17 |
| P1-AC31 | B | not_verified | P1-FR04、P1-FR06、P1-FR07、P1-FR14 |
| P1-AC32 | B | not_verified | P1-FR04、P1-FR06、P1-FR17 |
| P1-AC33 | D | untested | P1-FR09、P1-FR15、P1-FR17 |
| P1-AC34 | D | untested | P1-FR14、P1-FR16、P1-FR17 |
| P1-AC35 | D | untested | P1-FR14、P1-FR16 |

## 登记边界

本轮产品源码与验收 status.json 未变；A-19/D-10 的新反例需先修复，既有完整套件通过不代表相应产品路径已验收。实际执行后按 AC 保存输入、软件及制品版本、预期、实际、证据文件与未验证原因；不因文档清理降低验收范围。需求对账和证据硬边界由既有 acceptance tests 强制。
