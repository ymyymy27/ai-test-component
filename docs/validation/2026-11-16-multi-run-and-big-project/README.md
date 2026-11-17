# 多轮与大项目实跑（2026-11-16）

## Flaskr（小项目）

- 服务：`flask --app flaskr run --port 5000 --no-reload`；
- 计划：`plan_753fd6b155dc`，6 条用例（HTTP + requests + Playwright）；
- 连续运行：
  - `run_aa231cf946d7`：completed / passed，6/6；
  - `run_3a47a743db84`：completed / passed，6/6；
  - `run_e526413e4acb`：completed / passed，6/6。
- 工作空间：`%APPDATA%/aitest/workspaces/pilot`。

## requests（较大项目）

- 仓库：`psf/requests` 浅克隆，项目根 `E:/pilot/requests`；
- 环境：项目 `.venv`（Python 3.11），pytest + httpbin；
- 基线（人工 pytest）：621 passed / 10 failed / 3 skipped；失败集中在代理、外网与证书环境。
- 产品 AI 计划 `plan_f0c585bad061`：6 条脚本用例，覆盖 URL、参数编码、响应结构。
  - 第 1 次 `run_d133837f20ff`：5/6 通过；失败是 AI 脚本自身错误（`PreparedRequest.headers` 未初始化）。
  - `plan.update` 修订 2 修正脚本后：
    - `run_0fe9d95612bb`：passed，6/6；
    - `run_c5ee3f8d7e01`：passed，6/6；
    - 另一次同计划运行 passed，6/6。
- 工作空间：`%APPDATA%/aitest/workspaces/big`。

## 说明

- 产品在 requests 上跑的是 AI 选取的 6 条核心用例，不是全量 pytest；全量基线只用于确认项目本身状态。
- 第 1 次失败保留了原始脚本、证据与报告；修正通过 `plan.update` 形成新修订，旧运行未被覆盖。
- 这验证了：多轮重复运行、较大项目、AI 计划错误可被定位并通过修订重跑。

## 通用冒烟策略（2026-11-16 晚）

- 旧行为：通用冒烟直接执行 `test_commands[0]`；在 requests 上即全量 pytest（约 150s），并受代理、外网与证书等基线环境失败影响。
- 新行为：默认生成三条快速 L1 检查——入口模块可导入、`python -m pytest --collect-only -q` 能发现用例、`tests` 下最小的非空 `test_*.py` 能通过；项目可用 `l1.smoke_command` 覆盖为单条命令。非 pytest 项目回退到声明的测试命令，没有可用检查时返回 `INVALID_REQUEST`。
- requests 实测：`plan_ef920f0e7e44`（3 条 L1）、`run_a7d33cc2fac8` 与 `run_69eeeddbae0e` 均 completed / passed，3/3；从启动到轮询结束约 1s。
- 首次 `run_0a34b15e0c7d` 三条全部失败：父进程 `uv run` 注入的 `PYTHONHOME` 指向 3.13，与项目 `.venv` 的 3.11 标准库混用。worker 现在在注入项目解释器时移除 `PYTHONHOME`，重跑通过；原始失败运行与证据保留。
- 覆盖边界：`l1.smoke_command` 覆盖、无检查报错、最小测试文件选择与签名失效由单元测试覆盖，未在 requests 上逐一实跑；本轮未重新安装 Trae 面板验证。
