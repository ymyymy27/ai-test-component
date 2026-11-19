/** 把结构化错误翻译成“哪里错了 + 下一步怎么做”，不把错误码丢给用户。 */

export interface FriendlyError {
  title: string;
  detail: string;
  next: string;
}

interface Copy {
  title: string;
  next: string;
}

const COPY: Record<string, Copy> = {
  INVALID_REQUEST: {
    title: "提交的内容不完整或有字段不合法",
    next: "按提示补齐后重试；若反复出现，刷新面板再试一次",
  },
  INVALID_PATH: {
    title: "目录不存在或不可写",
    next: "换一个目录，或修正该目录的权限",
  },
  WORKSPACE_LOCKED: {
    title: "数据目录正被另一个窗口占用",
    next: "关闭另一个测试面板或等待当前操作结束后重试",
  },
  PROJECT_NOT_FOUND: {
    title: "找不到这个项目",
    next: "回到项目页重新选择项目文件夹",
  },
  TASK_NOT_FOUND: {
    title: "找不到这次测试任务",
    next: "回到第一步重新输入测试目标",
  },
  PLAN_NOT_PUBLISHED: {
    title: "测试计划还没有发布",
    next: "返回上一步重新生成计划",
  },
  SCOPE_EMPTY: {
    title: "本次范围里没有可执行的验收标准",
    next: "至少启用一条可观察的验收标准后再继续",
  },
  CONFIRMATION_REQUIRED: {
    title: "还没有完成执行前确认",
    next: "在确认页查看范围与影响后，点击“开始测试”",
  },
  RUN_ACTIVE: {
    title: "这个项目已经有正在运行的测试",
    next: "等待它结束，或先在运行页取消",
  },
  RUN_NOT_FOUND: {
    title: "找不到这次运行记录",
    next: "回到历史记录重新打开",
  },
  RUN_STATE_CONFLICT: {
    title: "当前状态不允许这个操作",
    next: "刷新后按当前状态重试",
  },
  MODEL_UNAVAILABLE: {
    title: "模型没有配置或连接不上",
    next: "在设置里检查模型配置；已有的本地测试与报告仍然可用",
  },
  MODEL_OUTPUT_INVALID: {
    title: "模型返回的内容不合格",
    next: "重试一次，或改用手工补充这一项",
  },
  GITHUB_UNAVAILABLE: {
    title: "GitHub 没有登录或读取失败",
    next: "安装并登录 gh 后重试；本地测试不受影响",
  },
  LINK_CONFIG_MISSING: {
    title: "缺少日志或查询配置",
    next: "在项目配置中补齐后再采集链路",
  },
  DEPENDENCY_MISSING: {
    title: "缺少运行依赖",
    next: "在确认页允许安装，或先手动安装依赖",
  },
  SCRIPT_REJECTED: {
    title: "生成的脚本包含危险操作，已被拒绝",
    next: "检查脚本内容，或改用手工验证",
  },
  SCHEMA_UNSUPPORTED: {
    title: "数据格式来自旧版本",
    next: "先做备份，再转换或重建数据",
  },
  BACKUP_INVALID: {
    title: "备份损坏或校验失败",
    next: "换一个备份文件",
  },
  DISK_FULL: {
    title: "磁盘空间不足",
    next: "清理磁盘后重试",
  },
  INTERNAL: {
    title: "处理过程中出现未预期的问题",
    next: "稍后重试；如果一直出现，记下操作时间并反馈",
  },
  WORKSPACE_NOT_CONFIGURED: {
    title: "还没有设置用户数据工作空间",
    next: "打开设置填写数据目录（不是被测源码目录）",
  },
  PYTHON_NOT_FOUND: {
    title: "找不到可用的 Python 3.11+ 环境",
    next: "在设置中填写解释器路径，或安装 Python 3.11 及以上版本",
  },
  CLI_TIMEOUT: {
    title: "本地服务响应超时，结果未知",
    next: "先刷新状态确认是否已经生效，不要直接重复提交",
  },
  CLI_EXIT_NONZERO: {
    title: "本地服务异常退出",
    next: "在设置里运行一次环境自检",
  },
  CLI_INVALID_JSON: {
    title: "本地服务返回的内容无法识别",
    next: "确认扩展与本地服务版本匹配，然后重新打开面板",
  },
  CLI_SPAWN_FAILED: {
    title: "无法启动本地服务",
    next: "在设置里检查 Python 解释器路径",
  },
  CLI_OUTPUT_LIMIT: {
    title: "本地服务输出过大，结果未采用",
    next: "在设置里运行环境自检，确认服务没有把日志写进结果",
  },
  COMMAND_NOT_ALLOWED: {
    title: "面板没有权限执行这个操作",
    next: "更新扩展后重新打开面板",
  },
  PANEL_TIMEOUT: {
    title: "等待超时，结果未知",
    next: "先刷新确认是否已经生效，不要重复提交",
  },
  HOST_CALL_FAILED: {
    title: "与编辑器的连接中断",
    next: "重新打开测试面板",
  },
};

export function describeError(error: unknown): FriendlyError {
  if (error && typeof error === "object") {
    const code = "code" in error ? String((error as { code: unknown }).code) : "";
    const message = "message" in error ? String((error as { message: unknown }).message) : "";
    const nextStep =
      "nextStep" in error ? String((error as { nextStep: unknown }).nextStep) : "";
    const known = COPY[code];
    if (known) {
      return {
        title: known.title,
        detail: message.trim(),
        next: nextStep.trim() || known.next,
      };
    }
    if (message.trim()) {
      return {
        title: "这次操作没有完成",
        detail: message.trim(),
        next: nextStep.trim() || "检查当前状态后重试",
      };
    }
  }
  return {
    title: "这次操作没有完成",
    detail: "",
    next: "重新打开面板后再试",
  };
}
