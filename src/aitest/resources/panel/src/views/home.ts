/** Current check home: pre-run / runtime / post-run, with one primary next action. */

import type { AppContext } from "../context";
import { testTabs } from "../test-tabs";
import {
  asNumber,
  asRecord,
  asStrings,
  gapHint,
  gapText,
  label,
  stageDescription,
  toneForState,
} from "../labels";
import {
  banner,
  button,
  card,
  cardHead,
  chip,
  div,
  facts,
  h2,
  h3,
  iconBadge,
  loading,
  pageHead,
  p,
  progress,
  span,
  stat,
  stepper,
} from "../ui";

export function renderHome(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  if (!state.hostAvailable) {
    return page(
      "本次测试",
      connectionEmpty(),
    );
  }
  if (state.connection === "connecting") {
    return page("本次测试", card("", loading("正在连接本地核心…")));
  }
  if (state.connection === "unavailable") {
    return page(
      "本次测试",
      card(
        "",
        cardHead("尚未连接核心", "连接后读取项目、计划和运行事实。"),
        banner(state.connectionMessage || "核心未连接。", "warning"),
        div(
          "hero-actions",
          button("连接核心", () => void ctx.ensureCore(), { variant: "primary", icon: "zap" }),
          button("打开设置", () => ctx.navigate("settings"), { icon: "settings" }),
        ),
      ),
    );
  }
  const project = state.project;
  if (!project || !project.project_id) {
    return page(
      "本次测试",
      card(
        "hero",
        div("eyebrow", "开始"),
        h2("把一次改动，变成可核验的结论"),
        p("选择本地项目，生成一份可核对的计划，然后逐步执行、独立核验并保存报告。所有业务结论由本地核心产生。", "muted"),
        div(
          "hero-steps",
          heroStep(1, "选择项目与测试入口", true),
          heroStep(2, "生成并核对检查计划", false),
          heroStep(3, "执行、核验、保存报告", false),
        ),
        div(
          "hero-actions",
          button("开始准备测试", () => ctx.navigate("wizard"), { variant: "primary", icon: "arrow-right" }),
          button("连接核心", () => void ctx.ensureCore(), { icon: "refresh" }),
        ),
      ),
      startNotes(),
    );
  }
  if (!project.run_id) {
    return div(
      "page",
      testTabs(ctx, "overview"),
      pageHead("本次测试", "概览"),
      projectReady(ctx),
      div(
        "hero-actions",
        button("继续准备测试", () => ctx.navigate("wizard"), { variant: "primary", icon: "arrow-right" }),
        button("刷新本次状态", () => void ctx.refreshCurrent(), { icon: "refresh" }),
      ),
    );
  }
  if (!state.panel || !state.run) {
    return div(
      "page",
      testTabs(ctx, "overview"),
      pageHead("本次测试", "概览"),
      card(
        "",
        cardHead("读取本次运行", "运行已经登记，但面板还没有取得可核对的视图。"),
        p("刚打开面板或核心刚恢复时，读取可能需要一点时间。若一直失败，请查看诊断中的核心状态。", "muted"),
        div(
          "hero-actions",
          button("读取 / 刷新本次运行", () => void ctx.refreshCurrent(), { variant: "primary", icon: "refresh" }),
          button("打开诊断", () => ctx.navigate("diagnostics"), { icon: "sliders" }),
        ),
      ),
    );
  }
  return div("page", testTabs(ctx, "overview"), runHero(ctx), runMetrics(ctx), gapsCard(ctx), attentionCard(ctx));
}

function page(title: string, ...children: HTMLElement[]): HTMLElement {
  const node = div("page");
  node.appendChild(pageHead("AI 辅助测试", title));
  for (const child of children) node.appendChild(child);
  return node;
}

function connectionEmpty(): HTMLElement {
  return card(
    "",
    cardHead("未连接编辑器宿主", "这个页面运行在独立预览中，不能读取或写入测试记录。"),
    banner("请在 Trae 中打开 AI 测试面板；独立浏览器只用于界面预览。", "info"),
  );
}

function heroStep(index: number, text: string, active: boolean): HTMLElement {
  return div(
    `hero-step${active ? " active" : ""}`,
    div("hero-step-num", String(index)),
    div("grow", text),
  );
}

