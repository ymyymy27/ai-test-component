# Surface brief — 共享测试面板

- 目标路径：`panel/`
- 模式：Operate（用户在执行一项任务：完成一次本地测试）
- 受众与任务：测试/开发用户在窄幅编辑器面板里完成项目登记、目标与验收标准确认、范围选择、执行确认、结果阅读与报告导出。
- 约束：Vue 3 + Vite + TS；只依赖 `window.aitestHost`；纯本地资源；400–900px 可用；文案全中文且不出现内部标识。
- 成功：用户在三分钟内从“无项目”走到“已开始运行”，并且能说清本次结论覆盖了哪几条验收项。

## Direction contract

THESIS: 一台测试计量仪，而不是表单堆栈。它拒绝“设置项一页铺开”的默认：范围与结论永远是同一块仪表上的分子与分母。
OWN-WORLD: 石墨黑地（#0e1012）与冷灰层（#15181b/#1a1e22），1px 发丝线定义层级、不用阴影堆深度；唯一钢蓝强调色只用于选择与焦点；结论色是低饱和苔绿/砖红/赭黄/中性灰；13px 系统无衬线、数字等宽，等宽字体只给证据与输出。
STORY: 用户打开面板先看到项目与环境事实，然后沿四步走到确认页；执行时看到进度与当前用例；结果页先给一句话结论与 X/Y 覆盖，再给失败证据与四段分析。
FIRST VIEWPORT: 48px 顶栏（项目选择器、环境点、设置），其下是项目卡：名称与根目录、模块/入口/最近运行三列扫描结果、上次结论与覆盖、主按钮“新建测试”与次按钮“查看历史”。
FORM: 单列分阶段控制台，向导用左侧刻度轨标出四步；位置为“与 VSCode 同族的精密工具界面”，世界由用户简报钉住，未跑概念掷骰。
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
