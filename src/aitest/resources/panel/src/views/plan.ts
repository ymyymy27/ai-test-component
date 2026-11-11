/** Published plan snapshot: what will run, which cases and which assertion basis. */

import type { AppContext } from "../context";
import { testTabs } from "../test-tabs";
import { asNumber, asRecord, asRecords, asStrings, label, shortId, toneForState } from "../labels";
import {
  button,
  card,
  cardHead,
  chip,
  div,
  empty,
  loading,
  pageHead,
  p,
  stat,
} from "../ui";

export function renderPlan(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const page = div("page");
  page.appendChild(testTabs(ctx, "plan"));
  page.appendChild(
    pageHead(
      "计划",
      "已发布的检查计划",
      "计划发布后不可变；修订会生成新版本。执行入口在工作台，不在计划页。",
      button("读取计划列表", () => void ctx.loadPlans(), { icon: "refresh", disabled: state.busy !== null }),
    ),
  );
  if (state.selectedPlan && state.planDetail) page.appendChild(planDetail(ctx, state.planDetail));
  else if (!state.plansLoaded) page.appendChild(card("", loading("正在读取已保存计划…")));
  else if (state.plans.length === 0) {
    page.appendChild(
      card(
        "",
        empty(
          "file",
          "还没有已保存的计划",
          "从准备测试向导生成并发布一份计划；发布后这里会显示用例、范围和依据。",
          button("开始准备测试", () => ctx.navigate("wizard"), { variant: "primary", icon: "arrow-right" }),
        ),
      ),
    );
  } else {
    page.appendChild(planList(ctx));
  }
  return page;
}

function planList(ctx: AppContext): HTMLElement {
  const list = div("list");
  for (const row of ctx.state.plans) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "row";
    item.onclick = () => void openPlan(ctx, row.record_id, row.revision);
    item.append(
      div("row-icon", "P"),
      div("row-main", div("row-title", `计划 ${shortId(row.record_id, 12)}`), div("row-sub", `修订 ${row.revision}`)),
      chip("读取", "neutral"),
    );
    list.appendChild(item);
  }
  return card("", cardHead("计划修订", "列表只读摘要；详情按准确修订读取。"), list);
}

async function openPlan(ctx: AppContext, recordId: string, revision: number): Promise<void> {
  ctx.state.selectedPlan = { record_id: recordId, revision };
  const view = await ctx.read("plan", { record_id: recordId, revision });
  ctx.state.planDetail = view;
  ctx.render();
}

function planDetail(ctx: AppContext, view: Record<string, unknown>): HTMLElement {
  const record = asRecord(view["record"]) ?? {};
  const scope = asRecord(record["scope"]) ?? {};
  const cases = asRecords(record["cases"]);
  const required = asStrings(record["required_case_ids"]);
  const status = record["status"];
  const stats = div(
    "stats",
    stat(cases.length, "用例"),
    stat(required.length, "必测"),
    stat(asNumber(record["revision"]), "计划修订"),
    stat(label(record["run_tier"]), "运行档位"),
  );
  const caseList = div("stack-sm");
  for (const item of cases.slice(0, 20)) {
    const basis = asRecord(item["assertion_basis"]) ?? {};
    const meta = [item["case_id"], item["layer"], label(item["importance"]), label(item["verification_method"])]
      .filter(value => typeof value === "string" && value)
      .join(" · ");
    caseList.appendChild(
      div(
        "row",
        div("row-main", div("row-title", typeof item["objective"] === "string" ? item["objective"] : "未命名用例"), div("row-sub", meta)),
        chip(label(basis["state"]), toneForState(basis["state"])),
      ),
    );
  }
  if (cases.length > 20) caseList.appendChild(p(`仅显示前 20 条，共 ${cases.length} 条。`, "muted subtle"));

  return div(
    "stack",
    card(
      "",
      cardHead(
        typeof scope["name"] === "string" && scope["name"] ? scope["name"] : "计划详情",
        `计划 ${shortId(record["plan_id"], 14)} · 修订 ${record["revision"] ?? "—"}`,
        chip(label(status), toneForState(status)),
      ),
      stats,
      factsRows([
        ["计划编号", String(record["plan_id"] ?? "—")],
        ["范围编号", String(scope["scope_id"] ?? "—")],
        ["业务目标", String(scope["objective"] ?? "—")],
        ["驱动", label(record["initial_driver"])],
        ["模板版本", Array.isArray(record["template_versions"]) ? record["template_versions"].map(item => {
          const template = asRecord(item);
          return template ? `${template["template_id"] ?? "?"} ${template["version"] ?? ""}`.trim() : "";
        }).filter(Boolean).join("、") : "—"],
      ]),
      p("本页只读。准备运行请使用“准备测试”向导；已有运行请从“本次测试·概览”读取。从既有计划直接准备还需要原始准备回执，本版未开放。", "muted subtle"),
      div("hero-actions", button("返回计划列表", () => {
        ctx.state.selectedPlan = null;
        ctx.state.planDetail = null;
        ctx.render();
      }, { icon: "arrow-left" })),
    ),
    card("", cardHead("用例与依据", "依据状态来自计划记录；确认不替代执行或核验。"), caseList),
  );
}

function factsRows(rows: Array<[string, string]>): HTMLElement {
  const dl = document.createElement("dl");
  dl.className = "facts";
  for (const [key, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = value || "—";
    dl.append(dt, dd);
  }
  return dl;
}
