# 一期源码重核与实施增量（2026-10-15）

基线：fix@ac7ab44。用户授权本次代理依据现行一期项目文档代为作技术裁定并实施；没有代填真实验收、人员签名或历史缺失记录。

## 本轮实现

- FR10/AC30：新增纯函数独立预期比对。准确 Case 依据 `aitest.pure-function-basis/1` 提供 expected_facts，实际值只读当前 Attempt 完整、未过滤、摘要校验后的永久 stdout JSON。没有 HTTP/数据库/模型依赖，不重跑业务。
- FR02：正式提测可关联准确报告修订，冻结源码/报告摘要/执行快照和验收范围派生结果。验收项的全部关联用例必须属于 E∩V；复用、自述、单步骤通过不能填满范围。运行修订按准确链读取且保留原关联义务；交付视图复核材料，缺失时显示材料无效。业务结论与核验状态分列。
- 真实提测回读修复：业务正文没有记录级 revision，原交付视图从正文取值导致 `saved delivery view requires revision`；改为核对提测记录封套身份后取封套修订，并补真实保存→回读测试。准确报告关联后半段发现此问题，单纯 DTO 夹具此前未覆盖。
- FR09/17：面板新增已授权计划推进、暂停/恢复/取消及带报告引用的提测表单；交付读取页面分列自述/验证/业务结论，拒绝串记录结果。表单只准备命令，仍由核心受控入口提交，未知终态不自动重跑。
- 存储路径检查：完整链采样发现索引路径重复读取元数据；符号链接/junction 合并为每层一次实时 lstat，不缓存路径事实，权限错误继续阻塞。真实目录被替换成 junction 的反例保持拒绝。[7项检查](path-check.xml)与[微基准](path-benchmark.json)已保存；500次路径检查的中位数由0.299秒变为0.166秒；32个同级目录各50轮检查由1.075秒变为0.129秒，仅说明该检查，不声称整链同倍加速。
- 接口：DEC-015/016/017 已决定并回写；BC旧半程草案的集合方向修为 M⊆S，BD五项已有确认回复回写正文，取消重复等待。

## 检查记录

- [首批](first.xml)：79 项中 78 通过、1 失败。纯函数真实进程→核验→报告→ZIP→重启组件链通过。原测试预期“缺 HTTP 配置即无核验能力”，现在默认已有纯函数能力，缺运行应为 BUSINESS_VERIFICATION_BLOCKED；已补结构化错误与对应断言，后续回归另记。
- [交付投影首批](delivery-first.xml)：6 项通过。真实文件报告/证据读取、错源码/错项目拒绝及范围映射反例；正向 E/V 映射使用显式夹具，不冒充业务验收。
- [第二批](second.xml)：125 通过、1 失败。新增完整链动态导入的新测试助手，与当时已加载的旧助手参数不一致；该批不能作为最终通过。后续使用新进程验证。
- 完整链扩展的前两次夹具错误分别为遗漏用例前提、request_id/intent_id相同，均被核心合同拒绝；[前提失败](chain-fixture-failure.xml)、[身份失败](chain-identity-fixture-failure.xml)保留。
- 第三次扩展遇到测试助手重用同一人工手势，核心拒绝第二个动作；已改为每步独立事件，[原失败](chain-gesture-fixture-failure.xml)保留。
- [发布检查回执](final-release-commands.json)：ruff、strict mypy 258 文件、版本/锁文件/Schema、宿主25、浏览器19、面板/VSIX/wheel构建、制品校验、隔离安装均退出0。[静态首轮](ruff-first.log)两处超长行已换行修正。
- [事实清单](facts.json)、[全仓源码与测试指纹](source-inventory.json)、[wheel字节对照](wheel-source-parity.json)：244核心模块，365 Python测试文件，5571节点仅收集；没有运行当前全量。
- [文档检查](document-checks.json)及[整改目录逐文件处置](document-audit.json)记录链接、表格、版本/FR/AC和保留历史范围。

## 证据边界

真实 Trae 点击与人工确认、用户业务部署、物理掉电和一期完整验收未验证。全量未运行。首批纯函数链只执行一个步骤，报告如实 incomplete。最终扩展链执行全部声明步骤，其结果另记；所有链的确认事件仍使用夹具，不以样例完整链冒充整项目全部完成。

完整四步链的首轮结果为 20 通过 / 1 失败：[原始回执](chain-source-proof-failure.xml)。四个实际执行与四个输出比对成功，但预期报告 passed 的断言失败；默认只有执行前探针，缺业务进程实际加载来源，报告正确保留 incomplete。本轮没有消除此实现缺口。后续回归将检查该缺口继续贯穿报告、关联提测、导出与重启，不能将回归通过解释为来源缺口关闭。

下一批 [164通过/1失败](chain-approval-fixture-failure.xml)：旧审批助手写死 project，而完整链是 project-ticket，核心拒绝跨项目材料。助手改为命令的准确项目；继续运行保存的报告时发现上面的封套修订缺陷并修复。[四步来源诊断](source-proof-gap.json)保存原报告与四个来源观察，不以输出匹配替代来源证明。

该控制流程夹具沿用工单模板和用例标识，把实际动作改为纯函数输出比较；报告同时保留 `source_identity_unverified` 与 `critical_path_missing`，并不是完整工单业务或完整 Python 库验收。修复后的[保存现场续跑](continuation.json)证明提测视图、ZIP 和重启回读，后续空工作空间回归另列。

路径扩大回归分别保留[45通过/1失败](path-budget-first.xml)和[143通过/1失败](path-old-hook-failure.xml)：两处旧测试监听已被替换的内部调用，已改为检查实际路径检查函数及次数；真实 junction 替换、权限错误和跨调用重查仍单独覆盖。

完整链及存储边界 166 项通过；受影响既有路径/合同/架构/验收台账 520 项通过。两个批次分别记录，不与历史重叠结果相加为全量。[完整链/存储回执](chain-and-path-final.xml)、[既有回归](existing-final.xml)、[准确回归命令](existing-command.json)、[汇总](final-summary.json)。完整链证明流程与诚实降级，不证明 actual_execution_load_source_unverified 已修；实际加载来源仍欠。最终发布检查已在存储修复后重跑，wheel/源码字节一致。修改前文档可从 ac7ab44 或本机 `.runtime/phase-one-docs-ac7ab44.zip` 恢复。
