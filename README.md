# AI 辅助测试插件 · 一期工程骨架

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)

持续集成、测试报告下载和版本交付操作见 [CI/CD 使用说明](docs/CI-CD.md)。

当前版本 **0.4.0**。以 [新版总体架构](docs/总体架构.md) 和 [一期文档](docs/一期/架构文档/00-架构总览.md) 为唯一开发基线。
本次完成分支整合与工程骨架重建，**不代表一期35项产品验收完成**。

## 开发环境与验证

Python 3.13、uv、Node.js 22+、npm。运行目标为 Windows 11 x64 / Trae；真实编辑器接入未验证。

```sh
uv sync --extra dev --locked
npm --prefix src/aitest/resources/panel ci
npm --prefix src/aitest/resources/panel run build
cd src/aitest/resources/panel
npx playwright install chromium
npm test
cd ../../../..
npm --prefix integrations/trae ci
npm --prefix integrations/trae run package
uv run python scripts/generate_schemas.py
uv run ruff check .
uv run mypy
uv run pytest
uv build
```

```sh
uv run aitest templates
uv run aitest doctor
uv run aitest mcp-relay --binding example
```

`templates`列出六个已打包模板及scaffold状态。`doctor`返回NOT_READY及退出码2；relay将不可用原因写入stderr并退出2，不向stdout伪造MCP消息。
目前没有业务写入、执行或网络入口；这些命令不会创建工作空间。

## 目录与边界

- `src/aitest/domain`：按project/planning/execution/evidence/review拆分的纯领域对象和守卫。
- `src/aitest/application`：一期用例落点，`ports.py`为唯一端口定义位置。
- `src/aitest/infrastructure`：文件存储及适配器落点；保留经验证的原子写入和OS锁工具。
- `src/aitest/contracts`：Pydantic边界与生成Schema；`interfaces`负责本地协议、DTO和CLI/relay。
- `src/aitest/resources`：六模板、唯一TypeScript面板源码；`bootstrap.py`唯一组装点。
- `integrations/trae`：仅命令/Webview的候选VSIX薄扩展，不复制面板业务。
- `tests`：复用模型、合同、依赖边界与底层故障测试；`tests/acceptance/p1`单列未完成AC。

二、三期保留现有设计文档，不预注册空能力或安装未来依赖。无ASGI、远程MCP、平台接收、团队会签或业务删除入口。
生成资源不手工修改：Schema由脚本生成，面板由TypeScript构建，VSIX打包使用同一面板制品。

## 交付状态

[整合与工程状态](docs/一期/工程状态.md)记录复用/清理依据、模块映射和待实现范围；[兼容清单](docs/compatibility.json)不把编译当作真实Trae兼容通过。
旧`ai_test`导入路径已移除，不提供双实现兼容层；旧工作空间不能直接作为新版工作空间打开，未来迁移须保留原始数据并按新文档验证。
历史设计保留在`docs旧`；新开发不据此实现。两个分支及ma源码ZIP仍可从Git合并历史追溯。
