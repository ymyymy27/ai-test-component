/** Diagnostics: real receipts only — capabilities, dependency states, events, advanced dispatch. */

import type { AppContext } from "../context";
import type { Outcome } from "../types";
import { icon } from "../icons";
import { asNumber, asRecord, asRecords, asStrings, label, shortId, toneForState } from "../labels";
import {
  banner,
  button,
  card,
  cardHead,
  chip,
  codeBlock,
  details,
  div,
  facts,
  pageHead,
  p,
  textArea,
  textInput,
} from "../ui";

const READ_ONLY_ACTIONS: ReadonlySet<string> = new Set([
  "query",
  "read_view",
  "events",
  "doctor",
  "storage_usage",
  "copy_repair_brief",
  "check_source",
  "inspect_case_reuse",
  "plan_confirmation_state",
]);

export function renderDiagnostics(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const doctor = state.doctor;
  const page = div("page");
  page.appendChild(
    pageHead(
      "诊断",
      "连接、依赖与高级操作",
      "这里展示核心真实回执；不推断未声明能力，也不把单点故障扩散成整体不可用。",
      button("刷新核心状态", () => void ctx.ensureCore(), { icon: "refresh", disabled: state.busy !== null }),
    ),
  );
  page.appendChild(capabilityCard(ctx));
  page.appendChild(dependencyCard(ctx));
  page.appendChild(eventsCard(ctx));
  page.appendChild(advancedCard(ctx));
  if (doctor) {
    page.appendChild(details("原始 doctor 回执", codeBlock(JSON.stringify(doctor, null, 2))));
  }
  return page;
}

function capabilityCard(ctx: AppContext): HTMLElement {
  const doctor = ctx.state.doctor;
  const actions = asStrings(doctor?.supported_actions);
  const cardNode = card(
    "",
    cardHead(
      "核心能力",
      doctor ? `协议 ${String(doctor["protocol"] ?? "—")} · 工作空间 ${shortId(ctx.state.workspaceId, 16)}` : "尚未连接核心。",
      chip(doctor ? label(doctor["status"]) : "未连接", doctor ? toneForState(doctor["status"]) : "warning"),
    ),
  );
  if (!doctor) {
    cardNode.appendChild(banner("先连接核心；能力列表只在真实回执中出现。", "warning"));
    return cardNode;
  }
  cardNode.appendChild(facts([
    ["状态", String(doctor["status"] ?? "未知")],
    ["协议", String(doctor["protocol"] ?? "—")],
    ["工作空间", ctx.state.workspaceId || "尚未取得"],
    ["项目", ctx.state.project?.project_id ?? "尚未发布"],
    ["可派发动作", `${actions.length} 项`],
  ]));
  if (actions.length) {
    cardNode.appendChild(
      div("inline", ...actions.slice(0, 14).map(action => chip(action, "neutral"))),
    );
    if (actions.length > 14) {
      cardNode.appendChild(details(`全部 ${actions.length} 个动作`, div("inline", ...actions.map(action => chip(action, "neutral")))));
    }
  }
  return cardNode;
}

function dependencyCard(ctx: AppContext): HTMLElement {
  const dependencies = asRecord(ctx.state.doctor?.dependencies);
  const conditions = asRecords(dependencies?.["dependencies"]);
  const actionDependencies = asRecord(dependencies?.["action_dependencies"]) ?? {};
  if (conditions.length === 0) {
    return card("", cardHead("依赖状态", "核心未声明依赖条件。"), p("没有可展示的依赖事实。", "muted subtle"));
  }
  const notReady = new Set<string>();
  const list = div("stack-sm");
  for (const condition of conditions) {
    const key = typeof condition["key"] === "string" ? condition["key"] : "unknown";
    const state = String(condition["state"] ?? "unknown");
    const reason = typeof condition["reason"] === "string" ? condition["reason"] : "";
    if (state !== "ready") notReady.add(key);
    list.appendChild(
      div(
        "row",
        div("row-icon", icon(state === "ready" ? "check-circle" : "alert", 15)),
        div("row-main", div("row-title", key), div("row-sub", state === "ready" ? "已就绪" : (reason || "没有给出原因"))),
        chip(label(state), toneForState(state)),
      ),
    );
  }
  const affected = asStrings(ctx.state.doctor?.supported_actions).filter(action =>
    asStrings(actionDependencies[action]).some(key => notReady.has(key)));
  return card(
    "",
    cardHead("依赖状态", "按具体动作的真实依赖判断；单个适配器故障不等于全部能力不可用。"),
    list,
    affected.length
      ? banner(`当前依赖可能拒绝的动作：${affected.join("、")}`, "warning")
      : banner("当前声明的依赖均为 ready。", "success"),
  );
}

function eventsCard(ctx: AppContext): HTMLElement {
  const events = ctx.state.events;
  const list = div("stack-sm");
  for (const event of events) {
    const sequence = asNumber(event["event_sequence"], -1);
    const commit = asNumber(event["commit_sequence"], -1);
    const revision = asNumber(event["revision"], -1);
    list.appendChild(
      div(
        "row",
        div("row-main",
          div("row-title", String(event["event_type"] ?? "?")),
          div("row-sub", `#${sequence >= 0 ? sequence : "?"} · 提交 ${commit >= 0 ? commit : "?"} · ${String(event["record_id"] ?? "?")}@${revision >= 0 ? revision : "?"}`),
        ),
      ),
    );
  }
  return card(
    "",
    cardHead(
      "已提交事件",
      ctx.state.eventCursor ? `当前游标：${shortId(ctx.state.eventCursor, 18)}` : "尚未取得游标。",
      button(ctx.state.eventCursor ? "续读新事件" : "读取最近事件", () => void ctx.loadEvents(), { icon: "list", disabled: ctx.state.hostAvailable === false }),
    ),
    ctx.state.eventNote ? banner(ctx.state.eventNote, "info") : null,
    events.length ? list : p("尚未读取事件，或回执里没有事件行。", "muted subtle"),
  );
}

