# ABC剩余问题验证材料

取证基线fix/1289e5b640a0312ae2386b93c656ddf6063abccf。组件检查与真实AC分开记录。逐项根因、源码、结果及剩余边界见[证据清单](../../ABC剩余问题修复证据-2026-10-04.md)。

## 不可变阶段

- `history/object-reference-incremental-startup/manifest.json`固定首阶段325份工程源码、26个AST函数/行号、8份原始日志及source ZIP。该阶段不含后续epoch/清单/来源/准备/确认修改，不能作为当前全量证明。
- [canonical-source-preparation-2065/manifest.json](history/canonical-source-preparation-2065/manifest.json)固定第二阶段342份工程文件、1032个AST符号/行号和58份原始日志/制品核对记录。完整Python2065 passed/2 skipped（627.73秒），Ruff、Mypy160文件、Schema/版本、wheel/sdist构建和隔离wheel冒烟通过；档案与当前工程字节核对通过。
- [source-closure-initial-registration-2083/manifest.json](history/source-closure-initial-registration-2083/manifest.json)固定后续源码blob与初始登记阶段。全量2083 passed/1 failed/2 skipped（866.47秒）；失败测试在运行时加载了更正前的“传输重试作为新准备”夹具，pytest显示的源码可能是随后更正版本，不能据回溯文字声称更正后夹具仍失败。实际更正单项1 passed，全部新增19项204.42秒通过。工程Ruff、Mypy162文件、生成物/版本、构建/隔离冒烟及现有VSIX面板字节核对通过；不改写为全量2084通过。
- 后续修复另建独立阶段，原档案不覆写。每阶段清单记录源码成员原始/换行规范化SHA256、AST符号行号、日志SHA256与准确结果；不以当前修改后的行号替代当时源码。
- [consumed-source-closure-2092/manifest.json](history/consumed-source-closure-2092/manifest.json)固定准备/初始登记实际消费历史源码的提交闭包：347份工程文件、1087个AST符号、101份原始记录。完整2092 passed/2 skipped（947.00秒），Ruff、Mypy162文件、Schema/版本、构建/隔离wheel冒烟及现有VSIX面板字节核对通过。模型依据四个新反例在此版本仍未修复，后续另建阶段。
- [model-basis-safe-receipt-2125/manifest.json](history/model-basis-safe-receipt-2125/manifest.json)固定模型准确依据、迟到响应与安全旁录阶段，基线为已推送d53f6d5。351份工程文件、407个AST符号、131份原始记录；完整2125 passed/2 skipped（1082.92秒），专项414 passed，Ruff、Mypy164文件、Schema/0.4.0版本、构建/隔离wheel冒烟及现有VSIX面板字节核对通过，档案/当前工程核对通过。未发布响应受控恢复、完整实际依赖和真实产品验收仍缺；其后运行事实身份7个实际反例尚未在此版本修复，重复步骤对照已被现有规则拒绝。此次全量于2026-10-05跨日完成。
- [runtime-facts-identity-287/manifest.json](history/runtime-facts-identity-287/manifest.json)固定2026-10-05的后续事实身份守卫，基线为已推送bcdb0d0。352份工程文件、7个本次AST符号、146份原始记录（含原探针源码）；受影响回归/全部合同/架构287 passed，原探针8 passed，Ruff/Mypy164文件、生成物/版本、构建/隔离wheel冒烟及制品核对通过，档案/当前工程核对通过。本局部增量未再跑全量，2125项全量只属于上一351文件版本。档案如实保留首次错误文本匹配失败及文档函数链接L52误标；源码正确行号为53，更正文档后166个本地链接及源码符号核对通过，独立日志未覆写原记录。

## 重复验证

```powershell
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/object-reference-incremental-startup/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/canonical-source-preparation-2065/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/source-closure-initial-registration-2083/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/consumed-source-closure-2092/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/model-basis-safe-receipt-2125/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/runtime-facts-identity-287/manifest.json
.venv/Scripts/python.exe -m pytest
.venv/Scripts/ruff.exe check .
.venv/Scripts/mypy.exe
.venv/Scripts/python.exe scripts/generate_schemas.py
.venv/Scripts/python.exe scripts/check_versions.py
uv build
.venv/Scripts/python.exe scripts/smoke_wheel.py
```

