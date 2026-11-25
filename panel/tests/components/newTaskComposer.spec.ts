import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import NewTaskComposer from "@/components/NewTaskComposer.vue";
import { setRepository, useWorkspaceStore } from "@/stores/workspace";
import { seedProject } from "../fixtures";

describe("NewTaskComposer", () => {
  it("switches three intents and runs create -> draftDelivery -> generatePlan without auto-starting", async () => {
    const seeded = await seedProject("D:/work/new-task-demo", "建测演示");
    setRepository(seeded.repo);
    const workspace = useWorkspaceStore();
    await workspace.bootstrap();

    const callOrder: string[] = [];
    const createSpy = vi.spyOn(workspace, "createTask").mockImplementation(async (pid, input) => {
      callOrder.push(`createTask:${input.requirement ?? ""}`);
      return seeded.repo.createTask(pid, input);
    });
    const draftSpy = vi
      .spyOn(workspace, "draftDelivery")
      .mockImplementation(async (pid, tid) => {
        callOrder.push("draftDelivery");
        return seeded.repo.draftDelivery(pid, tid);
      });
    const planSpy = vi.spyOn(workspace, "generatePlan").mockImplementation(async (pid, tid) => {
      callOrder.push("generatePlan");
      return seeded.repo.generatePlan(pid, tid);
    });
    const startSpy = vi.spyOn(workspace, "confirmAndStart");

    const wrapper = mount(NewTaskComposer, {
      props: { project: seeded.project },
    });

    const radios = wrapper.findAll('[role="radio"]');
    expect(radios.map((r) => r.text())).toEqual([
      "测本次改动",
      "按业务流程测",
      "粘贴需求 / 自选功能",
    ]);

    await radios[1]?.trigger("click");
    expect(wrapper.findAll(".flow-card-btn").length).toBeGreaterThan(0);

    await radios[2]?.trigger("click");
    await wrapper.get("#composer-goal-req").setValue("验证优惠券折扣逻辑");
    await wrapper.get("#composer-requirement-text").setValue("满 100 减 20，不可与折扣券叠加");

    const submitBtn = wrapper.find("button.btn-primary");
    await submitBtn.trigger("click");

    // 等待异步 3 步准备流水线完成
    await new Promise((resolve) => setTimeout(resolve, 20));
    await wrapper.vm.$nextTick();

    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(draftSpy).toHaveBeenCalledTimes(1);
    expect(planSpy).toHaveBeenCalledTimes(1);
    expect(startSpy).not.toHaveBeenCalled();

    expect(callOrder[0]).toContain("满 100 减 20");
    expect(callOrder[1]).toBe("draftDelivery");
    expect(callOrder[2]).toBe("generatePlan");

    const doneEmits = wrapper.emitted("done");
    expect(doneEmits).toBeDefined();
    expect(doneEmits?.[0]?.[1]).toBe("plan");
  });
});
