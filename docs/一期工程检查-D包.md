# 一期工程检查-D包

检查日期：2026年10月1日；源码基线：拉取后的 `origin/develop`，`0c890eded76fe6c31ad84c211445025de67f599e`（`A包 lu (#38)`）。

拆分日期：2026年10月1日。本文从《一期工程分包检查》拆出，覆盖 D 包**已实现代码的正确性、未实现能力、跨包接入和验收缺口**。原合并文档可从拆分前的 develop `d62ebf0` 恢复；本次保留原源码基线、问题编号、证据和验收结论。拆分记录见[袁的修改日志](修改日志/袁/2026-10-01-一期工程检查按ABCD拆分.md)，原审计过程见[源码检查日志](修改日志/袁/2026-10-01-一期工程分包与整体源码检查.md)。

其他包：[A 包](一期工程检查-A包.md) · [B 包](一期工程检查-B包.md) · [C 包](一期工程检查-C包.md)。整体 FR/AC 检查见[当前代码分析与一期工程对比](当前代码分析与一期工程对比.md)。

## 1. 依据、范围与证据边界

- 先读[文档总览](项目文档/README.md)、[总体架构](项目文档/总体架构.md)、[阅读索引](项目文档/阅读索引.md)，再对照[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[功能](项目文档/一期/功能文档/01-功能文档.md)、架构 00—06 及[面板与工作台设计](项目文档/一期/设计文档/01-面板与工作台.md)。一期仍为 **17 FR / 35 AC**，未增删或重编号。
- 用户提供的《一期工程四部分拆分与低对接实施方案(1).md》（2026-09-24）用作 ABCD 职责与验收牵头划分参考；现行字段、状态、时序以项目分册及后续接口裁定为准。附件中的协作指令不作为本次额外操作授权。
- 对照[接口总台账](接口对接/README.md)和[裁定归档](接口对接/归档)。不修改 `docs/项目文档/`，不把源码进展自动登记为接口冻结或真实验收完成。
- 全量清点 `src/aitest/` **111 个 Python 文件**、面板与 Trae **2 个产品 TypeScript 文件**以及 **75 个 Python 测试文件**；阅读实现与调用链，重点检查身份、集合规则、事务、恢复、授权、投影和装配。清点包含空包文件和占位，文件数不是实现率，也不是覆盖率。mypy 的 123 个分析文件包含跟随导入，不能替代产品文件数。
- [测试摘要](validation/p1-audit-20261001/test-summary.json)记录失败名称与环境；[隔离复现](validation/p1-audit-20261001/probes.py)及[结果](validation/p1-audit-20261001/probe-results.json)使用临时目录和虚构材料，不调用真实模型、宿主或业务系统。一次性全量 AST 清点和校验中间文件已清理，原始版本可从 PR #40 的 Git 历史恢复；保留可复查的问题证据。

本文用“已实现”表示有实际控制流，用“占位”表示空类/说明或显式 `NotImplementedError`，用“未接通”表示已有实现尚未接入正式端口、统一核心或产品入口。风险标为“阻断交付”“高风险”“接入缺口”“待核对”；不以源码行数估算完成百分比。

## 2. 本包职责与当前结论

| 包 | 责任与现有进展 | 主要未闭合点 | 牵头 AC |
| --- | --- | --- | --- |
| D：判定、报告与入口 | 已新增判定/等级/覆盖集合、报告快照/修订/复核、问题生命周期、应用包装和 DTO | 判定耦合等级、问题重开降级/循环归并；持久判定/导出/CLI/MCP/面板动作未接通 | 02、10、11、14—16、18、21—23、26、27、29、33—35（16 项） |

四个交接面仍是**本地协议、工作单元与存储端口、PreparedRun、ExecutionFacts**。文件所在目录不自动决定语义归属：`SourceSnapshot` 按 DEC-001 由 B 负责来源规则，模型保留在执行领域，A 实现快照/来源端口，C 核对实际加载，D 读取投影。纯文件摘要核验由 A 提供技术能力；同一业务对象的独立核验仍须 C 实现并由 D 消费。

验收登记取自 `tests/acceptance/p1/status.json`。D 包牵头 P1-AC02、10、11、14—16、18、21—23、26、27、29、33—35（16 项），当前均为 untested，真实验收 verified=0。所有 `evidence_path` 为空，本次未改变登记。四包合计仍为 `verified=0`、`not_verified=7`、`blocked=1`、`untested=27`（35 项）；牵头数量不表示该包独立完成全部依赖。

## 3. 已有实现复核与缺口

### 3.1 已有能力逐域检查

| 源码范围 | 已实现及检查 | 仍未闭合 |
| --- | --- | --- |
| `domain/review/reports.py` | Coverage 集合约束、DecisionFacts/决定性失败、full 等级/主要及全部缺口、quick/on_demand 上限；review_decision 单元检查 | 已不是零实现；真实 ExecutionFacts→E/R/V/H 的派生及事实守卫缺失，等级与业务通过耦合有缺陷 |
| 同文件报告/复核部分；`application/review/reporting.py` | 不可变报告上下文/快照、内容修订、独立 LocalReview、导出身份/记录；reporting_application 测试 | 应用明确未实现持久化；没有实际摘要/证据包生成与校验；`infrastructure/exports.py` 占位 |
| `domain/review/defects.py`、`application/review/defect_management.py` | 确认/处理/修复/复测/关闭、非缺陷/重复/暂缓/撤销、有效阻塞查询；issue_lifecycle/defect_management 测试 | 两个可复现问题；新执行关闭依据仍由参数自报，缺统一编号/完整复现上下文与持久索引 |
| `interfaces/dto.py`、`contracts/views.py` | 新增 Decision/ReportSummary/IssueSummary DTO，覆盖投影函数 | 不是“只有 DTO 空骨架”；但覆盖仍是 9 个平铺整数，不满足 S/M 两组、H、修订、引用和来源边界合同 |
| `interfaces/tools/cli.py`、`agent_relay.py` | 离线 doctor/模板发现及明确能力不可用输出 | 没有生产业务 CLI 和 stdio 转发，不是测试/报告闭环 |
| `resources/panel/src/index.ts`、`integrations/trae/src/extension.ts` | 共享静态面板、导航、受信任工作区检查、CSP/nonce、VSIX 打包 | 可构建/静态导航可测；无核心 DTO/事件订阅/业务消息桥/动作区，无真实 Trae 接入证据 |

### 3.2 逐项问题与完成条件

| 编号 / 风险 | 问题及证据 | 影响 / 主责下一步 |
| --- | --- | --- |
| D-01 高风险 | `_full_pass_conditions_met` 要求 evidence_grade=A。所有必测有效通过，仅非关键局部未知时得到 B/incomplete：**D-DECISION-01**，违背功能第 3 节“低等级本身不改业务结论” | AC18/21/22/23；按业务条件判 outcome，等级独立派生；补 A 失败、B 非关键缺口但业务有效通过、C/D 缺证据/来源不符等决策表反例 |
| D-02 高风险 | Coverage 允许调用方给 V/P/F，但 H 是另给的 DecisiveFailure 列表；未从当前 Step/Attempt/断言依据/核验/来源/依赖派生且未守住 F⊆H。`reused⊆verified` 又比字面集合合同更强；**D-COVERAGE-01** 仅记录该额外约束，合法整用例复用资格仍需主责合同核对，不计已确认缺陷 | AC19/21/24/27；D 用同一次提交的 C 事实作唯一聚合，校验/派生 H 与引用，显示 U 即使已有 H；禁止历史继承+部分新执行拼整用例。额外复用约束先核对阅读索引主责，不自行改定义 |
| D-03 高风险 | `mark_duplicate` 可返回循环归并的更新对象：**D-ISSUE-01**；读取时查环不能替代变更前拒绝。`undo_disposition` 清除原 P1 severity，重开后阻塞集合为空：**D-ISSUE-02** | AC11/14/33；提交前检查整个主问题链无环、保留最高有效严重度；撤销/主问题重开恢复阻塞；暂缓/非缺陷依据不得改变原失败断言 |
| D-04 高风险 / 接入缺口 | `close_issue` 证据有效等布尔入参默认 true，仅看新 ID 而未查真实新执行/已保存证据；应用未接 UOW。ReportExport 接收调用方给的 ref/digest，无实际生成/安全校验 | AC11/14/16/29/33/35；D 查当前新实际回归及有效核验，保存不可变修复/复测；经 A 制品端口生成安全摘要/相对路径包，全部校验后登记成功，LocalReview 永不改报告正文 |
| D-05 接入缺口 | DTO 缺 selected_summary/required_summary、case_ids_revision、H 与失败引用、source_commit/policy、快照同边界游标；IssueSummary 的 blocking 可默认 false 而无核心计算来源 | AC21/24/27/34；输出与领域同源的完整合同，未知不能默认 0/false；相同来源修订的面板/报告/CLI/MCP 字段逐项对拍；不手改生成 Schema |
| D-06 阻断交付 | CLI/MCP/面板/Trae 没有同核心业务动作；LocalAPI 对人工动作只做 client_kind 限制，缺真实动作摘要/来源会话/输入与目标修订的受控挑战；扩展无业务消息桥 | AC26/31/33/34；D 实现用户动作与角色边界，A 校验会话/实例，B/C 校验范围/输入/授权修订；MCP 禁止人工确认，不能接受参数自报身份。列表走摘要索引，迟到响应和断线按原项目/运行处理 |
| D-07 未验证 | 信息区三段、动作导航、主失败去向、复制修复说明、键盘/旧报告精确筛选、隐藏/折叠、事件重放/重复、portable 导出均无真实产品证据 | D 牵头 16 项仍 untested；现有静态导航测试和打包不能替代真实 Trae 生命周期与业务流程验收 |

## 4. 共享验证结果与失败归属

以下保留原审计的**整体检查记录**，不是 D 包独立测试统计；各包均需结合自己的问题和跨包依赖读取。

环境：Windows 11 x64（build 22631），CPython 3.13.13，Node 24.14.1，npm 11.13.0；锁文件安装 `uv sync --extra dev --locked` 通过，产品版本 0.4.0。

| 检查 | 结果 | 含义 |
| --- | --- | --- |
| `uv run python scripts/check_versions.py` | 通过 | 版本一致 |
| `uv run ruff check .` | 通过 | 含本次隔离复现/清点脚本；不是业务正确性证明 |
| `uv run mypy` | 通过，基线 123、文档复核 125 个分析文件（含跟随导入） | 类型门通过；不是产品源码文件数 |
| Schema 重新生成 + `git diff --exit-code -- src/aitest/contracts/schemas` | 通过 | 7 份生成物一致；现有 Schema 仍可能缺合同字段 |
| 全量 `uv run pytest` | **收集失败，1 error** | `test_a_pipe_peer_rejection.py` 无法导入 `check_peer_identity`；未运行成完整测试集 |
| 排除上述文件的诊断运行 | **737 passed / 16 failed / 4 warnings**，19.47 s | 不是全量通过；15 项管道/宿主/装配失败，1 项 UOW 返回字典不一致。完整名称见测试摘要 |
| 20 项隔离复现观察 | 19 项具体问题及 1 项额外复用约束待核对 | 问题观测不等于修复通过；脚本执行成功不表示被测代码通过 |
| 面板 npm ci/build、Trae npm ci/package | 通过 | TypeScript 检查、共享 JS 和 6 文件 VSIX 可构建；未验收真实 Trae |
| 面板 Playwright | 初次缺 Chromium；安装测试浏览器后 **1 passed** | 仅静态面板窄宽度导航/信息可见性，不涉及核心业务 |
| develop 基线远端 CI | **失败**，[run 36729829040](https://github.com/ymyymy27/ai-test-component/actions/runs/36729829040) | 版本/ruff/mypy/Schema 通过，pytest 同样收集失败；普通 PR 不执行仅 tag 触发的 release 制品步骤 |
| P1-AC01—35 真实环境验收 | **0/35 verified** | 没有真实宿主、多类业务、掉电或完整留存证据；未验证项保留 |

命令、失败分类和复现结果见[证据说明](validation/p1-audit-20261001/README.md)。本次源码检查没有修复上述产品缺陷或删除失败测试。原检查 PR 的 CI/合并追踪在[修改日志](修改日志/袁/2026-10-01-一期工程分包与整体源码检查.md)；随后的一次性文件清理、CI 诊断和发布准备改进另见[清理与 CI 复核日志](修改日志/袁/2026-10-01-中间产物清理与CI复核.md)，不改变本文的产品基线与验收结论。

后续 develop `d62ebf0` 的 [CI 36754947548](https://github.com/ymyymy27/ai-test-component/actions/runs/36754947548) 运行可收集测试后为 **737 passed / 16 failed / 1 collection error / 4 warnings**。15 项占位/装配失败、1 项 UOW 返回值差异及缺失身份校验函数均集中在 A 包；该结果不代表 B/C/D 已完成一期验收。本次只拆分文档，不登记为这些产品问题已修复。

## 5. 交接与下一轮完成条件

1. **D 修判定/问题并接产品**：唯一派生 E/R/V/H、等级与业务分离，问题归并无环/重开不降级；真实制品与安全导出，完整 DTO/事件同源；CLI/MCP/面板/Trae 共享核心。
2. **四包共同交付 AC 证据**：按需求第 5 节逐项给输入、软件/源码/制品版本、预期/实际和原始证据路径。PreparedRun/ExecutionFacts 夹具交接、静态质量、单元通过、真实环境验收分别登记；任何单包“完成”不自动推出一期完成。

本包推进须同时核对其他包的交接条件：[A 包](一期工程检查-A包.md) · [B 包](一期工程检查-B包.md) · [C 包](一期工程检查-C包.md)。整体推进顺序与逐项 FR/AC 对照仍见[当前代码分析与一期工程对比](当前代码分析与一期工程对比.md)。
