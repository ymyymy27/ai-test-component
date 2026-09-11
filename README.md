# AI Test Component

一个轻量、可嵌入的 AI 辅助测试组件骨架。项目采用单个 Python 包和文件存储，提供 Python API、CLI、ASGI 子应用、可选 MCP 入口及 Web Component 面板资源。

当前版本是工程骨架，已实现项目记录的最小闭环、工作空间初始化、原子 JSON 写入、能力发现和健康检查。测试执行、完整证据链、DeepSeek 调用、GitHub 同步、迁移和团队会签仍保留明确接口，尚未标记为完成。

## 运行要求

- Python 3.13
- 本地可写目录作为工作空间
- 团队模式使用一个 ASGI worker 挂载一个工作空间

## 安装

```bash
uv sync --extra dev
```

可选能力：

```bash
uv sync --extra mcp
uv sync --extra postgres-check
```

## 快速开始

```bash
uv run aitest --workspace .aitest-data init
uv run aitest --workspace .aitest-data capabilities
uv run aitest --workspace .aitest-data project-create demo "演示项目"
uv run aitest --workspace .aitest-data project-get demo
uv run aitest --workspace .aitest-data task-create demo task-1 "创建并查询工单" --scope "工单创建流程" --acceptance "AC1=创建后的工单可以查询到一致数据"
uv run aitest --workspace .aitest-data task-get task-1
uv run pytest
```

## 目录边界

- `domain`：业务状态和不变量，不访问文件或网络。
- `application`：用户用例和端口。
- `infrastructure`：文件、外部服务和遥测实现。
- `interfaces`：Python、CLI、MCP 和 ASGI 协议转换。
- `resources`：Schema、规则、提示、导出和面板静态资源。

依赖方向固定为 `interfaces → application → domain`。基础设施实现应用端口，通过 `composition.py` 装配。

## 文档

- [接入说明](docs/integration.md)
- [数据格式](docs/formats.md)
- [运行维护](docs/operations.md)
- [架构决策](docs/architecture.md)

## 状态

版本 `0.2.0` 仅代表骨架可运行，不代表需求文档中的首期业务验收已经完成。
