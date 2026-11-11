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

type ActionGuide = { title: string; when: string; caution: string };

const ACTION_GUIDE: Record<string, ActionGuide> = {
  retry_step: {
    title: "重试步骤",
    when: "某一步已有尝试，需要再执行一次；工作台会先解析新动作。",
    caution: "旧尝试与证据保留，下游依赖由核心按实际事实标记；不能跳过解析和授权。",
  },
  return_stage: {
    title: "退回上游",
    when: "上游结果变化或需要下游重跑时，对目标步骤创建新尝试。",
    caution: "实际需重跑清单以核心回执为准；旧尝试不会被改写。",
  },
  attach_evidence: {
    title: "补录人工证据",
    when: "自动核验无法覆盖的人工操作，需要绑定当前已完成的尝试补材料。",
    caution: "必须填写操作类型、目标、实际结果和业务对象；附件不能伪造完整证据。",
  },
  revise_pending_steps: {
    title: "修订后续步骤",
    when: "运行中需要按新的已保存用例修订未执行步骤。",
    caution: "需要准确的用例修订、目标步骤和依据确认编号；已执行步骤不能改。",
  },
  narrow_driver: {
    title: "收窄为逐步执行",
    when: "计划驱动需要从按计划执行收窄为逐步执行。",
    caution: "必须是按计划执行的运行；收窄会形成新修订。",
  },
  inspect_case_reuse: {
    title: "检查用例复用",
    when: "判断另一运行的同一用例能否作为本轮有效复用依据。",
    caution: "必须给出两个不同运行的准确快照；不兼容时返回拒绝原因，不授予 R。",
  },
  select_case_reuse: {
    title: "选择用例复用",
    when: "检查通过后，登记本轮采用的复用引用。",
    caution: "人工动作，需原生确认；选择不改写源运行或目标运行的历史事实。",
  },
  record_local_review: {
    title: "记录本地复核",
    when: "对某个准确报告修订给出本地复核结论和说明。",
    caution: "复核不改变报告正文；需要新的复核编号和报告修订。",
  },
  submit_delivery: {
    title: "正式提测",
    when: "交付草稿、源码和核验报告已保存后，提交正式提测。",
    caution: "需要准确的交付、源码快照和报告引用；人工动作，核心原生确认。",
  },
  save_rules: {
    title: "保存规则草稿",
    when: "需要先保存规则修订，再单独发布。",
    caution: "保存草稿不等于发布；发布是另一个受控动作。",
  },
  publish_rules: {
    title: "发布规则",
    when: "规则草稿已核对，准备冻结为可用版本。",
    caution: "发布后版本不可变；需要准确草稿修订和依据。",
  },
  save_plan: {
    title: "保存计划草稿",
    when: "需要保存计划修订但暂不发布。",
    caution: "草稿不产生运行资格；发布仍需覆盖、依据和人工确认。",
  },
  backup: {
    title: "备份工作空间",
    when: "维护或迁移前保存可校验备份。",
    caution: "维护动作，不是业务提交；备份位置与校验以核心回执为准。",
  },
  migrate: {
    title: "迁移工作空间",
    when: "按维护流程迁移格式或位置。",
    caution: "需要可校验备份和活动执行排空；未知活动不得自动迁移。",
  },
  check_source: {
    title: "核实源码",
    when: "需要按已保存绑定和快照核对当前源码来源。",
    caution: "只读核实；不能代替执行或业务核验。",
  },
  plan_confirmation_state: {
    title: "读取计划确认状态",
    when: "核对计划确认、派生许可和缺失依据。",
    caution: "只读；状态来自已保存计划，不重算判定。",
  },
  prepare_approval: {
    title: "准备人工授权",
    when: "需要为某个受控动作准备原生确认材料。",
    caution: "准备不等于授权；确认仍由核心窗口完成。",
  },
  revoke_approval: {
    title: "撤销人工授权",
    when: "已准备的授权不再需要或被替换。",
    caution: "撤销只影响尚未消费的授权；历史事实保留。",
  },
};

function guideFor(action: string): ActionGuide {
  return ACTION_GUIDE[action] ?? {
    title: action,
    when: "核心动作；仅在明确知道参数来源时使用。",
    caution: "参数必须是准确保存的标识；写动作仍受核心守卫和原生确认约束。",
  };
}

