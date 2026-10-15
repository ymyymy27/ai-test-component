# Trae 薄扩展预览 0.4.2

共享面板读取唯一核心的项目/运行/问题/报告/事件；新增受控高级操作入口和新建项目模板。扩展不直接写业务文件或生成确认事实。**真实Trae中国版/国际版的安装与完整用户流程仍未验收，完整自动测试向导与默认独立业务核验仍欠。**

先安装同版本Python包（Windows、Python3.13，原生确认需要tkinter/Tcl），在编辑器命令面板执行 `AI Test: 配置工作空间与项目`，选择已存在的专用用户数据目录和项目编号。用户数据目录不能选源码或安装目录。

执行 `AI Test: 打开一期面板（预览）` → 连接核心 → 刷新核心状态。仅新核心声明 `native-confirmation/1` 后启用受控操作。点击“填写新建项目”，修改实际名称/目标，再请求核心窗口确认；关闭/取消/超时不提交。

高级操作JSON只填 `action / intent_id / expected_revision / binding_revision / target / parameters`。项目由宿主显式选择，传输请求号由入口分配；业务意图不能用请求号替代。取消或结果未知时保留原意图核对，不能通过自动换号重跑。不要粘贴凭据正文。准确材料在核心窗口展示，网页/AI不能提交“已确认”代签。

旧核心没有此传输能力会明确拒绝；安装新包不会热替换活动实例。应经既有受控关闭端排空再启动，不能强杀有活动或未知执行的核心。`aitest.runId` 用于读取所选运行，面板不从目录猜测项目或运行身份。

从仓库根先构建 `src/aitest/resources/panel`，再在本目录执行 `npm ci` 与 `npm run package`。候选制品为 `dist/aitest-trae.vsix`。CLI/MCP复用同一核心；普通relay仍不能签署人工动作。

本次Node使用明确假CLI/宿主，浏览器为真实Chromium加模拟宿主；它们只证明入口组件，不证明实际Trae或实际人工点击。操作步骤与准确范围见仓库 `docs/一期工程整改/04-交接/0.4.2-受控入口预览安装与操作.md` 和 `docs/validation/p1-native-entry-20261015/README.md`。

机器级设置 `aitest.pythonCarriersFile` 指向操作者核对的宿主 Python 载体登记文件，仅新核心启动时使用；既有核心需受控重启。CLI `python-carrier --help` 可生成实际虚拟环境登记，新建项目表单要求真实名称和测试目标。