function advancedCard(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const advanced = state.advanced;
  const actions = asStrings(state.doctor?.supported_actions);
  const options = actions.map(action => ({ value: action, label: action }));
  if (options.length === 0) {
    return card("", cardHead("高级动作", "连接核心后按真实能力列出动作。"), p("当前没有可派发动作。", "muted subtle"));
  }
  if (!advanced.action) advanced.action = actions[0] ?? "";
  const content = div(
    "stack-sm",
    p("用于执行常用界面未覆盖的动作。参数必须来自准确记录；写动作仍受核心守卫与人工确认约束。", "muted subtle"),
    field("动作", selectElement(options, advanced.action || (actions[0] ?? ""), value => {
      advanced.action = value;
      ctx.render();
    })),
    field("参数 JSON", textArea(advanced.parameters, value => { advanced.parameters = value; }, {
      rows: 6,
      placeholder: '{"run_id":"..."}',
      ariaLabel: "动作参数 JSON",
    })),
    div(
      "form-grid",
      field("目标 target（可留空）", textInput(advanced.target, value => { advanced.target = value; })),
      field("预期修订 expected_revision", textInput(advanced.expected, value => { advanced.expected = value; }, { placeholder: "0" })),
    ),
    div(
      "hero-actions",
      button("提交动作", () => void submitAdvanced(ctx), {
        variant: "primary",
        icon: READ_ONLY_ACTIONS.has(advanced.action) ? "eye" : "shield",
        disabled: state.busy !== null,
      }),
      button("空间诊断", () => void storageUsage(ctx), { icon: "chart" }),
    ),
    advanced.result ? codeBlock(advanced.result) : null,
  );
  return card(
    "",
    cardHead(
      "高级动作",
      READ_ONLY_ACTIONS.has(advanced.action) ? "只读动作直接派发。" : "写动作会按核心合同选择普通写入或原生确认。",
      chip(READ_ONLY_ACTIONS.has(advanced.action) ? "只读" : "写入", READ_ONLY_ACTIONS.has(advanced.action) ? "info" : "warning"),
    ),
    content,
  );
}

async function submitAdvanced(ctx: AppContext): Promise<void> {
  const advanced = ctx.state.advanced;
  if (!advanced.action) return;
  let parameters: unknown = {};
  try {
    parameters = JSON.parse(advanced.parameters.trim() || "{}");
  } catch {
    advanced.result = "参数必须是 JSON 对象。";
    advanced.tone = "danger";
    ctx.render();
    return;
  }
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) {
    advanced.result = "参数必须是 JSON 对象。";
    advanced.tone = "danger";
    ctx.render();
    return;
  }
  const expected = advanced.expected.trim() ? Number(advanced.expected) : 0;
  if (!Number.isSafeInteger(expected) || expected < 0) {
    advanced.result = "预期修订必须是非负整数。";
    advanced.tone = "danger";
    ctx.render();
    return;
  }
  const target = advanced.target.trim() || undefined;
  const objectParameters = parameters as Record<string, unknown>;
  let outcome: Outcome;
  if (READ_ONLY_ACTIONS.has(advanced.action)) {
    const result = await ctx.readOnly(advanced.action, objectParameters);
    outcome = result
      ? { action: advanced.action, status: "ok", response: { result } }
      : { action: advanced.action, status: "refused", message: "只读动作未返回结果。" };
  } else {
    outcome = await ctx.write(advanced.action, objectParameters, {
      target: target ?? null,
      expectedRevision: expected,
      intentId: ctx.intent(advanced.action, objectParameters, target, expected),
    });
  }
  advanced.result = JSON.stringify(
    outcome.response ?? { status: outcome.status, code: outcome.code, message: outcome.message },
    null,
    2,
  );
  advanced.tone = outcome.status === "ok" ? "success" : "danger";
  ctx.render();
}

function selectElement(
  options: ReadonlyArray<{ value: string; label: string }>,
  value: string,
  onChange: (value: string) => void,
): HTMLSelectElement {
  const node = document.createElement("select");
  node.className = "select";
  for (const option of options) {
    const item = document.createElement("option");
    item.value = option.value;
    item.textContent = option.label;
    node.appendChild(item);
  }
  node.value = value;
  node.onchange = () => onChange(node.value);
  return node;
}

function field(labelText: string, control: HTMLElement): HTMLElement {
  const label = document.createElement("label");
  label.className = "field";
  const title = document.createElement("span");
  title.className = "field-label";
  title.textContent = labelText;
  label.append(title, control);
  return label;
}

async function storageUsage(ctx: AppContext): Promise<void> {
  const result = await ctx.readOnly("storage_usage", {});
  if (!result) return;
  ctx.showDialog("空间诊断", codeBlock(JSON.stringify(result, null, 2)));
}