function contextPrefill(ctx: AppContext, action: string): { values: Record<string, unknown>; target: string } {
  const state = ctx.state;
  const run = state.run;
  const runId = typeof run?.["run_id"] === "string" ? run["run_id"] : undefined;
  const snapshot = typeof run?.["snapshot_commit_id"] === "string" ? run["snapshot_commit_id"] : undefined;
  const steps = asRecords(run?.["steps"]);
  const selected = state.workbench.selectedStep;
  const step = steps.find(item => item["step_id"] === selected)
    ?? steps.find(item => ["pending_verification", "blocked", "running"].includes(String(item["state"])))
    ?? steps.find(item => item["state"] !== "completed");
  const stepId = typeof step?.["step_id"] === "string" ? step["step_id"] : undefined;
  const attemptId = typeof step?.["current_attempt_id"] === "string" ? step["current_attempt_id"] : undefined;
  const execution = stepId ? state.workbench.executions[stepId] : undefined;
  const planRecord = asRecord(state.planDetail?.["record"]);
  const planId = typeof planRecord?.["plan_id"] === "string"
    ? planRecord["plan_id"]
    : undefined;
  const planRevision = typeof planRecord?.["revision"] === "number"
    ? planRecord["revision"]
    : state.selectedPlan?.revision;
  const issueRecord = asRecord(state.issueDetail?.["record"]);
  const issueId = typeof issueRecord?.["issue_id"] === "string" ? issueRecord["issue_id"] : undefined;
  const issueCase = typeof issueRecord?.["case_id"] === "string" ? issueRecord["case_id"] : undefined;
  const issueRun = typeof issueRecord?.["run_id"] === "string" ? issueRecord["run_id"] : undefined;
  const issueAttempt = typeof issueRecord?.["attempt_id"] === "string" ? issueRecord["attempt_id"] : undefined;
  const reportId = state.selectedReport?.record_id;
  const reportRevision = state.selectedReport?.revision;
  const values: Record<string, unknown> = {};
  const put = (key: string, value: unknown): void => {
    if (value !== undefined && value !== null && value !== "") values[key] = value;
  };
  if (["retry_step", "return_stage", "attach_evidence", "prepare_execution", "execute_step", "verify_pending"].includes(action)) {
    put("run_id", runId);
    put("step_id", stepId);
    put("attempt_id", attemptId);
  }
  if (["authorize_step", "execute_step", "retry_step", "return_stage"].includes(action)) {
    put("execution_action_id", execution?.actionId);
  }
  if (["revise_pending_steps", "narrow_driver"].includes(action)) {
    put("run_id", runId);
    put("base_snapshot_commit_id", snapshot);
  }
  if (["inspect_case_reuse", "select_case_reuse"].includes(action)) {
    put("case_id", issueCase);
    put("target_run_id", runId);
  }
  if (["record_local_review"].includes(action)) {
    put("report_id", reportId);
    put("report_revision", reportRevision);
  }
  if (["update_issue", "close_issue", "record_fix", "request_regression"].includes(action)) {
    put("issue_id", issueId);
  }
  if (["close_issue"].includes(action)) {
    put("case_id", issueCase);
    put("run_id", issueRun);
    put("attempt_id", issueAttempt);
  }
  if (["save_plan", "publish_plan", "plan_confirmation_state", "create_plan_grant", "revoke_plan_grant"].includes(action)) {
    put("plan_id", planId);
    put("plan_revision", planRevision);
  }
  const target = ["retry_step", "return_stage", "attach_evidence", "prepare_execution", "authorize_step", "execute_step", "verify_pending"].includes(action)
    ? (stepId ?? "")
    : "";
  return { values, target };
}

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
  const guide = guideFor(advanced.action);
  const content = div(
    "stack-sm",
    p("用于执行常用界面未覆盖的动作。参数必须来自准确记录；写动作仍受核心守卫与人工确认约束。", "muted subtle"),
    field("动作", selectElement(options, advanced.action || (actions[0] ?? ""), value => {
      advanced.action = value;
      ctx.render();
    })),
    div(
      "stack-sm",
      p(guide.title, "card-title"),
      p(`何时使用：${guide.when}`, "muted subtle"),
      p(`注意：${guide.caution}`, "muted subtle"),
    ),
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
      button("填入当前上下文", () => {
        const prefill = contextPrefill(ctx, advanced.action);
        if (Object.keys(prefill.values).length === 0) {
          ctx.setNotice("当前页面没有可预填的标识；请从工作台、报告或问题详情进入后再试。", "warning");
          return;
        }
        let current: Record<string, unknown> = {};
        try {
          const parsed: unknown = JSON.parse(advanced.parameters.trim() || "{}");
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            current = parsed as Record<string, unknown>;
          }
        } catch {
          // An invalid draft is replaced by the current-context template.
        }
        advanced.parameters = JSON.stringify({ ...current, ...prefill.values }, null, 2);
        if (prefill.target) advanced.target = prefill.target;
        ctx.render();
      }, { icon: "copy" }),
      button("提交动作", () => void submitAdvanced(ctx), {
        variant: "primary",
        icon: READ_ONLY_ACTIONS.has(advanced.action) ? "eye" : "shield",
        disabled: state.busy !== null,
      }),
      button("空间诊断", () => void storageUsage(ctx), { icon: "chart" }),
    ),
    advanced.result ? codeBlock(advanced.result) : null,
  );
  const cardNode = card(
    "",
    cardHead(
      "高级动作",
      READ_ONLY_ACTIONS.has(advanced.action) ? "只读动作直接派发。" : "写动作会按核心合同选择普通写入或原生确认。",
      chip(READ_ONLY_ACTIONS.has(advanced.action) ? "只读" : "写入", READ_ONLY_ACTIONS.has(advanced.action) ? "info" : "warning"),
    ),
    content,
  );
  cardNode.dataset.testid = "advanced-actions";
  return cardNode;
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
