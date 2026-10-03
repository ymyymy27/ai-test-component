# 一期工程检查-C包

复核完成日期：2026年10月3日。取证基线：`develop 40c82c3`；产品源码仍`9bd4337c1a0696db9cd8bd21e7b67f3a751923bb`。本轮分支`codex/p1-end-to-end-audit-20261003`。

本文是**当前未闭合问题清单**，只列待修复、待补全、待核对或待真实验收的工作；部分完成条目只保留剩余缺口，编号不重排。实施进展和历史问题流转见[整体对比](当前代码分析与一期工程对比.md)，清单维护记录见[本轮修改日志](修改日志/袁/2026-10-03-一期端到端深入检查.md)。

其他包：[A包](一期工程检查-A包.md) · [B包](一期工程检查-B包.md) · [D包](一期工程检查-D包.md)。

## 1. 依据与检查边界

- 主责来源：[文档总览](项目文档/README.md)、[总体架构](项目文档/总体架构.md)、[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[一期功能](项目文档/一期/功能文档/01-功能文档.md)、架构00—06及[面板与工作台](项目文档/一期/设计文档/01-面板与工作台.md)。一期仍为17 FR/35 AC，不增删或重编号。
- 四部分分工沿用原拆分方案；字段、状态、时序按现行分册及[接口总台账](接口对接/README.md)、DEC-001—006实施，不因文档更新改接口冻结状态。
- 本轮144个非生成产品文件逐文件读取，7份Schema与2份npm锁另作生成/构建核对；153文件清单、14组隔离观察及端到端阻断见[深入检查](一期端到端深入检查-2026-10-03.md)。阅读覆盖不等于全部路径实测；旧19文件定向深查作为历史证据保留。
- “高风险”有源码或当前反例依据；“接入缺口”需补默认装配/真实持久链；“待验收”需真实输入、版本、预期/实际和证据。“待核对”不计为已确认缺陷。未以文件数、通过数估算完成率。

## 2. 主责与尚缺验收

| 包/FR主责 | 当前需完成的范围 | 牵头AC |
| --- | --- | --- |
| C：执行与证据 / FR08—13 | 完成下游失效、字节耐久/持久授权、加载来源、正式提交和多类业务核验 | 04—09、13、19、24、25（10项） |

四个交接面为本地协议、工作单元/存储端口、PreparedRun、ExecutionFacts。C需经A公开端口可靠保存执行/证据，再向D交付同边界ExecutionFacts；运行中修订按B守卫结论真实保存并应用失效/暂停。源码快照规则归B，A提供技术完整性，C仍需证明实际加载与同业务对象独立核验。

`tests/acceptance/p1/status.json`：本包10项均untested，全部evidence.path为空。全一期仍0verified/7not_verified/1blocked/27untested（35项）。需补齐本包真实证据和当前前置说明；本轮没有代填验收或改变AC预期。

## 3. 当前待修复与待补全问题

### 3.1 问题、证据与完成条件

| 编号/类型 | 当前剩余问题及依据 | 影响/完成条件 |
| --- | --- | --- |
| C-10 P1/本轮新增 | **重启恢复把已完成 Attempt 改成失效并允许安全重试**：真实临时 Python 子进程已 COMPLETED，ExitFact、输出及 checkpoint 已保存。新核心模拟 inspect=LOST，recover_pending 扫到该记录并改为 INVALIDATED，action=SAFE_RETRY，失效状态写回文件。 源码[runner.py](../src/aitest/application/execution/runner.py)第211行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期架构02第8/12节、架构04恢复顺序；FR09/16，AC19/28/34。先核对可靠终态/输出，再处理活动或未确定尝试；只读、幂等写、非幂等写分别验证完成后重启、重复恢复、退出标记损坏与真实未完成。 |
| C-11 P1/本轮新增 | **恢复抢救仍存活写入器的尾块，正常封口发生冲突**：写入器已写 13 字节、block_size=1024，原块尚未封口。inspection=RUNNING/identity_matches=true 时 recover_attempt 先 salvage，发布 index=0 的 partial 恢复块后返回 REATTACH；原写入器 close 报 spool block conflicts with existing metadata。 源码[recovery.py](../src/aitest/application/execution/recovery.py)第85行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期架构02第3/8/12节、架构04第3节；FR09/16，AC19/28。分清活动采集与失效尾部的所有权，存活只读取封口块；补存活追加/封口、已退出尾部、重复恢复、恢复再次中断和双流编号。 |
| C-12 P2/本轮新增 | **固定轮询预算使正常长步骤提前进入待核实，串行流程无续行**：真实只读命令运行 2.5 秒、timeout=10000ms，execute_attempt 约 1.05 秒返回 pending_verification/poll_limit_reached，此时进程仍运行；手动等待并 collect 后才得到完整结果。 源码[runner.py](../src/aitest/application/execution/runner.py)第125行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期架构02调度/控制；FR09/10，AC19/23/34。将轮询切片与执行截止分开，持久保留活动态并继续 inspect/collect；验证长步骤及其依赖后续步骤、真正超时、暂停/取消和重启，不靠无限同步等待规避。 |
| C-13 P2/本轮新增 | **BufferedReader.read 延迟短输出采集到进程退出**：真实子进程打印 EARLY 并 flush 后睡眠，设置 stream_block_size=1，约一秒时 spool 无任何块；退出后才采集 EARLY/LATE。 源码[command.py](../src/aitest/infrastructure/adapters/execution/command.py)第389行；[详细证据](一期端到端深入检查-2026-10-03.md) | 一期架构02第12/13节；FR09/16，AC09/19/28。使用可增量返回的读取并保持跨块脱敏；验证小输出后长等待、多次 flush、stderr、跨块凭据、停止/中断、EOF 与 reader 异常。 |
| C-08 高风险/新发现 | 父退出/后台子进程持流时collect仍报complete；返回时2个reader未结束、脱敏摘要0份、输出0字节，探针清理后才封口8字节（C-CAPTURE-01-parent-exit-before-stream-seal；[command.py](../src/aitest/infrastructure/adapters/execution/command.py)，273—286行） | 项目架构02第12/13节退出与完整采集分离；C采集/进程控制主责，A保存协作；AC19/24/28。 核实全组停止及两流EOF/封口、最终块/字节数和脱敏摘要后保存完整ExitFact；未完成保持partial/gap/待核实。补父先退出、持流子进程、reader超时/异常及最终补采，与A正式保存协作。 |
| C-09 高风险/新发现 | Windows OpenProcess缺PROCESS_TERMINATE(0x0001)，原Job绑定None/错误5；同一临时进程仅在内存补该权限位后成功（C-PROCESS-01-windows-job-access；[command.py](../src/aitest/infrastructure/adapters/execution/command.py)，618—625行） | 项目架构02第12节已验证受控作业/进程组；FR09，AC19/28。 按原生API设置正确权限/句柄声明，明确处理绑定失败及受控降级；不能用父退出代替全组停止。验证后台子进程、取消/超时/核心退出及实际全组终止，不用单父进程用例关闭。 |
| C-01 高风险 | `invalidate_downstream_attempts` 只处理活动态，完成的下游消费旧上游仍不失效：**C-INVALIDATION-01**；已支持活动Attempt的直接数据/控制依赖，但同计划修订提前返回；续查已明确上游变化仍不失效，链式依赖只失效直接下游、漏掉传递末端（`C-INVALIDATION-02/03`）。整用例旧复用撤销未接 | AC19/20/24；新 Attempt 立即撤销旧 R；所有实际消费旧结果的下游（含完成项）立即依据过期，同/异输出摘要均处理；无依赖分支保留，历史结论不改 |
| C-02 高风险 | SerialRunner 在 `ExecutionPort.start` 后才保存检查点；缺 A 持久 start 意图结果与动作授权校验；Attempt/Handle 核对不能替代实际输入/来源/授权修订核对 | AC19/24/31；先短事务保存可靠意图/授权/当前 Attempt，再事务外执行；同意图双入口只启动一次，未知副作用不自动重放；新执行重新授权 |
| C-03 阻断交付 | Python/HTTP/Agent/人工/外部导入适配器占位，业务级独立 Verification 未实现；只用命令 exit code/文件 hash 不能判必要业务断言或真实持久化 | FR08—13、AC04—09/24/25；逐类实现真实输入、独立核验、Mock/混合/未知、请求日志关联及人工证据；不支持应显式缺能力，不冒充不适用 |
| C-04 高风险 | 快照复制未证明 editable/PYTHONPATH/绝对入口加载来源；`source_checks.py` 占位；CommandAdapter 子进程状态在实例内存，换核心实例没有完整持久进程身份再附着路径 | AC13/19/25；核对实际解释器/入口/加载来源，失联先 inspect/collect/抢救；不能按 PID 或目录复制成功宣称对应源码；真实宿主退出/强杀/掉电尚未验收 |
| C-05 接入缺口 | FileCheckpointStore/EvidencePublisher 与 A UOW 不共同提交；ExecutionFactsAssembler 默认完整性及传入 commit/cursor 未与权威边界成对读取，采集缺口/可读性也未统一校验 | AC07/09/21/24/27/28；C 经 A 端口保存 Attempt/证据/引用/索引/意图，可靠保存后发布；按准确修订读事实，缺证据保留缺口、不补造完整事实 |
| C-06 未验证 | 暂停/取消/退回/补证、传输恢复与新副作用 Attempt、多类实际动作与宿主保活没有产品级入口和 AC 证据 | C 牵头 10 项仍 untested；补真实 Windows/Trae 的当前 Attempt、停止未确认、失败分支、旧依据过期、跨运行继承不能拼 E/R/V 等场景 |
| C-07 高风险 | 流式 `append` 仅 write/flush 即封口块，`_seal` 随后将游标标 `durable=True` 并保存清单；流文件的 fsync 延后到 close/abort（[spool.py](../src/aitest/infrastructure/file_store/spool.py)，104—110、119—120、155—162 行）。**C-DURABILITY-01**：追加 17 字节后，流文件 fsync=0、清单文件 fsync=1，但持久游标已确认到 offset=17 | FR09/16、AC19/28；按[执行与证据第 12 节](项目文档/一期/架构文档/02-执行与证据.md#12-命令输出与终态的耐久采集)先可靠保存过滤后字节，再持久发布块和确认游标，失败不推进。C 采集与 A 恢复/提交协作；补发布时序及写入故障检查。反例观察同步调用顺序，没有执行真实掉电实验 |

### 3.2 源码实施入口与剩余缺口

| 源码范围 | 尚需修复、补全或验证的边界 |
| --- | --- |
| `domain/execution/runs.py`、`sources.py`、`domain/evidence/evidence.py` | 值对象能表达事实，但当前实例/真实性由调用方填入，尚无正式持久来源证明 |
| `application/execution/runner.py`、`recovery.py` | 必须修正执行前意图顺序及下游失效，见 C-01/C-02 |
| `adapters/execution/command.py`、`redaction.py` | 修C-08输出未封口却完整终态、C-09缺PROCESS_TERMINATE导致Job绑定失败；跨核心恢复/业务断言仍缺 |
| `file_store/spool.py`、`checkpoints.py`、`application/evidence/publication.py` | 检查点直接文件保存，尚未与 A 业务记录和幂等结果共同发布 |
| `application/execution/facts.py`、`contracts/execution_facts.py` | 组装时 source_commit/cursor/修订/完整性由调用方提供，不能证明来自同一 A 提交；覆盖计数占位不能充当 D 的有效整用例集合 |
| `source_checks.py`、`evidence_review.py`、五类执行适配器 | 需真实动作/安全采集/失败/人工与导入路径；必要业务独立核验和多类适配需共同补齐 |

## 4. 当前验证边界

本轮2026-10-03重新执行：全量pytest **1148 passed、2 skipped（37.47s）**，两项符号链接权限跳过；版本/ruff/mypy（136文件）与7份Schema一致性通过。面板build、wheel/sdist、6文件VSIX及共享panel字节核对通过；独立wheel资源/诊断冒烟通过，但doctor仍NOT_READY、MCP relay仍不可用。Playwright **1 failed**（阶段标题预期3、实际4），后续流程未执行。

14组隔离观察确认15项新增问题，探针ruff通过；退出0表示当前缺陷符合断言，不表示已修复或AC通过。真实一期仍0/35，所有证据路径空。真实Trae、业务独立核验、模型、跨核心执行与掉电未验收。完整方法、结果和边界见[本轮深入检查](一期端到端深入检查-2026-10-03.md)与[结果JSON](validation/p1-e2e-audit-20261003/validation-results.json)；旧CI失败仅作为历史证据保留，本轮没有重新推断远端通过。

## 5. 收敛顺序与交付条件

1. 优先修C-09作业绑定/后台组停止与C-08终态前流封口，同时修C-01完成下游失效和C-07字节先耐久后确认游标，保留已实现的直接数据/控制依赖，补完成项、同计划重试、传递闭包及写入失败验证。
2. 执行前保存可靠意图/授权，与A共同提交检查点/证据/引用/幂等结果，与B真实保存并应用运行修订；核对实际入口/解释器/加载来源。
3. 补Python/HTTP/Agent/人工/导入和业务独立核验，验证未知副作用、跨核心恢复和宿主保活，交付牵头10项真实AC证据。

完成条目须按准确源码版本和证据复核后移出本清单；未验证项不得直接关闭。交接任务与FR/AC不因移出问题而省略，历史过程在修改日志、证据目录和Git记录中追溯。
