# 一期工程检查-B包

复核完成日期：2026年10月3日。取证基线：`develop 40c82c3`；产品源码仍`9bd4337c1a0696db9cd8bd21e7b67f3a751923bb`。本轮分支`codex/p1-end-to-end-audit-20261003`。

本文是**当前未闭合问题清单**，只列待修复、待补全、待核对或待真实验收的工作；部分完成条目只保留剩余缺口，编号不重排。实施进展和历史问题流转见[整体对比](当前代码分析与一期工程对比.md)，清单维护记录见[本轮修改日志](修改日志/袁/2026-10-03-一期端到端深入检查.md)。

其他包：[A包](一期工程检查-A包.md) · [C包](一期工程检查-C包.md) · [D包](一期工程检查-D包.md)。

## 1. 依据与检查边界

- 主责来源：[文档总览](项目文档/README.md)、[总体架构](项目文档/总体架构.md)、[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[一期功能](项目文档/一期/功能文档/01-功能文档.md)、架构00—06及[面板与工作台](项目文档/一期/设计文档/01-面板与工作台.md)。一期仍为17 FR/35 AC，不增删或重编号。
- 四部分分工沿用原拆分方案；字段、状态、时序按现行分册及[接口总台账](接口对接/README.md)、DEC-001—006实施，不因文档更新改接口冻结状态。
- 本轮144个非生成产品文件逐文件读取，7份Schema与2份npm锁另作生成/构建核对；153文件清单、14组隔离观察及端到端阻断见[深入检查](一期端到端深入检查-2026-10-03.md)。阅读覆盖不等于全部路径实测；旧19文件定向深查作为历史证据保留。
- “高风险”有源码或当前反例依据；“接入缺口”需补默认装配/真实持久链；“待验收”需真实输入、版本、预期/实际和证据。“待核对”不计为已确认缺陷。未以文件数、通过数估算完成率。

## 2. 主责与尚缺验收

| 包/FR主责 | 当前需完成的范围 | 牵头AC |
| --- | --- | --- |
| B：项目与计划 / FR01—07 | 受控确认/完整依据、真实模型响应安全与来源核对、运行修订和产品验收 | 01、03、12、17、20、30、31、32（8项） |

四个交接面为本地协议、工作单元/存储端口、PreparedRun、ExecutionFacts。B负责来源规则、冻结依据与准备身份，A负责端口与装配；C负责实际start、运行中修订保存/应用和加载来源，D负责编辑/确认/展示。SourceSnapshot、准备观察配对与模块路径语义按DEC-001—006及主责分册实施。

`tests/acceptance/p1/status.json`：本包7项not_verified，P1-AC20为blocked，全部evidence.path为空。全一期仍0verified/7not_verified/1blocked/27untested（35项）。需补齐本包真实证据和当前前置说明；本轮没有代填验收或改变AC预期。

## 3. 当前待修复与待补全问题

### 3.1 问题、证据与完成条件

| 编号/类型 | 当前剩余问题及依据 | 影响/完成条件 |
| --- | --- | --- |
| B-12 P1/本轮新增 | **命令、正文、模型策略和读取项目归属未统一校验**：默认 LocalAPI 接受 envelope-project 下保存 payload-project 的项目。保存的 Delivery 正文没有 project_id，随后 load_delivery(foreign-project) 可读取。真实模型编排在内存外部端口下接受 project-ticket 的策略为 other-project 准备请求。 源码[usecase_registry.py](../src/aitest/application/usecase_registry.py)第863行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期架构01第1/6/8节、架构04第2节；FR01/02/04/06，AC01/17/31/32。B 校验正文/引用/策略归属，A 同时守住持久命名空间；旧记录归属未知应显式阻塞或迁移。覆盖项目/绑定/任务/交付/规则引用/模型策略的同项目、跨项目及缺失归属输入。 |
| B-13 P1/本轮新增 | **交付方自填 verified_in_scope 即成为已验证交付**：默认 save_delivery 接受未登记 task_id 和调用方填写 verified_in_scope=[all-required]，没有执行记录或证据，读回 Delivery.is_verified 为真。 源码[usecase_registry.py](../src/aitest/application/usecase_registry.py)第1015行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期功能10.2/12节、架构01第1节；FR02，AC01/21。自述字段与核心验证投影分离；核对任务/版本及新执行依据，拒绝自报验证标记或将其明确保留为声明。验证无执行、只自测说明、跨任务、失效证据与真实有效验证。 |
| B-14 P1/本轮新增 | **发布忽略调用方预期修订，旧编辑可继续发布**：真实默认 HUMAN_UI 接口依次发布 original/new edit/stale edit，三个 Command.expected_revision 都是 0，全部 published=true，rule_version 产生三份正文历史。 源码[usecase_registry.py](../src/aitest/application/usecase_registry.py)第1280行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期架构01第8节、架构04原子提交；FR05/06，AC12/17/20/31。从编辑/挑战传递准确预期修订，在同事务检查相关依据；验证两个编辑者、旧修订、同请求重传、新明确发布、计划/规则及确认过期路径。 |
| B-15 P1/本轮新增 | **运行修订允许替换掉必测验收关联，且未校验计划完整身份**：必测 Case 的验收关联从 {AC-A,AC-B} 改为 {AC-A,AC-C} 被 accepted；同修订号但 plan_id=unrelated-plan 的计划也被接受。反例经真实应用门禁，输入为已派生的运行中 ExecutionFacts 夹具。 源码[runtime_revision.py](../src/aitest/domain/planning/runtime_revision.py)第417行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期功能第4/15节、架构01第4/8节；FR06/07，AC20/24/31。要求原关联集合包含于新集合，完整核对冻结计划身份及 Case/依据引用；补删除、替换、增加、同版本异计划/异摘要、正在执行步骤与受影响完成步骤。 |
| B-16 P2/本轮新增 | **目录移动沿用旧确认和内容摘要**：confirmed=true、manifest_digest=sha256:old 的 C:/old 绑定移动到 C:/new 后，修订增加到 2，但仍 confirmed=true 并继承原摘要。 源码[context.py](../src/aitest/application/project/context.py)第273行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期架构01第1节、功能10.1；FR01，AC12/25/30。保留旧绑定，新的路径/形态先待确认并重新固定内容；补同字节移动、不同内容目标、取消选择、形态转换及冻结运行仍引用旧绑定。 |
| B-17 P2/本轮新增 | **合法单模块无依赖项目被判为阻塞缺口**：一个有职责/输入输出的模块、匹配项目的无边依赖图和有效环境输入，detect_context_gaps 返回 blocking missing_dependency_registration。 源码[context.py](../src/aitest/application/project/context.py)第437行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期功能第11节、需求 AC03；FR01/03，AC03/30。区分已明确无依赖和未登记；覆盖单模块、独立多模块、真实依赖缺登记及无模块，单模块变更/回归照常执行。 |
| B-11 接口/保存缺陷（新发现） | 合法Case.revision=9被保存为仓储修订1、2；准确读取@1/@2均返回Case@9，正文与记录修订不一致（B-REVISION-01-payload-storage；[persistence.py](../src/aitest/application/planning/persistence.py)，124—125行） | 项目架构01第3/7节固定精确用例/范围修订；B保存/冻结主责；FR06，AC20/24/31。 B保存时核对或分配权威新正文修订，准确读回验证内容身份；拒绝旧修订/跳号错配。补新建/更新/重复旧修订及Scope/规则同类路径验证；A-11跨项目归属仍由A主责。 |
| B-10 高风险/新发现 | `request_model_draft`缺持久业务请求号及原结果复用分支；完全相同入参两次调用共用同一出站request_id，却调用模型2次、保存2份草稿、出站记录到修订4（`B-MODEL-04-repeat-intent`）。稳定记录ID没有去重外部副作用；当前无法区分重传与明确重新生成 | AC15/17/32及持久意图合同；入口提供一次明确生成的稳定身份，同键同输入返回原结果、异输入冲突，明确重新生成使用新意图；中断先核对原出站事实，补响应丢失/重复请求/未知结果和新生成对照，不盲目重发。仅用内存模型复现，未调用真实供应方 |
| B-01 接入缺口 | `save_confirmation`已有保存函数但未由受控人工动作接入；模板/SourceSnapshot完整业务保存和C/D消费路径仍缺，模型与运行修订动作未注册。默认15动作保存出的草稿目前触发A完整性误判并阻塞重启，见A-06 | FR01—07、B牵头8项AC；接受控确认与其余依据保存/查询，模型/运行修订接统一入口；与A闭合内联摘要及引用合同，验证当前默认装配下完整保存→重启→读回 |
| B-03 高风险/接入缺口 | `B-MODEL-03-response-before-save`：合成模型返回password正文，经当前编排原样进入generated_content记录；协议出口过滤不等于落盘前安全。真实投影/凭据/模型未进默认核心，迟到响应及真实模型失败恢复仍待验证；重复出站见B-10 | AC15/17/30/32；响应保存前过滤已知凭据及无法安全处理材料，补A真实端口装配；出站身份与恢复按B-10处理，AI关闭仍可人工操作，记录真实失败证据 |
| B-04 接入缺口 | 实际变更来源与正式SourceSnapshot转换未装配；drift仍只检查binding/environment/plan，虽然Case/规则/验收范围已有保存函数却未纳入核对，project_revision/template/snapshot也uncovered。已有JSON往返尚未实现合同的规则Markdown导入导出 | AC03/12/13/25/30；将准确已保存修订纳入完整依据核对，接SourceControl/快照/加载链及规则Markdown格式，读到旧修订不能替代当前来源一致 |
| B-05 接入缺口/待验收 | RuntimeRevision未持久写入实际运行序列，C runner未消费接受/失效清单；缺运行中/正在执行快照夹具和真实半程运行证据 | AC20/21/31；C保存RunPlanRevision/StepRevisionRef、应用暂停与依赖失效，D呈现双序列及准确依据；验证半程修订/驱动收窄/必测不弱化的真实流程 |
| B-06 待补全/待验收 | 牵头8项AC仍7not_verified、1blocked，证据路径均空；登记内部分前置说明与当前源码不一致，真实用户输入、软件/制品版本、预期/实际和证据未补齐 | AC01/03/12/17/20/30/31/32；根据当前实测更新前置与阻塞说明，逐项补真实证据后再登记verified；P1-AC20须包含运行中修订和C/D实际消费 |

### 3.2 源码实施入口与剩余缺口

| 源码范围 | 尚需修复、补全或验证的边界 |
| --- | --- |
| `planning/persistence.py`、`project/persistence.py`、`serialization.py` | 修B-11正文/仓储修订不一致，并与A-11核对归属；确认保存函数需接受控入口，模板/SourceSnapshot等完整依据及实际用户操作/重启路径仍缺；保存摘要需与A完整性合同对齐 |
| `portable.py`、`domain/planning/rules.py` | 当前仅规范JSON往返，合同要求的规则Markdown导入导出尚未实现；自定义模板仍缺完整发布/保存流程 |
| `publish.py`、`prepare_run.py`、`substrate_adapter.py` | 真实C start、同意图执行去重、并发准备/旧意图恢复与端口统一对拍未闭合 |
| `model_orchestration.py`、`draft.py`、`model_ports.py` | 模型草稿正文保存前缺安全过滤；真实模型/凭据未默认接线，重复出站已复现，见B-10；需区分稳定业务意图与明确新生成 |
| `regression_graph.py`、`drift.py` | 实际变化来源及完整冻结依据尚未核对；uncovered六类来源仍未接，部分已有保存能力不得继续按无记录跳过 |
| `runtime_revision.py`、`run_mode.py` | RuntimeRevision未持久写入运行序列，C实际runner未消费；半程修订/失效/暂停与D双序列展示未证 |
| `usecase_registry.py`、`b_registration.py` | 模型、运行修订、受控依据确认动作未注册；与A/C/D默认业务保存、执行及恢复链联合验证未闭合 |

### 3.3 尚未完成的交接

| 接口 | 需要补全的对接与证据 |
| --- | --- |
| CORE-001 / AB-001 | 台账仍reviewing/partial；端口签名与B默认注册实现需双方回写确认和统一对拍，正文摘要/引用恢复风险需共同闭合；模型/确认/运行修订入口未接 |
| BC-001 PreparedRun | C需使用实际B产物核对prepare/start与真实来源，不能只读取夹具；补最新实际对拍及last_verified_commit |
| BD-001 计划/依据展示 | reviewing，D接入not_started；需接完整范围、确认/过期/未验证字段和实际运行修订展示 |
| CD-001 ExecutionFacts | 元数据agreed、C done/D partial、fixture_passed；需C运行中/正在执行及A正式持久序列，D补生产事实适配和最新实际对拍 |

## 4. 当前验证边界

本轮2026-10-03重新执行：全量pytest **1148 passed、2 skipped（37.47s）**，两项符号链接权限跳过；版本/ruff/mypy（136文件）与7份Schema一致性通过。面板build、wheel/sdist、6文件VSIX及共享panel字节核对通过；独立wheel资源/诊断冒烟通过，但doctor仍NOT_READY、MCP relay仍不可用。Playwright **1 failed**（阶段标题预期3、实际4），后续流程未执行。

14组隔离观察确认15项新增问题，探针ruff通过；退出0表示当前缺陷符合断言，不表示已修复或AC通过。真实一期仍0/35，所有证据路径空。真实Trae、业务独立核验、模型、跨核心执行与掉电未验收。完整方法、结果和边界见[本轮深入检查](一期端到端深入检查-2026-10-03.md)与[结果JSON](validation/p1-e2e-audit-20261003/validation-results.json)；旧CI失败仅作为历史证据保留，本轮没有重新推断远端通过。

## 5. 收敛顺序与交付条件

1. 修B-03模型响应落盘前过滤及B-10生成意图/重复出站，与A修正常草稿保存后重启；补受控确认及模板/快照依据的完整业务入口和保存链。
2. 接完整来源/依据漂移、规则Markdown导入导出、真实模型端口；与C持久保存并应用运行修订，补D准确展示。
3. 以真实项目、用户输入、版本、预期/实际和证据更新牵头8项AC的前置/阻塞说明；验证半程运行、模型失败及人工替代流程后再登记验收。

完成条目须按准确源码版本和证据复核后移出本清单；未验证项不得直接关闭。交接任务与FR/AC不因移出问题而省略，历史过程在修改日志、证据目录和Git记录中追溯。