`--against-checkout`只用于核对与该阶段相同的工程源码；后续继续修改时应仅核对不可变档案，差异是正常的新阶段边界。

## 原始记录

`*-before*.log`记录实际反例；部分初次测试写错临时夹具调用/路径，名称明确保留，后续`*-corrected`/`*-behavior`记录纠正后的实际输入。`full-pytest-current.log`为首轮2028 passed/37 failed/2 skipped，`full-regression-repairs.log`为修正真实对象/权威材料后的150 passed，`full-pytest-corrected.log`为第二轮2065 passed/2 skipped。不能把没有收集到测试、夹具错误或历史档案格式错误写成工程测试通过。

静态检查、构建与wheel冒烟各有单独日志。所有真实AC状态未改变；不宣称本轮真实Trae、真实人工页面、物理掉电或供应方故障已经通过。

- [model-response-resolution-2177/manifest.json](history/model-response-resolution-2177/manifest.json)：本轮完整Python为2177 passed、2 skipped（1298.72秒），包括全部27个新增补登记节点。Ruff、Mypy165文件、Schema/0.4.0版本、wheel/sdist、隔离wheel冒烟及现有VSIX面板字节核对通过。冻结354份工程文件、147个AST符号与170份原始记录；源码ZIP SHA256为`1b776098d8b2c559923a0cb4f87cc302bedda7d08816172436d8179af469dd06`，档案及当时工作区核对通过。原2项反例、fixture导入/关键字参数错误和首次中断全量均原样保留；未知维护活动标记、完整依赖/执行链及真实验收仍缺。

- [recovery-marker-authority-428/manifest.json](history/recovery-marker-authority-428/manifest.json)：最终428 passed（132.74秒），涵盖27项新增活动核对及受影响恢复/写锁/安全/合同/架构；Ruff、Mypy165文件、Schema/0.4.0版本、wheel/sdist、隔离wheel冒烟和现有VSIX面板字节核对通过。本局部阶段未再跑完整Python；2177全量只属于前一模型阶段。冻结355份工程文件、85个AST符号及12份原始记录，源码ZIP SHA256为`0fc405ce13a71b58d638bb21bf84dfa013bfb79dd5d58768891b83a0a1aacdea`；档案与当前工程核对通过。原8项失败与中间结果保留，C活动恢复和旧版事件权威对照仍缺。

- [legacy-event-authority-full-failed-2225/manifest.json](history/legacy-event-authority-full-failed-2225/manifest.json)：首轮完整2225 passed/1 failed/2 skipped，356文件/115符号/28记录，源码ZIP SHA256为`4bad17b875d555b7f6647044bc2a3101435564ff1fba980abc7f20c988e86e39`。失败夹具尚未纠正时的源码保留，不伪写全绿。

- [legacy-event-authority-corrected-151/manifest.json](history/legacy-event-authority-corrected-151/manifest.json)：纠正旧夹具后受影响回归为151 passed in 191.16s (0:03:11)，包含27项活动标记、22项事件新增节点及维护/恢复/架构。首轮完整Python为2225 passed/1 failed/2 skipped（1844.57秒）；失败是旧回收协作夹具仅凭序号期待删除未知活动，已改成真实已提交事务正例和未知活动保留负例。失败全量的准确源码单独封存，未改写为2226通过；本夹具修订后未再跑完整Python。Ruff、Mypy165文件、Schema/版本、构建、隔离wheel与面板字节核对通过。冻结356份工程文件、133个AST符号和30份原始记录，源码ZIP SHA256为`1bef77a967e03336a913c1d1fc167d82ca1d111b61d03992e9aefb71c350473c`；档案逐项及当前工程核对通过。原10个权威失败和后续6个材料失败均保留；原始98/5夹具错误与103中间通过结果未改写。补充3种真实稀疏文件预算探针全部拒绝正文读取。C实际活动、完整运行中修订持久链、真实环境/掉电仍缺。

