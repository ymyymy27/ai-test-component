# develop修复复核证据（2026-10-03）

源码 7dbc5a45aa1666652c3dcdc2d0b0604066bc6498；比较335540b→7dbc5a4；完整结论见[复核报告](../../一期工程整改/01-现状检查/一期修复复核-2026-10-03.md)。

- issue-status.json：48条旧任务逐条关闭/部分/待接入验收，加C-14；15关闭、33旧剩余、34当前。
- observations.json / probes.py：20组独立观察，合成数据/临时目录、真实本地组件与短Python进程；无真实供应方/Trae/掉电验收。探针引用现有tests夹具及历史empty_full函数，在仓库根目录运行 uv run python docs/validation/p1-fix-recheck-20261003/probes.py 会更新同目录观察JSON。
- validation-results.json / pytest-summary.txt：本轮1405通过/2权限跳过、Ruff/mypy/版本/Schema、面板构建及1静态失败。未重做wheel/VSIX或查新版远端CI。
- ocr-scope.json / ocr-rules.json / review-inventory.json：124文件增量中58实现文件的差异/调用链检查范围与确定性规则；66排除项不宣称逐字全读，相关文档/测试按主题补读。
- document-checks.json：四包34条与48条旧总账逐项对齐，17FR/35AC、20组观察和58文件规则一致；193个本地链接目标存在，14份前次深入检查材料与Git基线一致，产品/主责规范无改动。未宣称逐个历史锚点复验。

命令：uv run pytest --continue-on-collection-errors --junitxml=.git/p1-fix-recheck-20261003/pytest.xml；uv run ruff check src tests；uv run mypy src；uv run python scripts/check_versions.py；uv run python scripts/generate_schemas.py 后核对7份生成物无Git差异；面板目录 npm run build / npm test；uv run python .git/p1-fix-recheck-20261003/recheck.py。

环境：Windows x64、CPython3.13.13、uv0.11.8、Node24.14.1、npm11.13.0、产品0.4.0。真实AC登记0verified/7not_verified/1blocked/27untested，证据path全空；未更改登记。探针退出0包含剩余反例成立，不能读成20组业务通过。
