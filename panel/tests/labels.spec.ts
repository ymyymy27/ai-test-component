import { describe, expect, it } from "vitest";

import { describeError } from "@/api/errors";
import { HostError } from "@/api/host";
import {
  CASE_RESULT_LABEL,
  LAYER_LABEL,
  RUN_STATUS_LABEL,
  VERDICT_LABEL,
  formatDateTime,
  methodLabel,
  sourceLabel,
} from "@/api/labels";

describe("错误与文案", () => {
  it("已知错误码给出原因和下一步，不暴露错误码本身", () => {
    const friendly = describeError(
      new HostError({
        code: "RUN_ACTIVE",
        message: "项目已有运行：run_abc",
        next_step: "等待或取消现有运行后重试",
      }),
    );
    expect(friendly.title).toContain("已经有正在运行的测试");
    expect(friendly.next).toBe("等待或取消现有运行后重试");
    expect(friendly.title).not.toContain("RUN_ACTIVE");
  });

  it("未知错误保留后端消息，并给通用下一步", () => {
    const friendly = describeError(new HostError({ code: "SOMETHING_NEW", message: "磁盘忙" }));
    expect(friendly.title).toBe("这次操作没有完成");
    expect(friendly.detail).toBe("磁盘忙");
    expect(friendly.next).toBeTruthy();
  });

  it("状态、结论与方法都翻译成中文", () => {
    expect(RUN_STATUS_LABEL.interrupted).toBe("已中断");
    expect(VERDICT_LABEL.incomplete).toBe("没测完");
    expect(CASE_RESULT_LABEL.manual_pending).toBe("等你确认");
    expect(LAYER_LABEL.L3).toBe("整条流程");
    expect(methodLabel("command:pytest tests")).toBe("命令执行");
    expect(methodLabel("manual:现场核对")).toBe("人工核对");
    expect(sourceLabel("git")).toBe("近期提交");
  });

  it("时间不可解析时原样返回，不伪造日期", () => {
    expect(formatDateTime(undefined)).toBe("时间未记录");
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });
});
