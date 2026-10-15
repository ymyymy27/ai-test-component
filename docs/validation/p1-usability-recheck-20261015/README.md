# 0.4.4 可用性复验（2026-10-15）

验证源码：`fix@2c6db41f1ffea3306221e955402ccab2c2e0eff6`。Windows、CPython 3.13.0；产品源码未修改。

- Python：`test_authenticated_dynamic_workflow.py`、`test_query_authentication.py`、`test_dynamic_object.py`、`test_export_evidence_names.py`，36 passed，退出 0，用时 370.08 秒。[JUnit 回执](python.xml)。运行命令为 `.runtime/completion-clean313/Scripts/python.exe -m pytest` 加上述四个 `tests/unit/` 文件路径。
- 真实进程组件链：发布计划、准备与授权、实际创建动态对象、当前 stdout JSON 取 ID、独立 HTTPS Bearer 查询、报告、ZIP 导出、重启回读与幂等查询通过；工作空间无测试凭据正文。人工确认仍使用夹具；只执行一个步骤，报告必须保留 incomplete。
- Windows 凭据管理器临时测试引用写入、读取、删除及用途限制通过；不代表用户真实凭据配置验收。
- `npm --prefix integrations/trae test`：25 passed，退出 0。
- `npm --prefix src/aitest/resources/panel test`：Chromium 17 passed，退出 0；这是浏览器面板检查，未进行真实 Trae 点击验收。
- `scripts/smoke_wheel.py`：仓库外新建隔离环境安装 0.4.4，模板读取、未配置 doctor 与 MCP 的预期拒绝通过，退出 0。安装依赖 Pydantic 2.14.1；Python 回归使用锁定环境。
- wheel 内 242 个 Python 模块与当前源码逐字节一致；wheel、VSIX 和源码构建面板逐字节一致。

制品 SHA256：

- wheel：`053c5ac595ed4701c511ce3583e28fb6ba2b168860ddf90e2cb8507c941e4f6b`
- VSIX：`79d911d87b2ae5aeba79f6a4d2079fbf348aa0d9e2fbfe1064d750d2866cec3d`

结论：0.4.4 已具备本机配置后试用的组件链依据，本轮未发现新失败。未证明真实 Trae 人工操作、用户业务部署、所有业务步骤、故障恢复及完整一期验收；没有运行全量，不与历史批次累计。验收登记不变。