function startNotes(): HTMLElement {
  return card(
    "flat",
    div("section-title", "这个流程会做什么"),
    div(
      "hero-steps",
      noteLine("只读事实", "面板读取核心 DTO，不重算判定、证据等级或覆盖。"),
      noteLine("人工确认", "发布计划、授权执行由核心原生窗口确认；面板不会代签。"),
      noteLine("可恢复", "退出重开后按原运行与原意图继续；未知结果不会自动重放。"),
    ),
  );
}

function noteLine(title: string, body: string): HTMLElement {
  return div("hero-step", div("hero-step-num", "·"), div("grow", h3(title), p(body, "muted subtle")));
}

function projectReady(ctx: AppContext): HTMLElement {
  const project = ctx.state.project;
  return card(
    "",
    cardHead(
      "项目已就绪",
      "还差一次运行准备：生成计划、发布，然后准备并登记运行。",
      chip("未开始", "neutral"),
    ),
    facts([
      ["项目", project?.project_id ?? "—"],
      ["范围", project?.scope_name ?? "尚未命名"],
      ["核心", ctx.state.doctor?.status === "READY" ? "已连接" : "待核实"],
    ]),
    div(
      "hero-steps",
      heroStep(1, "补充测试目标与环境", true),
      heroStep(2, "生成并发布计划", false),
      heroStep(3, "准备并登记运行", false),
    ),
  );
}

function runHero(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const panel = state.panel ?? {};
  const run = state.run ?? {};
  const stage = panel["stage"];
  const control = panel["control_state"];
  const title = typeof panel["project_name"] === "string" && panel["project_name"]
    ? String(panel["project_name"])
    : state.project?.scope_name || "本次测试";
  const outcome = panel["business_outcome"];
  const controlTone = toneForState(control);
  const steps = ["准备", "计划", "执行", "核验", "报告"];
  const activeStep = stage === "post_run" ? 4 : stage === "runtime" ? 2 : 2;
  const done = stage === "post_run" ? new Set([0, 1, 2, 3]) : stage === "runtime" ? new Set([0, 1]) : new Set([0, 1]);

  const actions = div("hero-actions");
  if (stage === "pre_run") {
    const base = typeof run["snapshot_commit_id"] === "string" ? run["snapshot_commit_id"] : "";
    const runId = typeof run["run_id"] === "string" ? run["run_id"] : null;
    const canStart = control === "not_started" && runId !== null && base !== "";
    actions.appendChild(button("开始执行", async () => {
      if (!runId || !base) return;
      const outcome = await ctx.write(
        "start_run",
        { run_id: runId, base_snapshot_commit_id: base },
        { target: runId, expectedRevision: 0, intentId: ctx.intent("start_run", { run_id: runId, base_snapshot_commit_id: base }, runId, 0) },
      );
      ctx.setNotice(outcome.status === "ok" ? "执行请求已返回，请刷新查看进度。" : (outcome.message ?? "执行请求未完成。"), outcome.status === "ok" ? "success" : "danger");
      await ctx.refreshCurrent();
    }, { variant: "primary", icon: "play", disabled: !canStart }));
    actions.appendChild(button("打开工作台", () => ctx.navigate("workbench"), { icon: "terminal" }));
  }
  if (stage === "runtime") {
    const runId = typeof run["run_id"] === "string" ? run["run_id"] : null;
    const base = typeof run["snapshot_commit_id"] === "string" ? run["snapshot_commit_id"] : "";
    const controlAction = control === "running" ? "pause_run" : control === "paused" || control === "pause_requested" ? "resume_run" : null;
    if (controlAction && runId && base) {
      actions.appendChild(button(controlAction === "pause_run" ? "暂停" : "继续", async () => {
        const outcome = await ctx.write(
          controlAction,
          { run_id: runId, base_snapshot_commit_id: base },
          { target: runId, expectedRevision: 0, intentId: ctx.intent(controlAction, { run_id: runId, base_snapshot_commit_id: base }, runId, 0) },
        );
        ctx.setNotice(outcome.status === "ok" ? "运行控制请求已返回。" : (outcome.message ?? "运行控制未完成。"), outcome.status === "ok" ? "info" : "danger");
        await ctx.refreshCurrent();
      }, { icon: controlAction === "pause_run" ? "pause" : "play" }));
    }
    actions.appendChild(button("打开工作台", () => ctx.navigate("workbench"), { variant: "primary", icon: "terminal" }));
  }
  if (stage === "post_run") {
    actions.appendChild(button("查看报告", () => ctx.navigate("reports"), { variant: "primary", icon: "report" }));
    actions.appendChild(button("打开工作台", () => ctx.navigate("workbench"), { icon: "terminal" }));
  }
  actions.appendChild(button("刷新", () => void ctx.refreshCurrent(), { icon: "refresh" }));

  const chips = div("inline");
  chips.append(
    chip(label(control), controlTone),
    chip(label(panel["tier"]), "neutral"),
    chip(label(panel["driver"]), "neutral"),
  );
  if (outcome !== undefined && outcome !== null) {
    chips.appendChild(chip(`业务结论：${label(outcome)}`, toneForState(outcome)));
  }

  return card(
    "hero",
    div("eyebrow", "本次测试"),
    h2(title),
    p(stageDescription(stage), "muted"),
    chips,
    stepper(steps, activeStep, done),
    actions,
  );
}

