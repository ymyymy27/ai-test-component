# 2026-09-30-A包DeepSeek配置

## 1. 本次实现概述

补齐 `infrastructure/adapters/deepseek.py` 占位模块，提供一期默认 DeepSeek 供应方配置：冻结 ModelProfile（供应方/地址/模型/凭据引用/Schema/参数），模型 id 必须显式传入不硬编码，调用归一逻辑复用 HttpModelProvider。

## 2. 新增/修改文件清单

- `src/aitest/infrastructure/adapters/deepseek.py`
  - `DeepSeekModelProvider(model_id, secret, base_address, transport)`：默认地址 `https://api.deepseek.com`，空模型名直接拒绝；`call` 委托 HttpModelProvider；`profile` 返回冻结事实。
  - `ModelProfile` 数据类与 `DEFAULT_BASE_ADDRESS`。
- `tests/unit/test_a_model_provider.py`
  - 已含 DeepSeek profile/委托与模型 id 必填两项测试。

## 3. 落地的约束清单

- 不硬编码未经实测的模型名、请求头或响应字段。
- 凭据引用与模型请求头构造只在通用模型适配中实现，本模块不重复。
- Profile 为冻结事实，可被应用层登记与展示。
- 不改动 B/C/D 包任何代码。

## 4. 重要备注

- 默认地址未做真实联通性实测；真实调用需用户完成凭据登记。
- 当前 Profile 未持久化（作为装配期事实）；后续可经 UOW 登记。

## 5. 变更摘要

- 重写源文件 1 个（deepseek.py，约 72 行）。
- 测试覆盖在 model provider 测试文件内（2 项相关用例）。
- 具体增删行数以 Git diff 统计为准。