- [runtime-reference-recovery-structure-449/manifest.json](history/runtime-reference-recovery-structure-449/manifest.json)：最终受影响回归449 passed（397.87秒），包括全部合同/架构、运行事实/修订、事件/活动恢复、维护、初始登记；73项门禁也独立通过。Ruff、Mypy165文件、Schema/0.4.0版本、wheel/sdist、隔离wheel与现有VSIX面板字节核对通过。本小增量未再跑完整Python，原2225/1失败/2跳过只属于已封存的失败快照，旧夹具已在151项阶段修正验证。冻结356份工程文件、125个AST符号和22份记录，源码ZIP SHA256为`49db7b94750aec66a5e4e4bd1e0c1b368b0238abb9ad19aad0bbaeee02bd9ee5`；原字节/符号及当前源码核对通过。原4个正文/仓储误判和5个未处理结构异常保留，新参数用例8/6项均进入最终449项。本阶段不借前一全量作为当前全绿证明。

- [saved-runtime-hidden-plan-failed-1/manifest.json](history/saved-runtime-hidden-plan-failed-1/manifest.json)：权威评估初版遗漏公开投影之外的expected_plan_revision_ref，真实保存的错误计划检查点仍被接受，1 failed（46.91秒）。358文件/169符号/7记录，ZIP SHA256为`56597756921deb2de71b567f0143a6164d98345521ffb576880738a68b10a1c7`；修复前档案及当时工程核对通过，不能改写为后续通过版本。其他正文替代/发布字段前后探针分开保存，未新增实际AC证据。

- [saved-runtime-assessment-frozen-publication-161/manifest.json](history/saved-runtime-assessment-frozen-publication-161/manifest.json)：最后161项执行/仓储/控制/认领/检查点/发布（543.42秒）、226项合同/架构/提交（17.63秒）、70项领域/事实（0.34秒）及1项真实正文7/仓储@2正负向（37.34秒），四组不重合，共458项通过。359文件/182符号/45记录，ZIP SHA256为`4852bd4ec929785664ac00549f27746303e4cee2e2108b3a881f8662d55af421`；档案/当前工程核对通过。Ruff/Mypy166、Schema、0.4.0、最新构建/隔离wheel及现有VSIX面板字节核对通过。本增量未再跑完整Python，早期26/336/338先于最后步骤守卫，更正失败也原样保留。只读评估不保存或授权；持久修订/实际C消费及真实验收仍缺，22项产品条件保留。

- [frozen-run-admission-failed-25/manifest.json](history/frozen-run-admission-failed-25/manifest.json)：普通快照改写driver/S/源码/序列/coverage及矛盾序列的原26项边界检查为25 failed/1 passed；361份工程文件、8个符号、3份日志，ZIP SHA256为`83078ed71089c98f09968bd73be17afe7a3096b94ac4dfb127c08f6965597f2b`。真文件测试初次缺依赖fixture及提交结束后的cleanup错误另作夹具历史记录，一次无输出补查中断，不计测试通过。

- [frozen-run-admission-349/manifest.json](history/frozen-run-admission-349/manifest.json)：342项合同/架构/规则/发布加7项真实文件事务，共349项通过；Ruff/Mypy166、Schema无差异、0.4.0、构建、隔离Python3.13.13 wheel及现有VSIX面板字节核对通过，未重建VSIX。361份工程文件、42个符号、13份记录，ZIP SHA256为`ed31cc74721eff3e6511f07551c8bdf5521d9660e0b0e5b5679f05fdf0577e8a`；与当前工程逐项核对通过。当时完整Python正在单独执行，后续中断/失败/最终完成结果分别另存，不把中途输出写成通过。实际DriverChange/来源核验/持久运行修订及真实AC仍缺。

