# 一期工程检查-D包

修复复核日期：2026年10月3日。取证基线：develop 7dbc5a45aa1666652c3dcdc2d0b0604066bc6498（19:34:52 +08:00，PR72合入）；文档工作分支：文档更新-袁。当前 7 项未闭合任务。

本文是**当前未闭合问题清单**，只列待修复、待补全、待核对或待真实验收的工作；部分完成条目只保留剩余缺口，编号不重排。实施进展和历史问题流转见[整体对比](一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)，清单维护记录见[本轮修改日志](修改日志/袁/2026-10-03-develop更新后修复复核.md)。

其他包：[A包](一期工程检查-A包.md) · [B包](一期工程检查-B包.md) · [C包](一期工程检查-C包.md)。

## 1. 依据与检查边界

- 主责来源：[文档总览](项目文档/README.md)、[总体架构](项目文档/总体架构.md)、[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[一期功能](项目文档/一期/功能文档/01-功能文档.md)、架构00—06及[面板与工作台](项目文档/一期/设计文档/01-面板与工作台.md)。一期仍为17 FR/35 AC，不增删或重编号。
- 四部分分工沿用原拆分方案；字段、状态、时序按现行分册及[接口总台账](接口对接/README.md)实施。DEC-007—009已按负责人选择实现并归档，BC-001回到进行中/agreed、双方partial；本轮A/B/C整改不表示D的7条任务或真实验收已完成，详见[修复证据](一期工程整改/03-修复证据/ABC包问题修复证据清单-2026-10-03.md)。
- 本轮按335540b→7dbc5a4的124文件增量及48条旧任务复核，OCR定位58个实现文件并解析规则，按差异/相关调用链核对，20组隔离观察；不是重新逐字审查所有历史文件或执行全部产品路径。见[复核范围与总账](一期工程整改/01-现状检查/一期修复复核-2026-10-03.md)；前次全实现深查保留为历史。
- “高风险”有源码或当前反例依据；“接入缺口”需补默认装配/真实持久链；“待验收”需真实输入、版本、预期/实际和证据。“待核对”不计为已确认缺陷。未以文件数、通过数估算完成率。

## 2. 主责与尚缺验收

| 包/FR主责 | 当前需完成的范围 | 牵头AC |
| --- | --- | --- |
| D：判定、报告与入口 / FR14/15/17 | 生产有效事实聚合、真实新回归/持久报告导出、同源DTO、业务入口及静态测试修复 | 02、10、11、14—16、18、21—23、26、27、29、33—35（16项） |

四个交接面为本地协议、工作单元/存储端口、PreparedRun、ExecutionFacts。D需消费B/C同一次提交的准确事实，通过A端口保存报告/问题并生成安全制品。宿主、CLI、MCP、面板和导出统一使用核心DTO；D不得自报有效执行/核验，不能用文件hash替代业务独立核验。

`tests/acceptance/p1/status.json`：本包16项均untested，全部evidence.path为空。全一期仍0verified/7not_verified/1blocked/27untested（35项）。需补齐本包真实证据和当前前置说明；本轮没有代填验收或改变AC预期。

## 3. 当前待修复与待补全问题

### 3.1 问题、证据与完成条件

| 编号/类型 | 当前剩余问题及依据 | 影响/完成条件 |
| --- | --- | --- |
| D-09 P1/本轮新增 | **空 full 范围在调用方标志为真时返回业务通过**：M/S/E/R/V/P/F 均空，tier=FULL，has_applicable_checks 默认 true，来源匹配、关键链路/证据/环境标志为 true，领域返回 business_outcome=passed、evidence_grade=D、gap=no_valid_execution_basis。 源码[reports.py](../src/aitest/domain/review/reports.py)第459行；[详细证据](一期工程整改/01-现状检查/一期端到端深入检查-2026-10-03.md) | 一期功能第3/14节；FR07/14/17，AC18/21/22/24/27。领域主动核对适用性和有效依据，空/未知输入 fail closed；覆盖显式全不适用、缺失范围、空执行、全合法复用、实际失败和 quick/on_demand，DTO/报告同口径。 |
| D-02 接入缺口 | FailureCandidate/H与F⊆H已有纯规则，但生产ExecutionFacts→DecisionFacts适配仍缺；E/R/V/P及候选有效性布尔事实由调用方给出，尚未按当前Step/Attempt/断言依据/核验/来源/依赖从A同一提交读取派生 | AC19/21/24/27；消费准确C事实形成唯一S/M整用例聚合和H引用，显示未完成U，禁止历史继承+部分新执行拼集合；接正式读取/守卫及真实夹具对拍 |
| D-04 高风险/接入缺口 | IssueClosureEvidence需显式入参，但真实新回归/当前Attempt/已保存证据等仍由调用方布尔自报，应用未查询权威记录或接UOW；ReportExport仍接收自给ref/digest，exports.py占位 | AC11/14/16/29/33/35；查同提交当前新实际回归和有效核验，保存不可变修复/复测/报告；经A制品端口真实生成安全摘要/相对路径包，完整校验后登记成功，LocalReview不改正文 |
| D-05 接入缺口 | 新增S/M汇总和H引用尚未来自生产事实适配；case_ids_revision/source_commit/snapshot commit/cursor允许空，IssueSummary阻塞来源未在真实读取链派生。公开CoverageDTO仍为9个平铺计数，实际协议/各入口尚未统一完整输出形状 | AC21/24/27/34；把准确范围/来源/策略/提交游标接入完整核心DTO，未知不补0/false；面板/报告/CLI/MCP同修订逐项对拍，统一公开合同和生成Schema |
| D-06 阻断交付 | CLI/MCP/面板/Trae 没有同核心业务动作；LocalAPI 对人工动作只做 client_kind 限制，缺真实动作摘要/来源会话/输入与目标修订的受控挑战；扩展无业务消息桥 | AC26/31/33/34；D 实现用户动作与角色边界，A 校验会话/实例，B/C 校验范围/输入/授权修订；MCP 禁止人工确认，不能接受参数自报身份。列表走摘要索引，迟到响应和断线按原项目/运行处理 |
| D-07 未验证 | 信息区三段、动作导航、主失败去向、复制修复说明、键盘/旧报告精确筛选、隐藏/折叠、事件重放/重复、portable 导出均无真实产品证据 | D 牵头 16 项仍 untested；现有静态导航测试和打包不能替代真实 Trae 生命周期与业务流程验收 |
| D-08 部分修复/剩余缺口 | phase-summary 区域标题移出后，三阶段标题数旧断言已通过；Playwright 接下来 getByRole(button, name=计划) 同时匹配“生成计划草稿”和“计划”，strict mode 失败（panel.spec.ts:18），后续详情返回/动作/窄屏未执行。需准确定位业务动作并完成整条静态流程，不能直接删测试。 | AC26相关静态验证；按阶段卡片精确定位并断言三阶段，再跑同面板返回/动作区/窄屏检查，不能仅删测试或把静态通过登记为真实Trae验收 |

### 3.2 源码实施入口与剩余缺口

| 源码范围 | 尚需修复、补全或验证的边界 |
| --- | --- |
| `domain/review/reports.py`、`application/review/reporting.py` | 生产ExecutionFacts→当前S/M整用例集合及H适配未接；报告/复核/导出应用仍未持久化 |
| `domain/review/defects.py`、`application/review/defect_management.py` | 新实际回归关闭依据需查权威记录；缺编号/完整上下文/持久索引与受控人工业务入口 |
| `interfaces/dto.py`、`contracts/views.py` | S/M及H需要准确范围/来源/提交实参和同一公开输出形状，nullable来源不能作为已核对事实 |
| `infrastructure/exports.py`、制品端口调用链 | 尚无真实安全报告/附件包生成、摘要/可携带引用验证和完整读回 |
| `interfaces/tools/cli.py`、`agent_relay.py` | 缺生产业务CLI与stdio转发，无法完成测试/报告闭环 |
| `resources/panel/src/index.ts`、`integrations/trae/src/extension.ts` | 当前静态原型尚无核心DTO/事件/受控业务消息桥及真实Trae证据 |
| `resources/panel/tests/panel.spec.ts` | 计划按钮名称定位匹配两项，后续静态流程未执行，见D-08 |

### 3.3 面板还需实现的业务流程

按[现行面板与工作台设计](项目文档/一期/设计文档/01-面板与工作台.md)及[编辑器内交互草案](项目文档/一期/设计文档/02-编辑器内面板交互设计草案.md)收敛模型API设置、同面板操作、运行/修订、问题确认、回归、报告查询及AI过期呈现。当前index.ts为“当前测试→同面板详情”的静态原型，extension.ts没有核心消息桥，需接真实DTO/事件、受控挑战、用户动作、保存结果与故障恢复。

报告/问题持久查询、S/M与H同源DTO和安全Markdown/portable导出仍需贯通；exports.py只有说明。真实Trae安装、目录切换、Webview业务、保活/退出/重连和旧报告筛选需补逐项证据。

本轮D领域/应用实现未变，D-09空full原反例仍复现；面板仅修阶段区域标题位置，D-08改保留新的按钮定位失败。共7项剩余；生产C完整性/来源事实仍需真实消费，不把资源句柄或自报布尔值当成验证依据。

## 4. 当前验证边界

本轮对 develop 7dbc5a4 重新执行：pytest **1405 passed、2 skipped（62.17s）**；两项符号链接权限跳过。版本0.4.0、Ruff、mypy（127个分析文件）与7份生成Schema无差异检查通过，面板build通过。Playwright **1 failed**：三阶段标题旧断言已过，计划按钮定位匹配两项，后续详情/窄屏未执行。

20组隔离观察覆盖真实本地文件底座、临时 Python 命令及合成/故障输入；探针退出0表示修复对照和剩余反例符合断言，不代表所有问题已修或真实AC通过。本轮未重新打包wheel/sdist/VSIX，未查询新远端CI，旧构建/CI数字按历史保留。真实一期仍 **0/35 verified**，全部证据路径空；真实Trae、供应方/业务核验、物理掉电等未验收。详见[本轮复核](一期工程整改/01-现状检查/一期修复复核-2026-10-03.md)及[证据说明](validation/p1-fix-recheck-20261003/README.md)。

## 5. 收敛顺序与交付条件

1. 补 D-02 当前 C 事实到 S/M 整用例集合/H 的唯一生产适配，同提交保存查询与准确 DTO 来源。
2. 查真实新执行关闭依据，持久报告/复核/问题及安全制品，接 CLI/MCP/共享面板/Trae 与人工动作。
3. 修 D-08 计划按钮定位后完成后续静态流程；核对 D-09 空 full 决策，补16项真实AC证据。

完成条目按准确源码与证据复核后从当前清单移出；已修15条及部分修复过程仅在[本轮总账](一期工程整改/01-现状检查/一期修复复核-2026-10-03.md)、修改日志与Git历史追溯。真实验收条件/FR/AC不因关闭代码反例而省略。

> 本节由 2026-10-10 现状复核追加，不改动上文历史结论；上文基线（`289e5b6` / develop `7dbc5a4`）与编号一律保留。
> 复核基线：分支 `fix`，`HEAD = a79f4c6`（该分支 441 次提交），版本 `0.4.0`。

## 现状复核 2026-10-10

### 1. 源代码规模与静态检查

| 项 | 数值 |
| --- | --- |
| `src/aitest` 模块 | **213** 个（`mypy` 检查 **228** 个源文件），合计 **54,344** 行 |
| ├ `contracts` | 17 文件 / 1,717 行 |
| ├ `domain` | 33 文件 / 5,546 行 |
| ├ `application` | 88 文件 / 26,154 行 |
| ├ `infrastructure` | 60 文件 / 17,055 行 |
| └ `interfaces` | 13 文件 / 2,617 行 |
| `tests` | 306 文件 / 58,960 行 |
| 静态门禁 | `ruff check src tests` 通过；`mypy`（strict，228 文件）通过；`scripts/check_versions.py` = 0.4.0；`scripts/generate_schemas.py` 重跑无 diff（未手改生成物） |
| 架构分层 | `tests/architecture` 通过（入口→接口适配→应用→领域方向未破坏） |

### 2. 本会话按合同落地的实现（均已提交，逐条有回归）

- `contracts/reuse_reference.py`：复用引用严格模型（目标运行/步骤/尝试、来源尝试映射与锚点、摘要可空＝未知、规范化字节），**不授予 R**；
- `application/execution/start_source_binding.py`：start 侧来源绑定解析与准入（映射六处核对、`workdir:` 受限 cwd、适配器唯一键、参数逐字、字节级校验、失败回执与缺口）；
- `application/execution/start_materialization.py`：按架构01 第12节物化到 `workdirs/<run_id>`，**先校验后写盘**、跨入口幂等读取同一冻结结果、映射恰好覆盖冻结清单；
- `application/execution/action_resolver.py`：生产动作解析器（`SavedActionResolver`）＋装配工厂 `build_saved_action_resolver`（真实物化准入、事实尝试序号、最严副作用类别）；
- `infrastructure/adapters/source_snapshot.py`：物化返回 `paths`（相对/实际路径/摘要/大小）与 `content_digest`；
- `infrastructure/path_compat.py`：`Path.is_junction` 的 3.11 等价实现（统一 13 处源码调用与 6 处测试补丁点）；
- `bootstrap.py`：默认装配注入可信解析器（`AB-001` **1.37**）；`environment_resolution.py`：环境未登记归一为 `SOURCE_BINDING_UNVERIFIED`（DEC-012 口径）。

### 3. 接口合同与裁定状态

| 项 | 状态 |
| --- | --- |
| `AB-001` 端口与保存 | `reviewing` **1.38**（1.35 四项 start 语义、1.37 默认注入、1.38 副作用类别默认口径） |
| `BC-001` PreparedRun | `reviewing` **0.45** |
| `CD-001` ExecutionFacts | `reviewing` **1.17**（来源核验期望侧**语义澄清**，冻结字段名与 `execution-facts/1.0` 形状不变） |
| `CORE-001` A 包公共接口 | `reviewing` **1.78** |
| `待裁定/` | **空**（仅 README）；DEC-010/011/012/**013** 均已裁定并归档 |
| 提供方/消费方 | 各合同仍为 `partial`；`verification_status` 仍为 `not_run` |

### 4. 未闭合与真实验收边界（未变化）

- ABC 剩余问题仍 **21 整项（A 7 / B 7 / C 7）**，`B-12` 已关闭；本轮未把任何条目移出清单；
- 一期 35 项 AC：**0 verified / 7 not_verified / 1 blocked / 27 untested**（真实证据路径仍为空）；
- **未验证**：真实 Trae 接入、真实供应方故障、物理掉电恢复、35 项 AC、默认链**真实进程端到端**（默认装配无真实执行端口，缺端口时按反例诚实降级）。

### 5. Python 版本兼容（2026-10-10 更新）

| 解释器 | 状态 |
| --- | --- |
| **3.13**（一期验收版本） | 全绿；本会话全部改动均在其上验证（默认链相关 141 项、受影响套件 615/245/150/215 等） |
| **3.12** | 静态通过（`ruff py311`、`mypy 3.11` 目标覆盖）＋相关批次通过；**未单独全量真跑** |
| **3.11.9**（真机 `E:\Python311`） | 全量 4826 passed / 16 failed → 修 `ctypes` 管道切片刻缺陷后按批次复验 176 + 121 passed；**剩 3 项独有失败待查**（`test_model_policy_approval[boolean_revision]`、`test_execution_start_integrity` 两项提交计数）→ 记为"基本可用，未验证" |
| ≤3.10 | 未做（广泛使用 `StrEnum`/`Self`，需整体替换） |

`requires-python` 现为 `>=3.11,<3.14`；解释器门禁按**登记声明**比对主.次（可选补丁）版本，POSIX `site-packages` 按实际解释器匹配。**一期验收范围不变（仍以 3.13 为准）。**

### 6. 本会话修掉的真实缺陷（含只在实际解释器上才暴露的）

1. 模型材料 JSON 投影 14 个反例（严格解码＋按解码后键值过滤，`POLICY_REVISION 4`）；
2. 复用材料读取的精确 `evidence_ref` 归属；
3. 冻结步骤截止未在 `prepare` 强制；
4. start 来源绑定/物化/解析器：先落盘后拒绝、物化结果缺 `snapshot_id`、二次解析被拒（改幂等）；
5. 环境未登记抛裸 `ValueError` → 协议层 `INTERNAL_ERROR`（归一为 DEC-012 已登记码）；
6. **`interfaces/local/pipe.py` 的 `ctypes` 切片符号性**：3.11/3.12 切片返回有符号整数，≥0x80 字节使 `bytes()` 抛错（3.13 无符号故未暴露）→ 改 `memoryview(buffer)[:read]`，**属 3.11/3.12 用户崩溃级**；
7. 3.11 兼容五类：PEP 695 语法、`Path.is_junction`、`ntpath.isreserved` 语义差距（32 例对照 0 不一致）、`MultiplexedPath.joinpath` 参数个数、测试写死 3.13。

### 7. 证据与入口

- 组件与反例：[动作解析器实施清单](一期工程整改/02-整改计划/动作解析器实施清单-2026-10-09.md)（含 5.6–5.9）；
- 会话回归与接线验证：[session-consolidated-regression-2026-10-09.txt](validation/p1-abc-remaining-20261004/session-consolidated-regression-2026-10-09.txt)、`build-and-wheel-smoke-2026-10-09.txt`；
- 交接与版本兼容说明：[交接说明-2026-10-09.md](一期工程整改/04-交接/交接说明-2026-10-09.md) 第 5.5/5.6 节；
- 变更摘要：[CHANGELOG.md](../CHANGELOG.md) 2026-10-09 与 2026-10-10 条目（后者含版本兼容与管道缺陷）。

### 8. 下一步（本复核给出的收敛顺序）

1. 查清 3.11 剩余 3 项独有失败（`boolean_revision` 校验路径、执行开始提交计数），查清后要么修复并全量复跑，要么在合同中明确排除并保留"未验证"标记；
2. 真实本地进程端到端：为默认装配装配真实执行端口并留下真实进程证据（仍不等于 35 项 AC）；
3. 各合同消费方接入与真实对拍（`CD-001` D 侧读取、`BC-001` 实际加载与复用）；
4. 真实 Trae / 供应方故障 / 掉电 / 35 项 AC——需真实环境，本机不可产生，**不得代填**。