function runMetrics(ctx: AppContext): HTMLElement {
  const run = ctx.state.run ?? {};
  const summary = asRecord(run["selected_summary"]) ?? asRecord(asRecord(run["coverage"])?.["selected_summary"]) ?? {};
  const total = asNumber(summary["denominator"]);
  const executed = asNumber(summary["executed_count"]);
  const verified = asNumber(summary["verified_count"]);
  const failed = asNumber(summary["failed_count"]);
  const unverified = asNumber(summary["unverified_count"]);
  const autoTotal = asNumber(run["automatic_step_count"]);
  const autoDone = asNumber(run["automatic_completed_count"]);
  const stats = div(
    "stats",
    stat(total, "必测范围"),
    stat(executed, "已执行"),
    stat(verified, "已验证"),
    stat(unverified, "未验证"),
  );
  if (failed > 0) stats.appendChild(stat(failed, "已确认失败"));
  return card(
    "",
    cardHead("本轮范围", total > 0 ? `必测 ${total} 项；不以百分比替代未完成范围。` : "尚未取得本轮分母。"),
    stats,
    autoTotal > 0
      ? div(
          "",
          div("between", span("自动步骤", "subtle"), span(`${autoDone} / ${autoTotal}`, "muted subtle")),
          progress(autoDone, autoTotal),
        )
      : p("当前运行没有自动步骤，或尚未读取到执行事实。", "muted subtle"),
  );
}

function gapsCard(ctx: AppContext): HTMLElement {
  const panel = ctx.state.panel ?? {};
  const codes = asStrings(panel["gap_codes"]);
  if (codes.length === 0) {
    return card("", cardHead("缺口", "核心没有登记本轮缺口。"), banner("没有登记缺口。真实结果仍需以报告和证据为准。", "success"));
  }
  const list = div("stack-sm");
  for (const code of codes) {
    const hint = gapHint(code);
    list.appendChild(
      div(
        "row",
        iconBadge("alert"),
        div("row-main", div("row-title", gapText(code)), hint ? div("row-sub", hint) : null),
      ),
    );
  }
  return card("", cardHead("还差什么", "缺口来自核心判定；面板只做中文说明。"), list);
}

function attentionCard(ctx: AppContext): HTMLElement {
  const run = ctx.state.run ?? {};
  const manual = asStrings(run["manual_pending_step_ids"]);
  const blocked = asStrings(run["blocked_step_ids"]);
  const pending = asStrings(run["pending_verification_step_ids"]);
  const invalidated = asStrings(run["invalidated_step_ids"]);
  const total = manual.length + blocked.length + pending.length + invalidated.length;
  if (total === 0) {
    return card(
      "",
      cardHead("待办", "没有需要人工处理的步骤。"),
      div("hero-actions", button("打开工作台", () => ctx.navigate("workbench"), { icon: "terminal" })),
    );
  }
  const list = div("stack-sm");
  const add = (ids: string[], title: string, hint: string): void => {
    if (ids.length === 0) return;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "row";
    row.onclick = () => {
      ctx.state.workbench.selectedStep = ids[0] ?? null;
      ctx.navigate("workbench");
    };
    row.append(
      iconBadge("clock"),
      div("row-main", div("row-title", `${title} · ${ids.length} 项`), div("row-sub", hint)),
      chip("处理", "warning"),
    );
    list.appendChild(row);
  };
  add(manual, "待人工操作", "打开工作台，按步骤执行并关联证据。");
  add(blocked, "阻塞步骤", "先处理阻塞原因，再继续执行。");
  add(pending, "待核验", "执行完成后需要独立核验。");
  add(invalidated, "已过期", "上游结果变化，需要重新核对或执行。");
  return card("", cardHead("需要你处理", "按顺序处理，面板不会替你确认。"), list);
}