- [frozen-run-full-failed-2329/manifest.json](history/frozen-run-full-failed-2329/manifest.json)：第二轮完整实际1 failed/2329 passed/2 skipped/2 errors（2273.98秒），361文件/33符号/30记录，ZIP SHA256 `19d4725628d21b4ea7c44b638d701f86726b5dd8f785359922f88975d50f002e`。旧不可读序列夹具经普通发布被新守卫拒绝，又未回滚；后续setup出现写队列忙。准确失败源码和当时日志封存，不改写为通过。
- [frozen-run-lock-owner-failed-3/manifest.json](history/frozen-run-lock-owner-failed-3/manifest.json)：追加五项探针真实复现工作空间A先退出时抹掉仍活动的B归属，3 failed/2 passed（7.26秒）；362文件/37符号/31记录，ZIP SHA256 `bc9c7b87df024e68c73aad2c7729961af65d9a0974cee3da14ab5233cb930e29`。普通OS锁、生命周期锁及真实两个UOW均复现，反向结束对照通过；新五项未参加前一完整2329阶段。
- [frozen-run-lock-and-sequence-7/manifest.json](history/frozen-run-lock-and-sequence-7/manifest.json)：锁及旧材料/外来运行专项7 passed（51.60秒），早于两处纯测试格式修正；修后工程362文件/79符号/41记录，ZIP SHA256 `d1420e0a61c14dddaecf7ba6250c89d242e30ff93befadb36f3faa5408ebc694`，档案与当前工程核对通过。Ruff/Mypy166、最终构建/隔离wheel与制品字节通过，当时最新完整2338节点运行中，活动全量日志未收入本阶段；最终完成见下节准确源码。

## 完整回归最终登记（2026-10-05）

- [frozen-run-current-errors-failed-2/manifest.json](history/frozen-run-current-errors-failed-2/manifest.json)：首次全量中两项当前Attempt回退/删除检查被新增冻结守卫抢先拒绝，独立复现2 failed in 3.08s；原测试不修改。361文件/0本次变更符号/17记录（源码与当时HEAD一致，但全份工程字节仍冻结），ZIP SHA256 `e463a60e9ce47fd5ac2d742acd422fbc985524cef823b5cea1bc6a27d3233b0e`；首轮全量中断，不登记完整结果。
- [frozen-run-full-final-2336/manifest.json](history/frozen-run-full-final-2336/manifest.json)：恢复准确当前引用优先级后的最终完整Python**2336 passed, 2 skipped in 2253.18s (0:37:33)**，两项本机Windows符号链接权限跳过；362文件/79符号/49记录，ZIP SHA256 `b89a959dcf5cc2ad06b08abfa0139689a1cee2d3be39d9dc92e5a5d358a6b533`。档案/当时工程核对通过，最终Ruff/Mypy166、Schema/版本、构建/隔离wheel和C模块/共享面板字节核对通过；未重建VSIX，不补真实AC或整项关闭22项产品条件。

```powershell
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/frozen-run-full-final-2336/manifest.json --against-checkout
.venv/Scripts/python.exe -u -m pytest -o addopts='' -q --tb=short -ra
```

后续源码修改后，旧阶段只能检查不可变档案自身，不把旧全量作为新工程已通过证明。

## 跨线程析构归属后续修复（2026-10-05）

- [frozen-run-foreign-cleanup-310/manifest.json](history/frozen-run-foreign-cleanup-310/manifest.json)：最新受影响回归**310 passed in 77.69s (0:01:17)**，包括全部合同/架构和八个锁/业务守卫节点；362文件/21符号/64记录，ZIP SHA256 `122da32bf6ff3098a8c4bfcb7f3a9e5ce1be16cdd7c483fe4874b87eb78e8229`，档案/当前工程核对通过。Ruff/Mypy166、最新wheel/sdist、隔离Python3.13.13及制品模块/共享面板字节核对通过，未重建VSIX。
- 首轮309 passed/1 failed为新测试误用repo.pending，准确原测试源与失败日志保存，改为unit.pending后整组重跑。原外部2项反例属于4396824完整源码，后续正式测试已进入310项；原探针由拒绝重取变为正常取得/释放。
- 本后续修补未再跑完整Python；当前2341节点仅收集成功，2336/2完整结果仍只属于4396824。既有20份档案自身完整性通过；真实AC和22项产品条件未改。

