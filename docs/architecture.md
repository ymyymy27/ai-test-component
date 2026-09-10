# 架构决策

## 组件边界

项目交付一个 Python 包，不创建平台数据库、消息队列、独立账号系统、专用 API 服务、Worker 或 Runner。团队模式把 `AitestASGIApp` 挂载到现有 Python ASGI 3.0 宿主，并复用宿主身份、权限、调度和监控。

## 依赖方向

```text
interfaces -> application -> domain
                    ^
                    |
             infrastructure
```

`domain` 不导入文件、HTTP、ASGI、MCP 或供应商 SDK。`application` 只通过 Protocol 端口访问外部能力。`composition.py` 负责选择实现。

## 数据边界

每个工作空间独立保存对象、generation、当前修订和锁。可信状态只能通过工作单元提交；附件使用 SHA-256 内容寻址，但不跨工作空间去重。

## 当前骨架范围

当前仅实现最小项目记录闭环和接入探针。其他目录中的接口用于固定依赖方向，具体业务将在对应验收测试就绪后实现。

