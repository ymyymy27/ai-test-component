# 一期工程检查-A包

复核完成日期：2026年10月3日；本轮从10月2日开始、10月3日收敛。源码基线：`develop 9bd4337c1a0696db9cd8bd21e7b67f3a751923bb`，文档分支`文档更新-袁`。

本文是**当前未闭合问题清单**，只列待修复、待补全、待核对或待真实验收的工作；部分完成条目只保留剩余缺口，编号不重排。实施进展和历史问题流转见[整体对比](当前代码分析与一期工程对比.md)，清单维护记录见[本轮修改日志](修改日志/袁/2026-10-03-一期源码深查与新增问题.md)。

其他包：[B包](一期工程检查-B包.md) · [C包](一期工程检查-C包.md) · [D包](一期工程检查-D包.md)。

## 1. 依据与检查边界

- 主责来源：[文档总览](项目文档/README.md)、[总体架构](项目文档/总体架构.md)、[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[一期功能](项目文档/一期/功能文档/01-功能文档.md)、架构00—06及[面板与工作台](项目文档/一期/设计文档/01-面板与工作台.md)。一期仍为17 FR/35 AC，不增删或重编号。
- 四部分分工沿用原拆分方案；字段、状态、时序按现行分册及[接口总台账](接口对接/README.md)、DEC-001—006实施，不因文档更新改接口冻结状态。
- 本次19个关键文件定向深查：[JSON](validation/p1-audit-20261002/deep-audit-9bd4337.json)、[脚本](validation/p1-audit-20261002/deep_probes.py)记录8项观察；预览118目标，19定向已审/99明确跳过，[覆盖清单](validation/p1-audit-20261002/deep-review-inventory.json)列范围/原因。不是全仓逐字重审；[前次JSON](validation/p1-audit-20261002/followup-9bd4337.json)和历史材料在[证据说明](validation/p1-audit-20261002/README.md)保留。临时数据/合成请求与本机进程/API实验不替代真实供应方、Trae、业务或掉电AC。
- “高风险”有源码或当前反例依据；“接入缺口”需补默认装配/真实持久链；“待验收”需真实输入、版本、预期/实际和证据。“待核对”不计为已确认缺陷。未以文件数、通过数估算完成率。

## 2. 主责与尚缺验收

| 包/FR主责 | 当前需完成的范围 | 牵头AC |
| --- | --- | --- |
| A：协议与底座 / FR16 | 永久闭包/恢复、全生命周期唯一核心、有限查询、实际来源与安全适配 | 28（1项） |

四个交接面为本地协议、工作单元/存储端口、PreparedRun、ExecutionFacts。A需冻结公开端口并提供同提交边界的保存/查询/恢复；B/C/D通过端口消费。源码来源规则归B，实际加载与业务独立核验归C，报告/问题/展示归D，A的文件hash能力不能替代业务核验。

`tests/acceptance/p1/status.json`：本包P1-AC28为untested，全部evidence.path为空。全一期仍0verified/7not_verified/1blocked/27untested（35项）。需补齐本包真实证据和当前前置说明；本轮没有代填验收或改变AC预期。

## 3. 当前待修复与待补全问题

### 3.1 问题、证据与完成条件

| 编号/类型 | 当前剩余问题及依据 | 影响/完成条件 |
| --- | --- | --- |
| A-12 高风险/新发现 | commit异常释放锁后仍保留暂存/身份，无锁重试被接受；另一UOW持锁的交错提交均序号1，已成功返回的case-a记录丢失（A-UOW-01-failed-commit-retry；[unit_of_work.py](../src/aitest/infrastructure/file_store/unit_of_work.py)，132—140行） | 项目架构04第2节唯一写入队列/原子提交；CORE-001第4节事务状态；AC28。 异常后禁止无锁继续提交；进入仅回滚/核实状态或重新准入并校验权威边界；验证竞争写入、未知发布结果及永久历史不丢。B转接头自动回滚已有，本反例不表示所有B动作必然丢记录。 |
| A-13 高风险/新发现 | 默认urllib跟随302，把Authorization从已确认HTTPS端点转给未确认的另一域，并接受其草稿为ok（A-MODEL-01-redirect-authorization；[model.py](../src/aitest/infrastructure/adapters/model.py)，53—58行） | 一期需求第7节/AC32接收目标变化需重新确认；A模型传输主责，B策略协作。 禁止未经确认的目标跳转，或逐跳校验授权范围，跨目标不转发凭据；与B策略联验同域/跨域/降级跳转。探针保留真实urllib流程、仅替换网络I/O，无真实外发或凭据。 |
| A-11 高风险/新发现 | B正式save_case经A真实端口，将project-a/case-1@1以project-b、expected_revision=1追加为@2，稳定记录链混入两个项目（A-OWNERSHIP-01-cross-project-lineage；[records.py](../src/aitest/infrastructure/file_store/records.py)，552—560行） | 项目架构04第2节原子提交需校验项目归属；架构01稳定项目/记录身份；FR01/06/16，AC28。 A短事务校验稳定记录原项目及payload/project一致；B保存/导入核对命名空间和归属，拒绝跨项目改归属。验证同名不同项目及合法同项目修订；旧修订在本反例中仍保留。 |
| A-14 接口/保存缺陷（新发现） | LocalAPI begin后同request_id的commit指纹冲突，异号commit/rollback不属于事务，仍active；内部B直接UOW路径可以收尾（A-PROTOCOL-01-transaction-identity；[api.py](../src/aitest/interfaces/local/api.py)，101—110行） | CORE-001第3—4节传输请求/事务身份及闭环；A本地协议主责，AC28/34。 公开路由分清传输请求和事务归属，完成begin→commit/rollback及各阶段重传/冲突；内部B直接UOW不受影响。接口语义须双方同步，不擅改冻结状态。 |
| A-02 高风险/接入缺口 | 工作空间仍只在短事务内持writer.lock；`assemble_workspace_core`允许同根目录装配两个不同实例（`A-CORE-02-lifetime-admission`）。默认仅注册B的15动作，C/D依赖需extra_handlers；worker将所有连接标为AGENT_RELAY，父进程消亡看门狗直接os._exit(6)，未核对活动执行/抢救与draining | AC19/26/27/31/34；覆盖全生命周期唯一写入者与可靠宿主会话，接C/D及人工入口；退出按活动事实决定保活/排空，跨入口同意图实际start只一次，旧epoch/重连/真实Trae验收 |
| A-05 接入缺口 | FileQueryIndex仍整读indexes.json、复制全部rows后过滤排序，records仍整读JSON；当前QuerySpec只支持记录类型/ID/修订/排序，未实现主责目录的报告/问题状态筛选、完整摘要索引与同边界事件快照。游标采用offset，完整末尾键/有限访问合同仍需补齐 | AC27/34；按存储第13节交付有限QuerySpec目录和摘要分片，验证无关历史增长不增加整库访问、旧提交分页、报告/问题筛选及快照事件对拍 |
| A-06 高风险/闭包缺口 | `A-INTEGRITY-03-inline-digest`：默认generate_draft保存成功，完整检查却把正文content_digest当objects引用，核心重启blocked；`A-BACKUP-02-snapshot-closure`：缺历史Blob时检查仍ok而物化rejected。backup白名单遗漏generations/core；恢复先resolve目标再查symlink，远端CI的目标符号链接用例失败；新增A-RECOVERY-01-rejected-restore：越界清单被底层拒绝（rejected/verified=false），目标未创建；恢复编排却报repaired/integrity_ok=true | AC16/28/35；按实际记录类型区分内联摘要、对象引用、源码Blob与其他永久材料；核对可达/摘要/项目边界和完整备份目录，恢复先验证原目标及路径各层链接，准确传播拒绝/未核对状态并核实恢复目标；补默认业务保存→重启、缺损备份与有符号链接权限的验证 |
| A-07 高风险/恢复协作缺口 | maintenance._referenced_digests只读.json，跳过永久.jsonl：`A-MAINTENANCE-02-jsonl-reference`中仍被永久JSONL引用的隐藏临时材料已被实际回收。活动门禁只查transactions/active.json及非空事件staging，尚未与C真实活动/未封口输出抢救协作 | AC28；JSON/JSONL及准确记录引用共同证明回收资格，未知或不可读引用应阻塞；C核实活动和抢救后A再迁移/回收，补无短事务标记但仍有执行的验证 |
| A-08 接入缺口/待验证 | B正式SourceSnapshot领域转换与C实际解释器/入口/加载来源仍未在默认核心接线，source_checks.py仍占位；Windows目录项耐久及真实重启/掉电来源证据未完成 | FR01、AC12/13/19/25/30；将固定字节、领域身份、实际加载来源按准确修订贯通，核对editable/PYTHONPATH/绝对入口，补目标Windows存储耐久与来源不匹配阻塞验证 |
| A-09 高风险/安全链缺口 | B模型响应在generated_content_payload落盘前未过滤：`B-MODEL-03-response-before-save`中合成password正文仍进入草稿记录；仅协议出口脱敏不能撤销已保存字节。完整已知凭据值、模型正文、诊断/备份/报告/附件的统一过滤与安全制品链未贯通 | AC07/16/32/35；A提供落盘前安全能力，B在保存响应前使用，C/D共用同底线；无法安全处理保留缺口，验证对象/spool/诊断/备份/导出均无过滤前字节 |
| A-10 接入缺口/待验收 | 默认装配未接真实模型/SecretPort/来源业务链；连接探测需显式connection_endpoint，低层连接事实尚未与真实用户动作、来源会话和失败恢复联合验收 | AC13/15/25/32/33；把连接/凭据/来源能力接入具体业务动作，验证换核心、失败分类、人工降级与重连；只降级实际依赖故障能力的动作，补真实模型及Trae输入/版本/证据 |

### 3.2 源码实施入口与剩余缺口

| 源码范围 | 尚需修复、补全或验证的边界 |
| --- | --- |
| `bootstrap.py`、`core_worker.py`、`workspace.py` | 缺全生命周期writer.lock、C/D默认装配及可信人工会话；父进程死亡直接退出，尚未按活动执行决定保活/抢救 |
| `unit_of_work.py`、`records.py`、`local/api.py` | 修A-12失败后无锁重试/历史丢失、A-11跨项目修订、A-14事务收尾；继续补A-05有限索引/完整查询 |
| `integrity.py`、`backup.py` | 正文摘要被误认对象、源码Blob闭包未核、备份目录未全覆盖；符号链接目标先resolve导致拒绝失效 |
| `maintenance.py`、`migrations.py` | 永久JSONL引用被漏读，真实执行活动和抢救事实尚未参与回收/迁移 |
| `adapters/source_snapshot.py`、`application/execution/source_checks.py` | 固定字节→B领域身份→C实际加载来源未接，目标Windows耐久与掉电仍待验证 |
| `projections.py`、`credentials.py`、模型/连接适配 | 模型响应落盘前与所有材料出口的安全过滤未贯通；默认模型/凭据/来源业务装配及产品失败恢复待验收；model.py需修A-13重定向转发凭据 |
| `adapters/execution/verification.py` | 文件技术完整性不能替代同业务对象函数/API/数据库独立核验 |
| `application/ports.py`、接口CORE-001/AB-001 | 当前签名实现需回写双方确认与统一对拍，台账仍reviewing/partial；不得以本次文档复核擅改接口冻结状态 |

## 4. 当前验证边界

本次8项深查观察由内部断言核对（7新问题、1细化A-06），新探针ruff通过；产品代码/测试未改。下列全量Python/构建/CI结果沿用同基线前次续查，本次未重跑；反例复现成功不表示缺陷已修复或AC通过。

10月2日本机Windows11 x64/build22631、CPython3.13.13、uv0.11.8、Node24.14.1、npm11.13.0，产品0.4.0。版本/ruff/mypy通过（本次136个分析文件），7份生成Schema无差异；全量pytest **1148 passed、2 skipped、0 failed/error**（33.54s）。两项符号链接测试因本机会话无创建权限跳过。

面板build通过，但静态Playwright **1 failed**：阶段区域标题数预期3、实际4；后续断言尚未执行。候选VSIX6文件打包通过。该基线已记录的[Windows CI](https://github.com/ymyymy27/ai-test-component/actions/runs/37008560076)为 **1149 passed、1 failed**：`test_restore_rejects_symlink_target`未抛BackupError；版本/静态/Schema步骤通过。普通CI未运行面板和发布制品检查；本次沿用原CI证据，未重新查询远端。

当前自动化含真实Windows凭据原语、命名管道、本地Git及本机监听器的底层集成；它们不替代真实Trae、模型、业务独立核验、安全导出、跨核心执行/掉电与35项产品AC。合成反例和完整验证范围见[本轮证据](validation/p1-audit-20261002/README.md)。

## 5. 收敛顺序与交付条件

1. 先阻断A-12无锁重试/历史丢失和A-13重定向外发，修A-11项目归属/A-14事务收尾，再修A-06正常草稿保存后重启阻塞/缺Blob误通过/符号链接目标，以及A-07永久JSONL引用遗漏；补真实底座跨包保存→恢复验证。
2. 补A-02全生命周期唯一写入者、可信入口、C/D装配及活动保活/退出，A-05有限摘要分片/完整查询目录；对拍同意图start、旧epoch与同提交快照。
3. 贯通实际加载来源、落盘前安全和真实模型/凭据/用户动作，验证完整永久备份、迁移、抢救/掉电，交付AC28真实证据。

完成条目须按准确源码版本和证据复核后移出本清单；未验证项不得直接关闭。交接任务与FR/AC不因移出问题而省略，历史过程在修改日志、证据目录和Git记录中追溯。