```powershell
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/frozen-run-foreign-cleanup-310/manifest.json --against-checkout
.venv/Scripts/python.exe -m pytest tests/unit/test_workspace_lock_ownership.py -o addopts='' -q --tb=short
```

后续源码变化后旧档案仅核对自身，不能将旧完整结果作为新源码已全部通过证明。


## 运行修订保留尝试历史与权威回读（2026-10-05）

[详细根因/源码/命令](../../修改日志/袁/2026-10-05-运行修订保留尝试历史与权威回读.md)。

- [修前37项](history/runtime-attempt-history-failed-37/manifest.json)：37失败/9通过；363文件/7符号/3记录，原漏夹具导入的3错误单独保留。
- [历史权威修前5项](history/runtime-attempt-history-authority-failed-5/manifest.json)：5失败/1通过；364文件/40符号/6记录。已有前两项修补，历史检查点仍被跳过。
- [旧视图断言失败](history/runtime-attempt-history-view-fixture-failed-121/manifest.json)：最终组件121通过/1失败；全部新52节点通过，旧内部全等断言尚未新增历史值核对；364文件/73符号/16记录。
- [最终394项](history/runtime-attempt-history-final-394/manifest.json)：122 passed in 231.95s加272 passed/23 deselected in 394.38s，互不重叠共394；364文件/78符号/27记录，ZIP SHA256 `771d20fb04601624a26396c000ffce7aaa04165f0145bc66fec4aa39faa5258f`。Ruff/Mypy166、Schema/版本、构建/隔离wheel、三模块制品及现有VSIX共享面板通过；24份原档案完整性通过，159处同名源码锚点已刷新，未覆写历史结果。

```powershell
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/runtime-attempt-history-final-394/manifest.json --against-checkout
.venv/Scripts/python.exe -m pytest tests/unit/test_runtime_revision_attempt_history.py tests/unit/test_runtime_revision_history_authority.py -o addopts='' -q --tb=short
```

2393仅收集，未重跑完整Python，2336/2完整结果仍只属于4396824；真实AC和22项产品条件保持开放，持久修订/实际C/D消费继续按整改计划实施。后续源码变化后旧档案只核对自身。

初始完整步骤内容保存与准确回读基础已补，丢响应按原意图找回原材料；源码、反例与本轮验证见[逐项记录](../../修改日志/袁/2026-10-05-步骤内容原子保存与准确回读.md)。继续贯通持久运行修订、实际执行与默认消费，真实AC不代填。

持久运行修订与历史消费后续阶段（2026-10-05）：内部序列/有效内容、锁内守卫与实际保存消费闭包已补，另修历史中间节点漏读、旧尝试误伤当前分支及状态重投影丢脱敏详情。逐项根因、源码、原始反例和本轮验证见[整改记录](../../修改日志/袁/2026-10-05-持久运行修订与历史消费闭包.md)。默认可信入口、真实活动/来源/授权/复用及产品验收继续实施，22项产品条件仍开放。

受控依据确认后续阶段（2026-10-05）：已修默认confirm_basis仅凭入口标签保存确认、确认来源未回读及缓存跨角色回用；补准确一次性挑战/交互与业务确认同事务，旧无来源材料阻塞，46项门禁通过。源码、反例和剩余发布/授权/宿主/CLI边界见[逐项日志](../../修改日志/袁/2026-10-05-受控依据确认与一次性挑战.md)。真实AC与22项产品条件仍保留，继续实施。

本阶段最终**337 passed**（46门禁/160.80秒+291回归/1133.98秒，无重叠），Mypy158及Ruff/Schema/版本/构建/12模块制品/隔离wheel通过；23文档987链接在封存前通过。见[准确阶段](history/approval-origin-final-337/manifest.json)，380文件/285符号/17记录，ZIP `4eb99c5eab17e3b1ef426ed9b5df162adb1597782a467c4c5966966c7740c1e4`；当前真实AC未变、完整Python未重跑。

确认撤销与通道收尾后续阶段（2026-10-05）：已修同一撤销意图改目标、来源读取异常/布尔修订及连接退出漏收尾；挑战撤销回执同提交，单会话最多16个未用挑战，失败保留重试依据。当前170项互不重叠的受影响检查通过；准确源码、原五项失败和边界见[逐项记录](../../修改日志/袁/2026-10-05-确认撤销幂等与通道收尾.md)。实际人工事件/其他发布动作、授权及默认C/D继续修复，22项产品条件与真实AC保留。

