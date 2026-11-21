import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import IssueStatusDialog from "@/components/IssueStatusDialog.vue";
import type { Issue } from "@/domain/types";

const issue = {
  id: "issue_1",
  status: "in_progress",
  disposition: null,
  reason: "",
} as Issue;

describe("IssueStatusDialog", () => {
  it("submits no disposition after switching back to pending", async () => {
    const wrapper = mount(IssueStatusDialog, {
      props: { issue, busy: false, modelValue: false },
    });
    await wrapper.setProps({ modelValue: true });
    await wrapper.vm.$nextTick();

    expect(wrapper.find("#issue-disposition").exists()).toBe(true);
    const disposition = wrapper.find("#issue-disposition");
    expect(disposition.text()).toContain("不填写");
    await wrapper.find("#issue-next-status").setValue("pending");
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("save")?.[0]?.[0]).toMatchObject({
      status: "pending",
      disposition: null,
    });
  });

  it("does not require a reason for a plain status move", async () => {
    const wrapper = mount(IssueStatusDialog, {
      props: { issue, busy: false, modelValue: false },
    });
    await wrapper.setProps({ modelValue: true });
    await wrapper.vm.$nextTick();
    await wrapper.find("form").trigger("submit");
    // 处理中 → 待复测 不需要理由；处置默认未选择。
    expect(wrapper.emitted("save")?.[0]?.[0]).toMatchObject({
      status: "retest",
      disposition: null,
    });
  });
});
