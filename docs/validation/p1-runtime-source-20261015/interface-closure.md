# 六主题接口关闭核对

本次按用户要求关闭接口对接主题；产品验收仍由项目现状与各期 status.json 管理。接口关闭只覆盖现行提供/消费合同，不把供应方不可用、真实用户未测或一期产品范围未完成写成已验收。

## 提供与消费义务

| 合同 | 提供方/消费方实施入口 | 必查拒绝与兼容分支 | 当前证据 |
| --- | --- | --- | --- |
| CORE-001 | application/ports.py、file_store/uow、LocalAPI、B 注册表、C 协调器、D 查询/报告 | 同意图异输入、错误码、提交/事件配对、有限 QuerySpec、能力按动作降级 | 377 续验的合同/架构检查；公共 API 与 query 配对；真实完整样例链；关闭补测 |
| AB-001 | bootstrap、substrate_adapter、模型窄凭据适配、准备/发布/报告提测 | 准备摘要冲突、同提交保存、凭据正文不进入 B 类型、未配置模型不影响本地核心 | 16 登记/准备、377 续验；模型装配与 substrate 关闭补测 |
| BC-001 | PreparedRun、初始登记、运行修订读写、runner 授权/控制 | M/S 不弱化、当前/历史修订、步骤边界暂停、未核实来源不授予复用 | 首批通过的授权/运行修订路径；377 续验；14 半程夹具；控制/修订关闭补测 |
| AC-001 | StageableWorkspaceUnitOfWork、CheckpointPort/FileCheckpointStore、EvidenceObjectStore、C 提交与重启消费 | 发布原子性、坏引用/摘要、未知活动保留、备份/迁移拒绝与恢复边界 | 首批执行提交/回执检查、完整链重启；检查点/提交恢复/备份/迁移关闭补测 |
| BD-001 | CommittedViewReader、Run/Panel/Delivery DTO、read_view、Trae 桥与共享面板 | 只读不写、同一领域判定、准确修订、丢失材料降级、项目上下文关联 | 浏览器 19、宿主桥发布回执；视图/relay/交付关闭补测 |
| CD-001 | ExecutionFactsAssembler、公开/消费夹具、build_decision_facts、SavedReport/SavedExport | 独立 stdout/stderr、未知/超时、当前 Attempt/来源、失效、报告准确引用 | 377 续验全部合同；14 半程夹具；真实文件底座 D 消费关闭补测；完整 Python 报告/ZIP 实物 |

## 实际补齐

- 修复公共生成器使用已经移除的内部 expected_source_binding_digest 参数，恢复可重复生成。
- 生产装配器生成 running、pause_requested、paused、revision_invalidated 四份公共/消费快照；14 项检查覆盖 D 消费、历史保留、步骤边界、原八份生成值不变。
- 初始冻结断言引用、实际 Python 进程来源、永久证据与当前报告已接通；完整四用例样例为 passed / A，提测/导出/重启通过。
- 已验证验收项与整体业务结论分开，修正旧提测测试；整体 incomplete 的场景仍保持 incomplete。
- 六主题旧技术待答逐项处置，旧交付说明归档并提供准确当前交付说明。

## 仍属于产品验收的事项

以下继续保留在[项目现状](../../现状.md)、[四包检查入口](../../一期工程整改/README.md)，不因接口关闭删除或标成通过：真实 Trae/人工流程、真实模型供应方、实际用户半程修订、完整编辑向导、动态依赖观察/整用例复用资格、其他执行载体、物理掉电/跨机/完整迁移恢复。已有接口按真实能力返回缺口或拒绝，不为缺输入伪造成功。

若关闭补测发现必需方法缺失、DTO 无消费或拒绝分支失效，必须先修复，不能用本表把实现缺陷改名成验收事项。

## 证据状态

关闭补测 237 passed，退出 0，见 [JUnit](interface-closure.xml) 与[准确命令](interface-closure-command.json)。另有 14 项双侧半程夹具通过。六主题现行接口按最低 fixture_passed 关闭，产品实施/验收仍按上节保留。目录移动后的检查、包字节和最终代码提交单独登记。

可复核代码提交：`12007d56951d5453ed8fcaa6b18e54901e31f524`（fix）。移动后 181 项通过；wheel 重建、制品核对、隔离安装通过。当前规范分册与验收结果保持原样，仅变更证据路径。