最终[不可变源码和原始输出](history/approval-revocation-final-170/manifest.json)已封存并against-checkout核对通过：383文件/333符号/16记录，ZIP SHA256 `99efcf9a5830d7dbf1c5b6956fad8111f10e85dffd0eae15bf890ad7fccb9609`。24份文档1010个本地链接及源码AST行号通过；后续改动后只核对旧档案自身，不将170项结果转用于新源码。

事务来源与安全出口后续修复（2026-10-05）：已修同名其他入口接管事务、结束句柄/项目省略被代填、失败begin误登记及Response/标量跳过投影；默认非交互关闭不触碰人工挑战。306项受影响检查通过，原17项及扩展两处失败/逐项源码见[详细记录](../../修改日志/袁/2026-10-05-事务准确来源与响应安全出口.md)。其余人工发布/授权、默认C/D及真实AC继续实施，22项产品条件保留。

最终[准确源码与原始输出](history/transaction-origin-final-306/manifest.json)已封存并against-checkout核对通过：384文件/369符号/17记录，ZIP `19f36f4d0145af633d3d4606c88d430b5165cce60b1cadb78ad2cbd65a9ce7eb`；25份文档1029链接/源码AST行号通过。后续源码变化后该档案仅核对自身。

模型策略后续修复（2026-10-05）：默认策略保存已要求准确一次性人工来源，确认消费/策略/原意图六记录同提交；发送前回读完整冻结证明，旧无来源策略阻塞。另修回执错指合法策略和配置变化破坏历史回读。最终333项受影响检查通过，原始反例、源码及边界见[逐项记录](../../修改日志/袁/2026-10-05-模型策略受控确认与发送来源.md)。其他人工发布、源码绑定可信消费、授权/复用及默认C/D仍继续，22项产品条件与真实AC保留。

绑定来源后续修复（2026-10-05）：默认save_binding已要求准确一次性交互，来源消费/绑定/效果回执六记录同提交；固定前、发布锁内、check_source和prepare_run均回读同一准确控制证明，旧confirmed布尔不能升级。原意图只回原修订、换输入冲突；417项受影响检查通过。根因、源码和故障证据见[逐项记录](../../修改日志/袁/2026-10-05-绑定受控保存与来源消费.md)。该绑定证明不授予实际执行/外发；规则/计划发布、授权/复用、实际来源/活动及默认C/D继续，22项产品条件与真实AC保留。

规则计划发布后续修复（2026-10-05）：默认publish_rules/publish_plan已要求准确受控交互，来源/正文/效果六记录同提交；规范计划正文比对准确Case/Scope，规则及B准备核对发布证明。另修必需缺口改标非阻塞和过滤后发布不同冻结正文。当前517项通过、2项跳过；原反例、源码与命令见[逐项记录](../../修改日志/袁/2026-10-05-规则计划受控发布与准确消费.md)。C初始登记的持续证明消费、未用授权/复用及默认C/D继续修复；22项产品条件和真实AC仍保留。

初始运行与运行修订来源后续修复（2026-10-06）：新登记核对准确B准备原回执、持续人工来源与锁内DTO重读；原回读核对Run/Step/完整内容。运行修订准确消费受控计划/规则/确认，完整记录摘要与业务正文分别校验。382项互不重复的受影响检查通过，原失败和逐项源码见[详细记录](../../修改日志/袁/2026-10-05-初始运行准确准备与持续来源消费.md)。环境解析/不隔离确认与未用授权仍有已复现缺口，22项产品条件与真实AC保留。

非默认隔离环境后续修复（2026-10-06）：已拒绝客户端布尔自签，确认/环境/效果六记录同提交；B准备/C新登记及运行修订回读准确证明，普通venv声明保持原流程。主工作区355项受影响检查通过，原反例、六保存点失败/重试及源码见[详细证据](../../修改日志/袁/2026-10-06-非默认隔离环境受控确认与持续消费.md)。实际环境解析、独立未用授权及真实验收继续，22项整项条件仍开放。

