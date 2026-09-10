# 运行维护

```bash
aitest --workspace <path> init
aitest --workspace <path> doctor
```

工作空间初始化后包含 `objects`、`generations`、`current.json` 和 `.locks`。当前骨架通过有限锁实现有限等待的跨进程单写，并通过修订号拒绝过期写入；锁租约、备份、迁移和断点恢复将在对应可靠性测试落地后启用。
