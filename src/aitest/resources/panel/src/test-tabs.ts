/** Secondary navigation inside the single "本次测试" entry. */

import type { AppContext } from "./context";
import { div } from "./ui";

export type TestTab = "overview" | "steps" | "plan" | "rules";

export function testTabs(ctx: AppContext, active: TestTab): HTMLElement {
  const tabs = div("segmented subnav");
  tabs.dataset.testid = "test-tabs";
  tabs.setAttribute("aria-label", "本次测试视图");
  const entries: ReadonlyArray<readonly [TestTab, string, () => void]> = [
    ["overview", "概览", () => ctx.navigate("home")],
    ["steps", "步骤", () => ctx.navigate("workbench")],
    ["plan", "计划", () => ctx.navigate("plan")],
    ["rules", "规则", () => ctx.navigate("rules")],
  ];
  for (const [key, label, onClick] of entries) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.className = key === active ? "active" : "";
    if (key === active) button.setAttribute("aria-current", "page");
    button.onclick = onClick;
    tabs.appendChild(button);
  }
  return tabs;
}