可信环境解析后续修复（2026-10-06）：核心显式登记实际载体，准备覆盖客户端解析声明，准确冻结解释器、依赖内容与声明摘要；新C初始登记事务外重探，原结果回读保留。最后源码重新运行36项通过，另181项保留原阶段结果并核对测试AST/产品字节，不称fresh217；故障阶段、准确源码及复跑命令见[详细记录](../../修改日志/袁/2026-10-06-可信环境解析与准备冻结.md)。实际业务加载、每步来源守卫、独立未用授权及默认C/D仍继续，22项整项产品条件和真实AC保留。

独立执行授权来源组件（2026-10-06）：已补可信保存动作、准确确认与原授权/未用状态六记录批次、占用/撤销及历史原文核对；当前源码的223项合同、16项范围/占用证明门禁及Ruff/Mypy183、Schema/版本/八模块制品检查通过。29项授权专项及103项登记/步骤/审批回归仍在执行，不计为已通过；旧十项服务结果仅属于已封存初版。实际SerialRunner强制消费尚未接入，原启动反例与未用授权/复用及真实AC仍开放；整项剩余A7/B8/C7，共22项，本次整项关闭0项。根因、源码、失败阶段和准确范围见[整改日志](../../修改日志/袁/2026-10-06-独立执行授权原始来源.md)。

授权来源最终355项版与独立预授权失败版分别封存：`history/authorization-origin-final-355/manifest.json`证明29/103/223三个互不重复组已完成；`history/authorization-origin-pregranted-failed-1/manifest.json`证明第二步原始未用授权被无关全局进度误拒，1 failed。二者准确产品源码均基于21e8e462，新增反例不借355项通过隐藏。历史239检查点不改写；后续修复在独立工作树进行，整项剩余22、真实AC verified 0。

2026-10-07：默认受控运行修订/独立驱动收窄及准确原确认消费已接通；采集/恢复共用可靠连续前缀游标，原4+3项反例已修。最终2+147项检查通过，扩展修订组仍运行不计通过；ABC仍21项。见[根因/源码/精简验证](../../修改日志/袁/2026-10-07-默认运行修订与可靠采集恢复位置.md)。

2026-10-07：活动命令现返回准确已保存前缀，默认有界执行/原句柄观察同步采集，非法位置不回running；126项检查通过，包含真实大输出长命令的默认API暂停续采/取消，ABC仍21项。见[根因/源码/精简证据](../../修改日志/袁/2026-10-07-活动输出前缀与默认续采.md)。

2026-10-07：失效Attempt后续自然退出/停止/采集错误保持invalidated，准确材料仍可保存；107项回归通过，ABC仍21项。见[根因/源码/精简证据](../../修改日志/袁/2026-10-07-失效尝试后续采集不恢复旧依据.md)。

2026-10-07：无可靠退出的旧终态继续原观察，错误/失效活动步骤下个切片可恢复且阻止新调度；异常失效同样保存准确gap，不恢复旧依据。当前93项及独立默认API1项共94项通过；默认入口补存退出仍保留采集错误/gap，可靠终态不要求执行器；ABC仍21项。见[根因/源码/精简证据](../../修改日志/袁/2026-10-07-无可靠退出标签的原观察与串行边界.md)。

最终93项命令：pytest tests/unit/test_invalidated_observation_gap.py tests/unit/test_unverified_terminal_observation.py tests/unit/test_invalidated_capture_state.py tests/unit/test_serial_execution_slices.py tests/unit/test_serial_execution_loop.py tests/unit/test_serial_runner.py tests/unit/test_execution_observation_identity.py tests/unit/test_capture_completion_closure.py tests/unit/test_execution_control.py -k "not default_error_without_exit" -o addopts="" -q --tb=short -x；解释器为项目.venv Python3.13.13。

默认API最终独立命令：pytest tests/unit/test_unverified_terminal_observation.py::test_default_error_without_exit_continues_original_collection_and_keeps_gap -o addopts="" -q --tb=short；1 passed in 128.06s。与上列93项不重叠，共94项。

