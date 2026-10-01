# 一期工程检查-B包

复核完成日期：2026年10月2日；本机检查于10月1日执行。源码基线：`develop 10fc22feed4e7927151a28277c93e2e96a3c70d0`，文档分支`文档更新-袁`。

本文是**当前未闭合问题清单**，只列待修复、待补全、待核对或待真实验收的工作；部分完成条目只保留剩余缺口，编号不重排。实施进展和历史问题流转见[整体对比](当前代码分析与一期工程对比.md)，清单维护记录见[本轮修改日志](修改日志/袁/2026-10-02-develop进展复核与文档更新.md)。

其他包：[A包](一期工程检查-A包.md) · [C包](一期工程检查-C包.md) · [D包](一期工程检查-D包.md)。

## 1. 依据与检查边界

- 主责来源：[文档总览](项目文档/README.md)、[总体架构](项目文档/总体架构.md)、[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[一期功能](项目文档/一期/功能文档/01-功能文档.md)、架构00—06及[面板与工作台](项目文档/一期/设计文档/01-面板与工作台.md)。一期仍为17 FR/35 AC，不增删或重编号。
- 四部分分工沿用原拆分方案；字段、状态、时序按现行分册及[接口总台账](接口对接/README.md)、DEC-001—006实施，不因文档更新改接口冻结状态。
- 阅读当前控制流/测试并重放反例；当前依据为[结果JSON](validation/p1-audit-20261001/develop-refresh-10fc22f.json)，方法和历史材料在[证据说明](validation/p1-audit-20261001/README.md)。合成输入/临时目录和静态检查不替代真实宿主、业务核验或掉电证据。
- “高风险”有源码或当前反例依据；“接入缺口”需补默认装配/真实持久链；“待验收”需真实输入、版本、预期/实际和证据。“待核对”不计为已确认缺陷。未以文件数、通过数估算完成率。

## 2. 主责与尚缺验收

| 包/FR主责 | 当前需完成的范围 | 牵头AC |
| --- | --- | --- |
| B：项目与计划 / FR01—07 | 实际输入冻结、默认业务装配、完整依据存储、真实模型/来源及运行修订 | 01、03、12、17、20、30、31、32（8项） |

四个交接面为本地协议、工作单元/存储端口、PreparedRun、ExecutionFacts。B负责来源规则、冻结依据与准备身份，A负责端口与装配；C负责实际start、运行中修订保存/应用和加载来源，D负责编辑/确认/展示。SourceSnapshot、准备观察配对与模块路径语义按DEC-001—006及主责分册实施。

`tests/acceptance/p1/status.json`：本包7项not_verified，P1-AC20为blocked，全部evidence.path为空。全一期仍0verified/7not_verified/1blocked/27untested（35项）。需补齐本包真实证据和当前前置说明；本轮没有代填验收或改变AC预期。

## 3. 当前待修复与待补全问题

### 3.1 问题、证据与完成条件

| 编号/类型 | 当前剩余问题及依据 | 影响/完成条件 |
| --- | --- | --- |
| B-01 接入缺口 | 默认CoreBootstrap/core_worker未构造BUseCaseDependencies或注册B业务；publish_plan、模型、运行修订动作未注册，交付/任务/验收/独立Case/确认等完整记录保存链未闭合 | FR01—07、B牵头8项AC；与A冻结current_revision/commit_seq/next_commit_seq并装配唯一核心，补计划发布、模板草稿正文与其余记录保存，供C/D按准确修订恢复和查询 |
| B-02 高风险 | 独立改变`execution_source.resolved_input_digest`时same_payload=true，旧意图仍prepared且返回改变后的输入，证据键`B-PREPARE-01-resolved-input-only` | AC19/20/31；按计划架构第11/12节闭合实际动作/目标/参数/凭据引用与来源事实的摘要及观察职责，同意图不得换实际输入；补只改单项输入的回归与真实prepare/start对拍 |
| B-03 接入缺口/待验证 | 真实投影、凭据、模型未进默认核心；模型返回正文的已知凭据过滤、调用中断/迟到/重复请求与原出站事实恢复未完整验证 | AC15/17/30/32；接真实端口，证明响应落盘前安全、出站结果可恢复、重复请求不重复出站及AI关闭仍可人工操作；记录真实输入/版本/失败证据 |
| B-04 接入缺口 | 实际变更文件来源和正式SourceSnapshot转换未装配；drift只检查binding/environment/plan可读性，project_revision/case/rule/template/scope/snapshot仍uncovered；规则Markdown导入导出与完整冻结依据未接 | AC03/12/13/25/30；接SourceControl/快照链、保存独立Case/依据/范围/模板修订与规则导入导出，交C在start前核对来源；旧修订可读不能替代当前来源一致 |
| B-05 接入缺口/待验收 | RuntimeRevision未持久写入实际运行序列，C runner未消费接受/失效清单；缺运行中/正在执行快照夹具和真实半程运行证据 | AC20/21/31；C保存RunPlanRevision/StepRevisionRef、应用暂停与依赖失效，D呈现双序列及准确依据；验证半程修订/驱动收窄/必测不弱化的真实流程 |
| B-06 待补全/待验收 | 牵头8项AC仍7not_verified、1blocked，证据路径均空；登记内部分前置说明与当前源码不一致，真实用户输入、软件/制品版本、预期/实际和证据未补齐 | AC01/03/12/17/20/30/31/32；根据当前实测更新前置与阻塞说明，逐项补真实证据后再登记verified；P1-AC20须包含运行中修订和C/D实际消费 |

### 3.2 源码实施入口与剩余缺口

| 源码范围 | 尚需修复、补全或验证的边界 |
| --- | --- |
| `domain/project/context.py`、`project/persistence.py`、`serialization.py` | 交付/任务/验收/独立 Case/确认等完整保存与用户流程未接；空 source_paths 只表示未登记，不得推断影响全部 |
| `domain/planning/rules.py`、`plans.py`、`resources/templates/` | 六模板资源不等于六类执行已实现；规则 Markdown 导入导出、计划编辑/发布入口尚未闭合 |
| `publish.py`、`preparation.py`、`prepare_run.py` | `execution_source.resolved_input_digest` 单独改变仍不影响请求载荷/观察判定，见 B-02；PreparedRun 复用不等于启动动作已去重 |
| `substrate.py`、`substrate_adapter.py` | 仍通过构造注入依赖，A ports.py 的三个只读能力尚未冻结；跨进程装配、并发准备及旧意图恢复需共同验证 |
| `model_orchestration.py`、`model_ports.py` | 真实投影/凭据/模型未进默认核心；模型返回正文的已知凭据过滤、异常中断与重传不能重复出站尚需完整链路验证 |
| `regression_graph.py`、`drift.py` | 变更文件来源仍待真实 SourceControl/快照链装配；drift 明列 project_revision/case/rule/template/scope/snapshot 为 uncovered，intact 只代表已核三类可读，不能宣称完整来源一致 |
| `runtime_revision.py`、`run_mode.py` | 未将 RuntimeRevision 持久写入运行序列，也未由 C 实际 runner 消费接受清单；不代表 AC20 的真实半程修订完成 |
| `application/usecase_registry.py`、`interfaces/local/b_registration.py` | 需要显式 BUseCaseDependencies；bootstrap/core_worker 不自动接线；publish_plan、模型类、运行修订动作尚未注册。generate_draft 目前返回模板草稿元数据，未自动保存正文 |

### 3.3 尚未完成的交接

| 接口 | 需要补全的对接与证据 |
| --- | --- |
| CORE-001 / AB-001 | reviewing/partial；A公开只读能力和默认装配未冻结/接线，B计划/模型/其余保存动作未闭合，双方统一对拍未登记 |
| BC-001 PreparedRun | C需使用实际B产物核对prepare/start与真实来源，不能只读取夹具；补最新实际对拍及last_verified_commit |
| BD-001 计划/依据展示 | reviewing，D接入not_started；需接完整范围、确认/过期/未验证字段和实际运行修订展示 |
| CD-001 ExecutionFacts | 需C运行中/正在执行样例及正式持久序列，D接有效事实聚合；夹具不能替代真实运行消费 |

## 4. 当前验证边界

10月1日本机Windows11 x64/build22631、CPython3.13.13、uv0.11.8、Node24.14.1、npm11.13.0，产品0.4.0。版本/ruff/mypy通过（132个分析文件），7份生成Schema无差异；全量pytest **949 passed，0 failed/error/skipped**。面板build、静态Playwright1项及候选VSIX6文件打包通过。

现有自动化没有覆盖上表全部风险，当前反例/接入任务和真实AC需逐项完成。真实模型/凭据、业务独立核验、Trae用户流程、跨核心/掉电恢复、安全导出和永久留存未验收；旧远端CI不作当前依据。命令与证据详见[复核说明](validation/p1-audit-20261001/README.md)。

## 5. 收敛顺序与交付条件

1. 修B-02单项实际输入冻结；补完整Case/依据/范围/模板/计划保存与规则导入导出，接默认计划/模型/运行修订入口。
2. 与A对拍正式端口和装配，与C接真实来源/start及实时修订落盘，与D接准确编辑/确认/查询；模型响应安全及中断恢复逐分支验证。
3. 以当前实际前置补齐8项AC输入、版本、预期/实际和证据；尤其AC20需真实运行中修订，不能用纯守卫或夹具代替。

完成条目须按准确源码版本和证据复核后移出本清单；未验证项不得直接关闭。交接任务与FR/AC不因移出问题而省略，历史过程在修改日志、证据目录和Git记录中追溯。
