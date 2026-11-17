# Surface brief — 共享测试面板

- 目标路径：`panel/`
- 模式：Operate（用户在执行一项任务：完成一次本地测试）
- 受众与任务：测试/开发用户在窄幅编辑器面板里完成项目登记、查看建议测试单、一次点击开始测试、读取结论与覆盖、处理失败证据与问题、导出报告；需要时可打开调整或历史抽屉。
- 约束：Vue 3 + Vite + TS；只依赖 `window.aitestHost`；纯本地资源；400–900px 可用；文案全中文且不出现内部标识。
- 成功：用户在三分钟内从“无项目”走到“已开始运行”，并且能说清本次结论覆盖了哪几条验收项。

## Direction contract

THESIS: 一台本地测试控制台，而不是网页表单。打开后先看到 AITEST 字标、项目、状态灯与一张铺满面板的测试单；范围与结论是同一块仪表上的分子与分母。
OWN-WORLD: 石墨黑地（#08090a）全宽铺开，没有卡片；1px 规则线、锐角 2px、铜色信号（#e0a56b）只给编号/选中/光标；结论色低饱和苔绿/砖红/赭黄；皮肤可选 Console（石墨黑/铜）或 Paper（暖纸白/墨）；13px 系统无衬线、10–11px 单色标签、44px 结论词，等宽只给编号、状态与输出。
STORY: 用户打开面板像打开一台本地测试控制台：顶栏是 AITEST/项目/READY，首屏是目标、范围刻度与带编号的检查清单；开始后同一页变成用例分段与当前用例；结束后原地变成大号结论、S/M 记分牌与 [01]… 分区；底部状态线始终显示本地/无网络/轮询或 Ctrl+Enter 提示。历史与调整在右侧 INSPECTOR 抽屉里，不跳页。
FIRST VIEWPORT: 48px 顶栏（项目选择器、项目详情/历史、环境点、设置），其下是测试单卡片：项目名与“测试单”标识、目标标题与一句依据、验收标准开关列表、范围 X/Y、环境/副作用/耗时三行摘要，底部“开始测试”。
FORM: 单张测试单卡片，相位在卡片内切换；历史、调整、项目详情与证据是右侧抽屉；位置为“与 VSCode 同族的精密工具界面”，世界由用户简报钉住，未跑概念掷骰。
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