2026-10-07：默认start_run持久有界调度已接通，按原逐动作授权及共享领域依赖规则续行，保留原活动与控制；completed标签另核对可靠退出/完整采集/历史边界。当前88项互不重叠检查及wheel/sdist、隔离制品冒烟通过，后台保活/业务核验/复用与真实AC继续；ABC仍21项。见[根因/源码/精简证据](../../修改日志/袁/2026-10-07-默认整运行持久有界调度.md)。

2026-10-07：默认独立业务查询已保存安全实际JSON、证据/核验事实与原意图回执；重传/换核心只回原材料，边界变化只留历史，必需字段被过滤保持缺口。凭据字段名漏过滤同时修复；最新180项通过、2项跳过及构建/隔离wheel冒烟完成。五类生产配置、持久外部导入/最终一致性/交付派生验证与真实AC继续，ABC仍21项。见[根因/源码/精简证据](../../修改日志/袁/2026-10-07-独立业务查询持久材料与原意图回读.md)。

2026-10-07：默认外部导入已持久保存安全原材料、附件和原意图，同来源异内容冲突，重启回原记录；附件提交前丢失整体拒绝。122项通过、2项跳过及构建/隔离wheel验证完成。准确关联重算/生产核验/复用与真实AC继续，ABC仍21项。见[源码与精简证据](../../修改日志/袁/2026-10-07-持久外部导入与附件提交闭包.md)。

2026-10-07：核心退出已核对权威执行与实际保存材料，父进程丢失/停机不终止活动或未知执行；客户端仅凭准确回执登记stopping，修复监听漏唤醒与半帧无限读取。254项通过及构建/隔离wheel验证完成；有界活动库存/后台续行/真实宿主仍继续，ABC剩21项。见[根因与精简源码证据](../../修改日志/袁/2026-10-07-核心退出准入与准确停机回执.md)。

2026-10-07：默认submit_delivery已补正式提交的准确草稿/任务/固定源码/绑定与一次性人工来源，六记录同提交、丢回执/重启回原材料；版本标签不替代content_identity，全部验收项保持未验证。实际交付验证投影/面板及真实宿主仍继续，ABC剩21项。见[根因与精简源码证据](../../修改日志/袁/2026-10-07-正式交付冻结版本源码与人工来源.md)。

2026-10-07：源码原意图现核对原快照/准确指针及实际固定字节，SOURCE暂停可读原材料，缺失/损坏拒绝元数据成功；同字节新意图补实际发布闭包、只读同批视图和准确惰性读取。ABC整项仍21，真实AC不代填。见[根因与精简源码证据](../../修改日志/袁/2026-10-07-源码原意图材料回读与同批发布闭包.md)。

2026-10-07：业务源码快照现按完整冻结正文命名，同字节的逻辑/复取范围变更不再撞旧编号；所选路径排序去重，提交核对完整冻结范围。原编号/意图不迁移，content_identity保持原口径；ABC仍21。见[根因与精简源码证据](../../修改日志/袁/2026-10-07-源码快照业务身份与范围冻结.md)。

2026-10-07：仓储严格核对实际整数修订/水位、身份与材料形状，原意图回读逐项核对准确引用及原正文，拒绝类型转换与错误修订；166个不重复受影响节点通过，ABC仍21。见[根因与源码证据](../../修改日志/袁/2026-10-07-权威仓储修订类型与原回执核对.md)。

2026-10-07：有限查询严格核对已读叶页的物理键与实际摘要，仓储水合核对准确材料、项目与白名单摘要；错误整页转维护，不增加普通历史扫描。ABC仍21。见[根因与精简证据](../../修改日志/袁/2026-10-07-有限查询物理键与准确材料核对.md)。

2026-10-07：不可变树缓存隔离原输入/读出引用并保持实际JSON语义，拒绝重复字段/非JSON数值及超限读取；最终424项通过，ABC仍21。见[根因与源码证据](../../修改日志/袁/2026-10-07-不可变材料缓存与严格JSON读取.md)。
