# 一期工程检查-A包

复核完成日期：2026年10月2日；本机检查于10月1日执行。源码基线：`develop 10fc22feed4e7927151a28277c93e2e96a3c70d0`，文档分支`文档更新-袁`。

本文是**当前未闭合问题清单**，只列待修复、待补全、待核对或待真实验收的工作；部分完成条目只保留剩余缺口，编号不重排。实施进展和历史问题流转见[整体对比](当前代码分析与一期工程对比.md)，清单维护记录见[本轮修改日志](修改日志/袁/2026-10-02-develop进展复核与文档更新.md)。

其他包：[B包](一期工程检查-B包.md) · [C包](一期工程检查-C包.md) · [D包](一期工程检查-D包.md)。

## 1. 依据与检查边界

- 主责来源：[文档总览](项目文档/README.md)、[总体架构](项目文档/总体架构.md)、[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[一期功能](项目文档/一期/功能文档/01-功能文档.md)、架构00—06及[面板与工作台](项目文档/一期/设计文档/01-面板与工作台.md)。一期仍为17 FR/35 AC，不增删或重编号。
- 四部分分工沿用原拆分方案；字段、状态、时序按现行分册及[接口总台账](接口对接/README.md)、DEC-001—006实施，不因文档更新改接口冻结状态。
- 阅读当前控制流/测试并重放反例；当前依据为[结果JSON](validation/p1-audit-20261001/develop-refresh-10fc22f.json)，方法和历史材料在[证据说明](validation/p1-audit-20261001/README.md)。合成输入/临时目录和静态检查不替代真实宿主、业务核验或掉电证据。
- “高风险”有源码或当前反例依据；“接入缺口”需补默认装配/真实持久链；“待验收”需真实输入、版本、预期/实际和证据。“待核对”不计为已确认缺陷。未以文件数、通过数估算完成率。

## 2. 主责与尚缺验收

| 包/FR主责 | 当前需完成的范围 | 牵头AC |
| --- | --- | --- |
| A：协议与底座 / FR16 | 永久留存/恢复、正式端口与唯一核心、有限查询、来源耐久和真实适配 | 28（1项） |

四个交接面为本地协议、工作单元/存储端口、PreparedRun、ExecutionFacts。A需冻结公开端口并提供同提交边界的保存/查询/恢复；B/C/D通过端口消费。源码来源规则归B，实际加载与业务独立核验归C，报告/问题/展示归D，A的文件hash能力不能替代业务核验。

`tests/acceptance/p1/status.json`：本包P1-AC28为untested，全部evidence.path为空。全一期仍0verified/7not_verified/1blocked/27untested（35项）。需补齐本包真实证据和当前前置说明；本轮没有代填验收或改变AC预期。

## 3. 当前待修复与待补全问题

### 3.1 问题、证据与完成条件

| 编号/类型 | 当前剩余问题及依据 | 影响/完成条件 |
| --- | --- | --- |
| A-01 接入缺口 | `WorkspaceUnitOfWork`未声明B转接依赖的`commit_seq/next_commit_seq`，`RecordRepository`未声明`current_revision`；B窄协议与正式端口的签名、返回及Secret语义未最终冻结 | FR16、AC16/27/28/31/35；A按CORE-001/AB-001统一公开端口，B/C/D按准确签名对拍；制品/维护方法需由真实调用链验证 |
| A-02 阻断交付 | `CoreBootstrap.create`未自动装配B/C/D依赖；`core_worker`只执行`server.read_message()`，不解析Command、不派发LocalAPI、不回写Response，默认`max_clients=1`断开即退出。短事务排他锁未覆盖工作空间整个核心生命周期 | AC19/26/27/31/34；装配默认业务依赖与统一消息派发，补长期唯一写入者、跨入口持久启动意图、旧epoch、运行保活/draining/退出/重连；与C核对同意图只启动一次，真实Trae验收 |
| A-04 高风险 | records已发布后commit.json写失败，当前记录修订1可读，索引maintenance_required、事件0、活动标记已清；恢复报repaired但committed_sequences为空。证据键`A-COMMIT-01-post-publication` | AC19/27/28/34；统一records权威边界与恢复事实来源，逐个发布点都能恢复同边界记录/索引/事件/意图；保留必要抢救状态，补发布后故障注入和重启验证 |
| A-05 高风险/补全缺口 | 游标仅携schema/key，未绑定查询条件、commit_id、epoch/generation；换project_id仍返回ok（`A-QUERY-01-query-binding`）。列表/记录仍整读JSON，有限摘要分片与同提交事件快照未完成 | AC27/34；按存储第13节实现query_id/版本/条件/commit和末尾完整键绑定；验证旧提交分页、状态筛选、摘要索引及无关历史增长时的有限访问 |
| A-06 待补全/待验证 | 目录白名单与基本sha256检查尚未证明永久记录、证据、报告、导出、诊断和全部历史引用闭包；缺附件/符号链接、活动spool清单及恢复目标边界缺完整产品证据 | AC16/28/35；按存储合同核对每条永久引用可达与摘要，补完整/缺损备份、空目标恢复、活动输出抢救和portable制品联合验证 |
| A-07 待补全 | maintenance回收主要按文件名及同名已发布目标判断，尚未从真实活动执行、可靠保存与无引用事实证明回收资格；未知副作用与未封口输出的迁移恢复协作未闭合 | AC28；C先核实活动并抢救，A再判迁移/回收资格；补永久缺口、备份完整性、generation及故障中断验证，不能把目标存在当可回收依据 |
| A-08 高风险/接入缺口 | SourceSnapshot Blob发布没有显式fsync，耐久发布未验证；B正式领域SourceSnapshot转换与C实际解释器/入口/加载来源核对未接通 | FR01、AC12/13/19/25/30；先确认历史字节可靠保存，再冻结来源；接准确领域身份与实际加载核对，补editable/PYTHONPATH/绝对入口及重启/掉电来源验证 |
| A-09 待补全/待验证 | 自定义projector输出仍需统一安全底线；模型正文、结构化错误和已知凭据在所有落盘/出站出口的过滤链尚未完成联合验证，真实安全报告/附件导出未贯通 | AC07/16/32/35；统一落盘前过滤和安全投影，验证默认/自定义出口、模型响应/错误、诊断/备份/导出；无法安全处理应保留缺口而非保存原字节 |
| A-10 接入缺口/待验收 | ConnectionMonitor/LocalAPI连接状态仍是实例内字典，没有跨重启持久保存；实际模型、Windows Credential、Git来源与用户动作的端到端调用、故障恢复及动作级降级没有验收证据 | AC13/15/25/32/33；保存可恢复连接事实并核对来源会话，接真实凭据/目标与具体业务动作；验证换核心/重连/失败分支，只降级真实依赖该能力的动作 |

### 3.2 源码实施入口与剩余缺口

| 源码范围 | 尚需修复、补全或验证的边界 |
| --- | --- |
| `contracts/responses.py`、`views.py`、`application/ports.py` | `WorkspaceUnitOfWork` 未声明 B 转接依赖的 `commit_seq/next_commit_seq`，`RecordRepository` 未声明 `current_revision`；签名/返回语义与各实现仍需统一冻结 |
| `bootstrap.py`、`interfaces/local/pipe.py`、`editor_host.py` | 默认注册表仍不自动装配 B/C/D 依赖；`interfaces/local/core_worker.py` 只读消息，不解析 Command、不调用 LocalAPI、不回 Response，默认一个客户端断开即退出 |
| `workspace.py`、`atomic.py`、`unit_of_work.py`、`records.py` | 短事务排他锁不等于整个工作空间长生命周期唯一核心；records 发布之后发生投影故障仍留下恢复不完整边界，见 A-04 |
| `objects.py`、`integrity.py`、`recovery.py` | 恢复依据仍取 commit.json，尚未统一从 records 权威边界重建全部索引/事件；不代表跨核心执行抢救和真实掉电通过 |
| `index.py`、`events.py` | 游标只携 schema/key，未绑定条件摘要、commit_id、epoch/generation；列表/记录仍整读 JSON，有限摘要分片与同提交快照未完成 |
| `backup.py`、`migrations.py`、`maintenance.py` | 目录扩展和基本 hash 检查仍需验证完整永久引用闭包；回收仍主要依据文件名及同名目标存在，尚未从活动/提交引用证明资格 |
| `adapters/source_snapshot.py`、`source_control.py` | Blob 写入未显式 fsync，不能把恢复到旧字节的本机结果当掉电持久性保证；B 的正式 SourceSnapshot 转换和 C 实际加载来源尚未闭合 |
| `contracts/redaction.py`、`projections.py`、`credentials.py`、模型与连接适配 | 自定义 projector 的返回值仍需统一底线；模型响应落盘与已知凭据的完整过滤、真实凭据/API/产品入口仍待联合核对。ConnectionMonitor/LocalAPI 的状态为实例内字典，未见跨重启持久保存 |
| `adapters/execution/verification.py` | 只提供技术完整性，不是同一业务对象的函数/API/数据库独立核验 |

## 4. 当前验证边界

10月1日本机Windows11 x64/build22631、CPython3.13.13、uv0.11.8、Node24.14.1、npm11.13.0，产品0.4.0。版本/ruff/mypy通过（132个分析文件），7份生成Schema无差异；全量pytest **949 passed，0 failed/error/skipped**。面板build、静态Playwright1项及候选VSIX6文件打包通过。

现有自动化没有覆盖上表全部风险，当前反例/接入任务和真实AC需逐项完成。真实模型/凭据、业务独立核验、Trae用户流程、跨核心/掉电恢复、安全导出和永久留存未验收；旧远端CI不作当前依据。命令与证据详见[复核说明](validation/p1-audit-20261001/README.md)。

## 5. 收敛顺序与交付条件

1. 先补A-04发布后恢复与A-05游标/摘要索引，A-08耐久来源；每个发布点失败都能恢复一致边界，查询不跨条件或提交。
2. 与B/C/D冻结端口，接默认核心业务派发及全生命周期唯一写入者；验证多入口同意图、旧epoch、执行保活和跨核心恢复。
3. 按永久引用闭包验证备份/迁移/回收，闭合安全投影、真实外部适配及连接状态恢复，再交付AC28真实证据。

完成条目须按准确源码版本和证据复核后移出本清单；未验证项不得直接关闭。交接任务与FR/AC不因移出问题而省略，历史过程在修改日志、证据目录和Git记录中追溯。
