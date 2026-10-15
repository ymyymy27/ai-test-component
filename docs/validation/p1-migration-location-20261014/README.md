# 迁移备份独立位置配置续修

2026-10-15复核，代码在既有隔离树开发，原5427全量源码没有改动。该全量现在已登记外部中断：1705通过/2跳过、31.5%、无完整JUnit/退出回执，不再写运行中。本批以[事实](facts.json)和[602源码指纹](source-inventory.json)为准，5451仅收集。

## 已实现

可信装配新增可选migration_backup_root；未显式传入时读取宿主进程的AITEST_MIGRATION_BACKUP_ROOT。默认进程启动器继承环境，核心工作进程另有--migration-backup-root选项。配置不是公共Command参数，不新增业务动作或让relay代签；启动迁移与后续受控migrate使用同一份配置。

必须是工作空间外的绝对目录，拒绝文件、相对地址、目录本身/祖先/最终工作空间命名目录的链接；已知凭据出现在用户目录或派生目录时在创建/保存前拒绝。不同工作空间用稳定身份派生的独立目录，避免共享存储时同计划名碰撞。inspect不创建外部目录。

活动周期冻结实际backup_path和原材料摘要；重启时配置更换或取消，仍从原地址核对，副本缺失不重建。旧周期没有backup_path时按已知旧固定位置读取，不因新配置移动历史或谎报外置；全部回滚后，新一轮才使用当前配置。未配置保留旧本地行为，实际位置仍回执false，不能把兼容模式写成已满足独立位置。

## 已验证与未验证

[三份完整小型文件](components-result.json)/[JUnit](components.xml)：82 passed，约25.84秒，包含既有58和新24；早期81回执保留。覆盖配置、重启地址、跨工作空间、原副本丢失、已知凭据及真实Windows junction。启动和worker参数接缝用提前拒绝探针，不把探针说成成功启动。

另跑[既有原生CLI集成用例](native-cli-result.json)/[JUnit](native-cli.xml)：1 passed，真实Windows子核心/身份核实管道/CLI doctor与dispatch共享核心、非人工CLI不能伪造授权。继承真实配置后，[现场备份事实](native-location-facts.json)证明启动迁移备份在活动目录外、位于配置根目录并可完整自校验。这是组件级实际进程，不是Trae、业务闭环、掉电或AC验收。

ruff与strict mypy248通过；[5451节点收集](collect-result.json)不是全量执行。公开Command/Response、端口签名、Schema、61动作和接口版本/签署不变。新制品/完整既有bootstrap与worker回归仍需后续验证；本批未登记全量通过。

## 配置方式与剩余范围

在启动核心的宿主环境设置AITEST_MIGRATION_BACKUP_ROOT为用户选定的外部绝对目录；或可信装配传入migration_backup_root，或直接启动worker时传--migration-backup-root。显式装配值优先于环境。连接已有核心不热重配，受控关闭后下一次启动才采用新配置；活动迁移始终使用冻结地址。宿主设置页、真实用户操作与跨机/掉电未验，不关闭A-06/A-07；完整永久历史、新generation和未知执行核实仍欠。

接下来交付优先级见[可用版本主线](../../一期工程整改/04-交接/可用版本交付路径-2026-10-15.md)，不以测试数字替代可用性。
