# 宿主接入

## Python API

调用 `create_component(workspace)` 创建组件，再通过 `component.projects` 等应用服务使用用例；也可用 `AITestAPI` 获得更窄的稳定接口。

## ASGI

`AitestASGIApp` 是 ASGI 3.0 子应用。骨架提供：

- `GET /capabilities`
- `GET /health`

团队宿主必须使用单 worker，并在后续实现中通过 `configure_host()` 注入身份、执行、凭据、维护和遥测 Bridge。

## MCP

安装 `mcp` 可选依赖后调用 `create_mcp_server(workspace)`。骨架只暴露只读能力发现工具。
