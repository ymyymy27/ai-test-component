import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import RunProgress from "@/components/RunProgress.vue";
import type { Run } from "@/domain/types";

function run(cases: Run["cases"]): Run {
  return {
    id: "run_1",
    status: "running",
    simulated: false,
    cases,
    items: [],
    currentCaseId: "",
  } as unknown as Run;
}

describe("RunProgress", () => {
  it("does not render a 0/0 progressbar when there are no cases", () => {
    const wrapper = mount(RunProgress, { props: { run: run([]), elapsedMs: 1000 } });
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("没有可执行的用例检查");
  });
});
