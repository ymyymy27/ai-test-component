# 一期第二轮 develop 复核证据

复核日期：2026-10-02；源码基线：`9bd4337c1a0696db9cd8bd21e7b67f3a751923bb`，文档分支`文档更新-袁`。本轮对比上次合并点`f4d2bab`之后的PR61—63，核对A/B/D修复及C的剩余交接。旧结果保留在[10月1日证据目录](../p1-audit-20261001/README.md)，不覆盖旧JSON或用历史失败描述当前代码。

材料：[结果JSON](develop-refresh-9bd4337.json) · [确定性探针](probes.py) · [整体对比](../../一期工程整改/01-现状检查/当前代码分析与一期工程对比.md) · [本轮修改日志](../../修改日志/袁/2026-10-02-develop第二轮复核.md)。

本次续查另见[续查JSON](followup-9bd4337.json)、[续查脚本](followup_probes.py)及[续查修改日志](../../修改日志/袁/2026-10-02-一期检查续查与文档收敛.md)。下面第1—4节保留前次快照，第5节记录本次结果。

## 1. 前次实测结果及边界

本机Windows11 x64/build22631、CPython3.13.13、uv0.11.8、Node24.14.1、npm11.13.0，产品0.4.0；依赖复用当前锁文件环境。

| 检查 | 实际结果 | 边界 |
| --- | --- | --- |
| 版本、ruff、mypy | 通过，mypy135个分析文件 | 不是产品验收或完成率 |
| Schema再生成 | 7份无差异 | 不证明生产事实/DTO链完整 |
| 全量pytest | 1148passed、2skipped、0failed/error；38.11s | 符号链接两项因创建权限不足跳过；其余含Windows凭据、命名管道、本地Git、监听器底层集成 |
| 面板build | 通过 | 当前为静态原型 |
| 面板Playwright | 1failed | phase-summary标题预期3实际4；后续断言未执行 |
| Trae npm package | 通过，VSIX6文件 | 未做真实Trae安装、业务、目录切换或生命周期验收 |
| 该基线已记录的Windows CI | [37008560076](https://github.com/ymyymy27/ai-test-component/actions/runs/37008560076)：1149passed、1failed，47.46s | 符号链接目标未抛BackupError；版本/静态/Schema通过；普通CI跳过面板与发布制品 |
| 真实一期AC登记 | 0verified、7not_verified、1blocked、27untested | 35项证据路径全空；没有代填状态或修改预期 |

远端CI运行于Windows Server 2025，能创建符号链接，`tests/unit/test_a_a06_reference_closure.py::test_restore_rejects_symlink_target`实际失败。本机跳过该场景不能证明修复；`backup.restore`先resolve目标再查symlink与失败一致。证据按各自环境保存，不写成全部质量门通过。

## 2. 本轮观察

| 观察键 | 当前结果 | 处理 |
| --- | --- | --- |
| A-COMMIT-01-post-publication | 发布后commit.json失败恢复到记录1/序号1/事件1、index ok；再次healthy | A-04闭合，移出当前清单 |
| A-QUERY-01-query-binding | 跨项目游标invalid_cursor | 移除A-05已修部分；有限索引/完整查询仍缺 |
| A-CORE-02-lifetime-admission | 同根目录可装配两个不同进程内实例，B默认15动作 | A-02保留长期锁/可信入口/C/D/活动保活；该观察不是两个真实worker实验 |
| A-INTEGRITY-03-inline-digest | 默认generate_draft成功后，正文摘要被误认objects引用，重启blocked | 补入A-06跨包真实底座恢复问题 |
| A-BACKUP-02-snapshot-closure | 删除历史Blob后完整检查ok，物化rejected | A-06需按记录类型核对真实来源闭包 |
| A-MAINTENANCE-02-jsonl-reference | 永久JSONL仍引用的临时材料被实际回收 | A-07需纳入JSONL/不可读引用/活动事实 |
| B-PREPARE-01-resolved-input-only | 请求载荷不变，返回blocked，原因needs_reprepare，失效resolved_input_digest | B-02闭合；解析观察不混进请求摘要 |
| B-MODEL-03-response-before-save | 合成password正文仍保存到generated_content | A-09/B-03保留落盘前过滤风险 |
| C-INVALIDATION-01 | 完成下游未失效 | C-01仍需修复 |
| C-DURABILITY-01 | 流fsync0/清单fsync1，游标durable=true、offset17 | C-07仍需修复；不是硬件掉电实验 |
| D-DECISION-01 | B级非关键缺口时passed | D-01闭合 |
| D-COVERAGE-01 | 复用而未核验的集合可接受 | 原额外R⊆V约束已移除，不再列待核对；实际复用仍需合法事实 |
| D-ISSUE-01 | 循环归并在返回前拒绝 | D-03对应部分闭合 |
| D-ISSUE-02 | 重开保留P1且阻塞集合含issue | D-03对应部分闭合 |

A-01端口签名/返回与序号口径另由源码、新增`test_a_a01_frozen_ports.py`及当前全量结果复核；本轮没有擅自冻结CORE-001/AB-001。A-08的Blob fsync、A-09自定义projector底线、A-10连接事实保存同样只移出已实现部分，实际来源/模型/产品验收工作保留。

## 3. 重放方法

仓库根目录：

```powershell
uv run --no-sync python scripts/check_versions.py
uv run --no-sync ruff check .
uv run --no-sync mypy
uv run --no-sync python scripts/generate_schemas.py
git diff --exit-code -- src/aitest/contracts/schemas
uv run --no-sync pytest --continue-on-collection-errors --junitxml=.git/develop-recheck-20261002/pytest.xml
uv run --no-sync python -m docs.validation.p1-audit-20261002.probes
```

在`src/aitest/resources/panel`执行`npm.cmd run build`、`npm.cmd test`；在`integrations/trae`执行`npm.cmd run package`。探针只向stdout打印观察，不覆盖旧证据。它使用测试工厂/内存模型和一次性临时工作空间；为验证回收资格，只删除探针自己创建的合成临时材料，不操作真实用户业务数据。模型反例只用固定合成password文本，结果只保存是否落盘的布尔值。

本轮未重新构建/烟测wheel，也未执行真实模型、业务系统独立核验、完整Trae用户流程、掉电、永久留存或安全portable产品验收。Junit与本机Playwright截图/trace在未跟踪的诊断/构建目录，提交JSON保存摘要；CI结果链接对应准确源码提交。

## 4. 前次清单维护

当前25项未闭合任务（A7/B5/C7/D6）。移出A-01、A-04、B-02、D-01、D-03；其余部分完成条目只保留剩余问题，新增D-08静态测试失败，原编号不重排。它不是25个已确认缺陷或产品完成百分比。历史修改日志、10月1日JSON和Git记录继续保留；现行接口及35项真实AC状态未在本轮更改。

## 5. 本次续查（保留现有改动）

源码仍为9bd4337，现有未提交文档先备份到`.git/docs-review-followup-20261002/`。重读实际源码、项目分册和A/B/C/D实施计划，五项关闭结论保持；本次新增B-10，同入参/同出站请求号调用模型2次、保存2份草稿、记录到修订4。C-01补同计划重试与传递末端反例，同时纠正“直接控制依赖未实现”的描述。当前26项待办（A7/B6/C7/D6），编号不重排；第4节25项为前次快照。

本次版本/ruff/mypy通过（136个分析文件），7份Schema再生成无差异；全量pytest1148 passed、2 skipped、0 failed/error（33.54s）。跳过仍为两项符号链接权限不足。面板build、候选6文件VSIX打包通过；Playwright仍1 failed（标题3/4），后续断言未执行。14项原观察重放一致，新探针增加3项观察；直接控制依赖确实失效，无依赖分支保留。

新增探针在根目录执行：`uv run --no-sync python -m docs.validation.p1-audit-20261002.followup_probes`。全部使用固定合成材料、内存模型或一次性临时工作空间；没有真实供应方请求或用户业务数据。JUnit在`.git/docs-review-followup-20261002/pytest.xml`，提交的续查JSON保存摘要与方法。前次JSON与修改日志不覆盖。远端CI沿用原准确基线证据，未重新查询；产品AC登记、源码/测试/生成Schema、项目规范和接口状态未更改。

## 6. 本次同基线深查（10月3日收敛，当前结论）

第1—5节保留前次快照。本次材料：[JSON](deep-audit-9bd4337.json) · [脚本](deep_probes.py) · [覆盖清单](deep-review-inventory.json) · [日志](../../修改日志/袁/2026-10-03-一期源码深查与新增问题.md)。源码仍9bd4337，仅更新现状文档/证据。

OCR可审目标118，本轮19定向复核/99明确跳过，占比16.10%，逐项记录范围/原因；不是全仓/整文件逐字覆盖。404个总变化文件、286排除另列，无外部审查LLM/子代理。

| 问题/证据键 | 断言核对的实际结果 |
| --- | --- |
| A-12 / A-UOW-01-failed-commit-retry | commit异常释放锁后仍保留暂存/身份，无锁重试被接受；另一UOW持锁的交错提交均序号1，已成功返回的case-a记录丢失 |
| A-13 / A-MODEL-01-redirect-authorization | 默认urllib跟随302，把Authorization从已确认HTTPS端点转给未确认的另一域，并接受其草稿为ok |
| A-11 / A-OWNERSHIP-01-cross-project-lineage | B正式save_case经A真实端口，将project-a/case-1@1以project-b、expected_revision=1追加为@2，稳定记录链混入两个项目 |
| C-08 / C-CAPTURE-01-parent-exit-before-stream-seal | 父退出/后台子进程持流时collect仍报complete；返回时2个reader未结束、脱敏摘要0份、输出0字节，探针清理后才封口8字节 |
| C-09 / C-PROCESS-01-windows-job-access | Windows OpenProcess缺PROCESS_TERMINATE(0x0001)，原Job绑定None/错误5；同一临时进程仅在内存补该权限位后成功 |
| B-11 / B-REVISION-01-payload-storage | 合法Case.revision=9被保存为仓储修订1、2；准确读取@1/@2均返回Case@9，正文与记录修订不一致 |
| A-14 / A-PROTOCOL-01-transaction-identity | LocalAPI begin后同request_id的commit指纹冲突，异号commit/rollback不属于事务，仍active；内部B直接UOW路径可以收尾 |
| A-06 / A-RECOVERY-01-rejected-restore | 越界清单被底层拒绝（rejected/verified=false），目标未创建；恢复编排却报repaired/integrity_ok=true |

执行`uv run --no-sync python -m docs.validation.p1-audit-20261002.deep_probes`；8项内部断言通过，新探针ruff通过。仅临时数据/进程和合成模型网络I/O；C-09核对[Microsoft权限合同](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)。无真实供应方/业务/Trae验收，未整改产品代码。

当前33项（A11/B7/C9/D6），D未确认新独立问题。旧JSON/日志原字节保留，文档校验在深查JSON；全量测试/构建/CI沿用第5节同基线实测，本次未重跑，AC登记不变。
