import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ProjectStats from "@/components/ProjectStats.vue";

describe("ProjectStats", () => {
  it("shows four real counts with labels", () => {
    const wrapper = mount(ProjectStats, {
      props: { tasks: 2, runs: 5, openIssues: 3, features: 4 },
    });
    expect(wrapper.findAll(".stat")).toHaveLength(4);
    expect(wrapper.text()).toContain("任务");
    expect(wrapper.text()).toContain("运行");
    expect(wrapper.text()).toContain("未关闭问题");
    expect(wrapper.text()).toContain("业务功能");
    expect(wrapper.text()).toContain("5");
  });
});
