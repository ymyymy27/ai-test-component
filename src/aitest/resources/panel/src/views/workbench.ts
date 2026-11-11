/** Workbench: inspect one step, prepare -> authorize -> execute -> verify, then read evidence. */

import type { AppContext } from "../context";
import { icon } from "../icons";
import { testTabs } from "../test-tabs";
import {
  asNumber,
  asRecord,
  asRecords,
  asStrings,
  gapHint,
  gapText,
  label,
  omittedReason,
  shortId,
  toneForState,
} from "../labels";
import type { ExecutionState } from "../types";
import {
  banner,
  button,
  card,
  cardHead,
  checkbox,
  chip,
  details,
  div,
  empty,
  facts,
  field,
  loading,
  pageHead,
  p,
  progress,
  select,
  textArea,
  textInput,
} from "../ui";

export function renderWorkbench(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const page = div("page");
  page.appendChild(testTabs(ctx, "steps"));
  page.appendChild(
    pageHead(
      "工作台",
      "按步骤执行与核验",
      "操作解析、授权、执行和核验是不同事实；执行请求返回不等于业务通过。",
      button("刷新", () => void ctx.refreshCurrent(), { icon: "refresh", disabled: state.busy !== null }),
    ),
  );
  if (!state.project?.run_id) {
    page.appendChild(
      card(
        "",
        empty(
          "terminal",
          "还没有可用的运行",
          "先在准备向导中保存计划、准备并登记运行，工作台会显示每个步骤的事实。",
          button("开始准备测试", () => ctx.navigate("wizard"), { variant: "primary", icon: "arrow-right" }),
        ),
      ),
    );
    return page;
  }
  if (!state.run) {
    page.appendChild(card("", loading("正在读取本次运行…")));
    return page;
  }
  const run = state.run;
  const steps = asRecords(run["steps"]);
  if (steps.length === 0) {
    page.appendChild(card("", empty("list", "运行没有步骤", "读取到的运行视图没有步骤；请核对准备记录。")));
    return page;
  }
  const selectedId = chooseStep(ctx, steps);
  const selected = steps.find(step => step["step_id"] === selectedId) ?? steps[0];
  page.appendChild(runSummary(ctx, run));
  page.appendChild(revisionCard(ctx, run));
  page.appendChild(batchAuthorizationCard(ctx, run));
  page.appendChild(reuseCard(ctx, run));
  const grid = div("workbench-grid");
  grid.appendChild(stepRail(ctx, steps, selectedId));
  grid.appendChild(stepDetail(ctx, run, selected));
  page.appendChild(grid);
  return page;
}

function chooseStep(ctx: AppContext, steps: Record<string, unknown>[]): string {
  const known = new Set(steps.map(step => String(step["step_id"] ?? "")));
  const current = ctx.state.workbench.selectedStep;
  if (current && known.has(current)) return current;
  const pending = Object.keys(ctx.state.workbench.executions).find(key => known.has(key));
  if (pending) {
    ctx.state.workbench.selectedStep = pending;
    return pending;
  }
  const preferred = steps.find(step =>
    ["blocked", "pending_verification", "running"].includes(String(step["state"])))
    ?? steps.find(step => step["state"] !== "completed")
    ?? steps[0];
  const selected = String(preferred["step_id"] ?? "");
  ctx.state.workbench.selectedStep = selected;
  return selected;
}



function reuseCard(ctx: AppContext, run: Record<string, unknown>): HTMLElement {
  const state = ctx.state.workbench.reuse;
  const steps = asRecords(run["steps"]);
  const caseIds = [...new Set(steps.map(step => String(step["case_id"] ?? "")).filter(Boolean))];
  if (!state.caseId || !caseIds.includes(state.caseId)) state.caseId = caseIds[0] ?? "";
  const caseSelect = select(
    caseIds.map(caseId => ({ value: caseId, label: shortId(caseId, 18) })),
    state.caseId,
    value => { state.caseId = value; },
    caseIds.length === 0,
  );
  const sourceInput = textInput(state.sourceRunId, value => {
    state.sourceRunId = value;
    state.inspection = null;
  }, { placeholder: "来源运行编号" });
  const note = p(state.note, "field-help");
  const targetRunId = typeof run["run_id"] === "string" ? run["run_id"] : "";
  const inspect = button("核对复用资格", async () => {
    state.note = "";
    state.inspection = null;
    const sourceRunId = state.sourceRunId.trim();
    if (!sourceRunId) {
      state.note = "请输入来源运行编号。";
      ctx.render();
      return;
    }
    if (sourceRunId === targetRunId) {
      state.note = "来源运行必须是另一次已提交运行。";
      ctx.render();
      return;
    }
    if (!state.caseId) {
      state.note = "当前运行没有可复用的用例。";
      ctx.render();
      return;
    }
    const targetRef = asRecord(run["snapshot_ref"]);
    if (!targetRef) {
      state.note = "当前运行视图缺少快照内容身份，不能核对复用；请升级核心后重试。";
      ctx.render();
      return;
    }
    ctx.state.busy = "reuse-inspect";
    ctx.render();
    const sourceView = await ctx.read("run", { run_id: sourceRunId });
    const sourceRef = asRecord(sourceView?.["snapshot_ref"]);
    if (!sourceView || !sourceRef) {
      ctx.state.busy = null;
      state.note = sourceView
        ? "来源运行视图缺少快照内容身份，不能核对复用。"
        : "来源运行读取未完成。";
      ctx.render();
      return;
    }
    const inspection = await ctx.readOnly("inspect_case_reuse", {
      case_id: state.caseId,
      source_run_id: sourceRunId,
      source_snapshot: sourceRef,
      target_run_id: targetRunId,
      target_snapshot: targetRef,
    });
    ctx.state.busy = null;
    if (!inspection) {
      state.note = "复用核对未返回可核实结果。";
      ctx.render();
      return;
    }
    state.inspection = inspection;
    state.note = "";
    ctx.render();
  }, { icon: "search", disabled: ctx.state.busy !== null });

  const inspection = state.inspection;
  const status = inspection ? String(inspection["status"] ?? "") : "";
  const reasons = inspection ? asStrings(inspection["denial_reasons"]) : [];
  const mapping = inspection ? asRecords(inspection["step_mapping"]) : [];
  const register = button("登记复用选择", async () => {
    if (!inspection || status !== "unverified") return;
    state.note = "";
    ctx.state.busy = "reuse-select";
    ctx.render();
    const rows = await ctx.query({ aggregate_kind: "reuse_decision", limit: 50 });
    let expected = 0;
    for (const row of rows) {
      const detail = await ctx.read("reuse_decision", {
        record_id: row.record_id,
        revision: row.revision,
      });
      const record = asRecord(detail?.["record"]);
      if (record?.["target_run_id"] === targetRunId) {
        expected = Math.max(expected, asNumber(row.revision, 0));
      }
    }
    const parameters = {
      case_id: state.caseId,
      source_run_id: String(inspection["source_run_id"] ?? ""),
      source_snapshot: asRecord(inspection["source_snapshot"]) ?? {},
      target_run_id: targetRunId,
      target_snapshot: asRecord(inspection["target_snapshot"]) ?? {},
    };
    const outcome = await ctx.write("select_case_reuse", parameters, {
      target: state.caseId,
      expectedRevision: expected,
      intentId: ctx.intent("select_case_reuse", parameters, state.caseId, expected),
      controlled: true,
    });
    ctx.state.busy = null;
    if (outcome.status !== "ok") {
      state.note = outcome.message ?? "复用选择未保存。";
      ctx.render();
      return;
    }
    state.inspection = null;
    state.note = "复用选择已保存；资格仍由后续执行事实重新派生，不会直接写成通过。";
    ctx.setNotice(state.note, "success");
    await ctx.refreshCurrent();
  }, {
    variant: "primary",
    icon: "shield",
    disabled: !inspection || status !== "unverified" || ctx.state.busy !== null,
  });

  return card(
    "",
    cardHead("复用历史结果", "只核对精确来源运行与当前目标快照；核对不等于授予复用资格。"),
    p("复用只适用于当前运行尚未开始的用例；来源必须有完整执行与核验材料。", "muted subtle"),
    field("目标用例", caseSelect),
    field("来源运行编号", sourceInput),
    div("hero-actions", inspect),
    inspection
      ? div(
        "stack-sm",
        facts([
          ["核对状态", chip(label(status), toneForState(status))],
          ["来源运行", String(inspection["source_run_id"] ?? "—")],
          ["步骤映射", String(mapping.length)],
        ]),
        reasons.length
          ? div(
            "stack-sm",
            p("核心拒绝/未证实原因（原样显示）：", "muted"),
            ...reasons.map(reason => p(reason, "muted subtle")),
          )
          : null,
        status === "unverified"
          ? p("材料未发现不兼容；登记后仍需按执行事实重新派生资格。", "muted subtle")
          : banner("材料不兼容或未完整核对，不能登记复用选择。", "warning"),
        div("hero-actions", register),
      )
      : null,
    note,
  );
}

function batchAuthorizationCard(ctx: AppContext, run: Record<string, unknown>): HTMLElement {
  const steps = asRecords(run["steps"]);
  const candidates = steps.filter(step =>
    step["state"] !== "completed" && step["current_attempt_id"] == null);
  const selected = new Set(candidates.map(step => String(step["step_id"] ?? "")));
  const note = p("", "field-help");
  const list = div("stack-sm");
  for (const step of candidates) {
    const stepId = String(step["step_id"] ?? "");
    list.appendChild(
      checkbox(`步骤 ${shortId(stepId, 12)}（${label(step["state"])}）`, true, checked => {
        if (checked) selected.add(stepId);
        else selected.delete(stepId);
      }),
    );
  }
  const runId = typeof run["run_id"] === "string" ? run["run_id"] : "";
  const submit = button("准备并逐项授权", async () => {
    note.textContent = "";
    if (!runId) {
      note.textContent = "运行视图缺少运行编号。";
      return;
    }
    if (selected.size === 0) {
      note.textContent = "至少选择一个尚未执行的步骤。";
      return;
    }
    ctx.state.busy = "batch-authorize";
    const prepared: Array<{ stepId: string; actionId: string; intent: string }> = [];
    for (const step of candidates) {
      const stepId = String(step["step_id"] ?? "");
      if (!selected.has(stepId)) continue;
      const execution = ctx.state.workbench.executions[stepId] ?? {
        intent: ctx.intent("step-flow", { step_id: stepId }, stepId, 0),
      };
      ctx.state.workbench.executions[stepId] = execution;
      let actionId = execution.actionId ?? "";
      if (!actionId) {
        const outcome = await ctx.write(
          "prepare_execution",
          { run_id: runId, step_id: stepId },
          { target: stepId, expectedRevision: 0, intentId: execution.intent },
        );
        const result = asRecord(outcome.response?.["result"]) ?? {};
        actionId = typeof result["execution_action_id"] === "string"
          ? result["execution_action_id"]
          : "";
        if (outcome.status !== "ok" || !actionId) {
          ctx.state.busy = null;
          note.textContent = outcome.message ?? `步骤 ${stepId} 没有解析出可授权动作。`;
          ctx.setNotice(note.textContent, "danger");
          return;
        }
        execution.actionId = actionId;
        execution.authorized = false;
        execution.executed = false;
      }
      prepared.push({ stepId, actionId, intent: execution.intent });
    }
    let confirmed = 0;
    for (const item of prepared) {
      const outcome = await ctx.write(
        "authorize_step",
        { execution_action_id: item.actionId, record_revision: 1 },
        {
          target: item.stepId,
          expectedRevision: 0,
          intentId: item.intent,
          controlled: true,
        },
      );
      const itemResult = asRecord(outcome.response?.["result"]) ?? {};
      if (outcome.status === "ok" && typeof itemResult["authorization_id"] === "string") {
        const execution = ctx.state.workbench.executions[item.stepId];
        if (execution) execution.authorized = true;
        confirmed += 1;
      } else if (outcome.status !== "ok") {
        ctx.state.busy = null;
        note.textContent = outcome.message ?? `步骤 ${item.stepId} 未获授权。`;
        ctx.setNotice(note.textContent, "danger");
        return;
      }
    }
    ctx.state.busy = null;
    const pending = prepared.length - confirmed;
    note.textContent = pending > 0
      ? `已确认 ${confirmed} 项；仍有 ${pending} 项未确认，未确认项不会执行。`
      : `已确认 ${confirmed} 项；可以逐项执行。`;
    ctx.setNotice(note.textContent, pending > 0 ? "warning" : "success");
    ctx.render();
  }, {
    variant: "primary",
    icon: "shield",
    disabled: candidates.length === 0 || ctx.state.busy !== null,
  });
  return card(
    "",
    cardHead("逐项授权", "先解析动作，再在面板内逐项确认；不打开原生窗口。"),
    candidates.length
      ? list
      : p("当前没有未执行且未开始记录的步骤可批量授权。", "muted subtle"),
    note,
    div("hero-actions", submit),
    p("批量窗口中的每一项仍需单独确认；取消或未确认的项不会写成已授权。", "muted subtle"),
  );
}

function runSummary(ctx: AppContext, run: Record<string, unknown>): HTMLElement {
  const control = run["control_state"];
  const autoTotal = asNumber(run["automatic_step_count"]);
  const autoDone = asNumber(run["automatic_completed_count"]);
  const summary = asRecord(run["selected_summary"]) ?? {};
  const verified = asNumber(summary["verified_count"]);
  const unverified = asNumber(summary["unverified_count"]);
  const manual = asStrings(run["manual_pending_step_ids"]).length;
  const controls = div("hero-actions");
  const runId = typeof run["run_id"] === "string" ? run["run_id"] : null;
  const base = typeof run["snapshot_commit_id"] === "string" ? run["snapshot_commit_id"] : "";
  if (runId && base) {
    controls.appendChild(button("暂停", async () => {
      await controlRun(ctx, "pause_run", runId, base);
    }, { icon: "pause", disabled: control !== "running" }));
    controls.appendChild(button("继续", async () => {
      await controlRun(ctx, "resume_run", runId, base);
    }, { icon: "play", disabled: control !== "paused" && control !== "pause_requested" }));
    controls.appendChild(button("取消", async () => {
      await controlRun(ctx, "cancel_run", runId, base);
    }, { icon: "stop", variant: "danger", disabled: !["not_started", "running", "paused", "pause_requested", "pending_verification"].includes(String(control)) }));
  }
  return card(
    "",
    cardHead(
      `运行 ${shortId(run["run_id"], 12)}`,
      `${label(run["tier"])} · ${label(run["driver"])} · 计划 ${String(run["plan_revision_ref"] ?? "—")}`,
      chip(label(control), toneForState(control)),
    ),
    div(
      "stats",
      div("stat", div("stat-value", String(verified)), div("stat-label", "已验证")),
      div("stat", div("stat-value", String(unverified)), div("stat-label", "未验证")),
      div("stat", div("stat-value", String(manual)), div("stat-label", "待人工")),
      div("stat", div("stat-value", `${autoDone}/${autoTotal}`), div("stat-label", "自动步骤")),
    ),
    autoTotal > 0 ? div("", progress(autoDone, autoTotal)) : null,
    controls,
  );
}


type RevisionSelection = {
  enabled: boolean;
  revision: string;
  steps: Set<string>;
};

function revisionCard(ctx: AppContext, run: Record<string, unknown>): HTMLElement {
  const basis = ctx.state.workbench.revisionBasis;
  const driver = String(run["driver"] ?? "");
  const tier = String(run["tier"] ?? "");
  const narrow = button("收窄为逐步执行", () => confirmNarrowDriver(ctx, run), {
    icon: "arrow-right",
    disabled: driver !== "planned" || ctx.state.busy !== null,
    title: driver === "planned" ? "把自动推进改为逐步执行" : "当前运行不是计划驱动",
  });
  if (!basis) {
    return card(
      "",
      cardHead("运行中修订", "只读取计划确认状态；提交前不会写入任何记录。"),
      p("修订未执行步骤前，需要先读取计划的冻结用例修订和依据确认编号。", "muted subtle"),
      div(
        "hero-actions",
        button("读取修订依据", () => void loadRevisionBasis(ctx, run), {
          icon: "refresh",
          disabled: ctx.state.busy !== null,
        }),
        narrow,
      ),
    );
  }
  const state = String(basis["state"] ?? "");
  const cases = asRecords(basis["cases"]);
  const steps = asRecords(run["steps"]);
  const selections = new Map<string, RevisionSelection>();
  const caseRows = div("stack-sm");
  for (const item of cases) {
    const caseId = String(item["case_id"] ?? "");
    if (!caseId) continue;
    const frozenRevision = asNumber(item["case_revision"], 0);
    const basisId = typeof item["basis_confirmation_id"] === "string"
      ? item["basis_confirmation_id"]
      : "";
    const pending = steps.filter(step =>
      step["case_id"] === caseId && step["state"] !== "completed");
    const selectable = pending.filter(step => step["current_attempt_id"] == null);
    const entry: RevisionSelection = {
      enabled: selectable.length > 0 && Boolean(basisId),
      revision: "",
      steps: new Set(selectable.map(step => String(step["step_id"] ?? ""))),
    };
    selections.set(caseId, entry);
    const stepChoices = selectable.length
      ? div("stack-sm", ...selectable.map(step => {
        const stepId = String(step["step_id"] ?? "");
        return checkbox(`步骤 ${shortId(stepId, 12)}（${label(step["state"])}）`, true, checked => {
          if (checked) entry.steps.add(stepId);
          else entry.steps.delete(stepId);
        });
      }))
      : p("该用例没有可改写的未执行步骤。", "muted subtle");
    caseRows.appendChild(
      div(
        "stack-sm",
        checkbox(
          `修订用例 ${shortId(caseId, 14)}（当前修订 ${frozenRevision}）`,
          entry.enabled,
          checked => { entry.enabled = checked; },
          selectable.length === 0,
        ),
        field(
          "新用例修订",
          textInput("", value => { entry.revision = value; }, {
            type: "number",
            placeholder: `必须是已保存且大于 ${frozenRevision} 的修订`,
          }),
          "只接收已经保存的用例记录；不会在修订请求里内联或改写用例内容。",
        ),
        stepChoices,
        basisId
          ? p(`依据确认：${shortId(basisId, 18)}`, "muted tiny")
          : p("该用例没有依据确认编号，不能提交修订；请先完成计划依据派生。", "muted subtle"),
      ),
    );
  }
  const reason = textInput("", () => {}, { placeholder: "说明为什么修订未执行步骤" });
  const note = p("", "field-help");
  const submit = button("提交运行中修订", async () => {
    note.textContent = "";
    if (tier === "full") {
      note.textContent = "完整验证运行不允许改写计划内容；只能收窄驱动或新建运行。";
      return;
    }
    if (state !== "ready") {
      note.textContent = "计划确认尚未就绪，不能提交修订；先核对缺失的依据确认。";
      return;
    }
    const changes: Array<Record<string, unknown>> = [];
    const confirmationIds = new Set<string>();
    for (const item of cases) {
      const caseId = String(item["case_id"] ?? "");
      const entry = selections.get(caseId);
      if (!caseId || !entry?.enabled) continue;
      const nextRevision = Number(entry.revision);
      const currentRevision = asNumber(item["case_revision"], 0);
      if (!Number.isInteger(nextRevision) || nextRevision <= currentRevision) {
        note.textContent = `${caseId} 的新修订必须是大于 ${currentRevision} 的整数。`;
        return;
      }
      if (entry.steps.size === 0) {
        note.textContent = `${caseId} 至少选择一个未执行步骤。`;
        return;
      }
      const basisId = item["basis_confirmation_id"];
      if (typeof basisId !== "string" || !basisId) {
        note.textContent = `${caseId} 缺少依据确认编号。`;
        return;
      }
      confirmationIds.add(basisId);
      changes.push({
        case_id: caseId,
        record_revision: nextRevision,
        target_step_ids: [...entry.steps].sort(),
      });
    }
    changes.sort((left, right) => String(left["case_id"]).localeCompare(String(right["case_id"])));
    if (changes.length === 0) {
      note.textContent = "至少选择一个用例并勾选未执行步骤。";
      return;
    }
    if (!reason.value.trim()) {
      note.textContent = "请填写修订原因。";
      return;
    }
    const runId = typeof run["run_id"] === "string" ? run["run_id"] : "";
    const base = typeof run["snapshot_commit_id"] === "string" ? run["snapshot_commit_id"] : "";
    if (!runId || !base) {
      note.textContent = "运行视图缺少运行编号或快照边界，不能提交修订。";
      return;
    }
    const parameters = {
      run_id: runId,
      base_snapshot_commit_id: base,
      case_changes: changes,
      confirmation_ids: [...confirmationIds].sort(),
      reason: reason.value.trim(),
    };
    ctx.state.busy = "revision";
    note.textContent = "正在请求核心确认并提交修订…";
    const outcome = await ctx.write("revise_pending_steps", parameters, {
      target: runId,
      expectedRevision: 0,
      intentId: ctx.intent("revise_pending_steps", parameters, runId, 0),
      controlled: true,
    });
    ctx.state.busy = null;
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "运行中修订未完成。";
      ctx.setNotice(note.textContent, "danger");
      return;
    }
    const result = asRecord(outcome.response?.["result"]) ?? {};
    const affected = asStrings(result["affected_step_ids"]);
    const message = affected.length
      ? `修订已保存；受影响步骤：${affected.join("、")}`
      : "修订已保存；当前运行事实请刷新核对。";
    ctx.state.workbench.revisionBasis = null;
    ctx.setNotice(message, "success");
    await ctx.refreshCurrent();
  }, { variant: "primary", icon: "shield", disabled: ctx.state.busy !== null || tier === "full" });
  return card(
    "",
    cardHead("运行中修订", "只改写尚未执行的步骤；已记录事实、正在执行的步骤和必测范围不能借修订删除。"),
    facts([
      ["计划确认", String(basis["plan_confirmation_id"] ?? "—")],
      ["确认状态", chip(label(state), toneForState(state))],
      ["计划记录修订", String(basis["plan_record_revision"] ?? "—")],
    ]),
    tier === "full"
      ? banner("完整验证（full）运行不允许改写计划内容；可收窄驱动，或新建一次运行。", "warning")
      : state === "ready"
        ? null
        : banner("计划确认未就绪：缺少依据确认或许可。请先核对下方用例，再提交修订。", "warning"),
    cases.length ? caseRows : p("计划确认状态没有返回可修订用例。", "muted subtle"),
    field("修订原因", reason),
    note,
    div("hero-actions", submit, narrow),
    p("提交会打开核心原生确认窗口；面板不代替确认，也不会把回执直接当成通过。", "muted subtle"),
  );
}

async function loadRevisionBasis(ctx: AppContext, run: Record<string, unknown>): Promise<void> {
  const ref = typeof run["plan_revision_ref"] === "string" ? run["plan_revision_ref"] : "";
  const at = ref.lastIndexOf("@");
  const planId = at > 0 ? ref.slice(0, at) : "";
  const revision = at > 0 ? Number(ref.slice(at + 1)) : 0;
  if (!planId || !Number.isInteger(revision) || revision < 1) {
    ctx.setNotice("运行视图没有可解析的计划修订标识，无法读取修订依据。", "warning");
    return;
  }
  ctx.state.busy = "revision-basis";
  ctx.render();
  const result = await ctx.readOnly("plan_confirmation_state", {
    plan_id: planId,
    plan_revision: revision,
  });
  ctx.state.busy = null;
  if (!result) {
    ctx.render();
    return;
  }
  ctx.state.workbench.revisionBasis = result;
  ctx.render();
}

function confirmNarrowDriver(ctx: AppContext, run: Record<string, unknown>): void {
  const runId = typeof run["run_id"] === "string" ? run["run_id"] : "";
  const base = typeof run["snapshot_commit_id"] === "string" ? run["snapshot_commit_id"] : "";
  const reason = textInput("", () => {}, { placeholder: "说明为什么收窄为逐步执行" });
  const note = p("", "field-help");
  const submit = button("确认收窄", async () => {
    note.textContent = "";
    if (!reason.value.trim()) {
      note.textContent = "请填写收窄原因。";
      return;
    }
    if (!runId || !base) {
      note.textContent = "运行视图缺少运行编号或快照边界。";
      return;
    }
    const parameters = {
      run_id: runId,
      base_snapshot_commit_id: base,
      case_changes: [],
      confirmation_ids: [],
      reason: reason.value.trim(),
    };
    const outcome = await ctx.write("narrow_driver", parameters, {
      target: runId,
      expectedRevision: 0,
      intentId: ctx.intent("narrow_driver", parameters, runId, 0),
      controlled: true,
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "收窄驱动未完成。";
      return;
    }
    ctx.closeDialog();
    ctx.setNotice("驱动收窄请求已返回；请刷新核对当前驱动。", "success");
    await ctx.refreshCurrent();
  }, { variant: "primary", icon: "shield" });
  ctx.showDialog(
    "收窄为逐步执行",
    div(
      "stack",
      p("收窄只改变执行推进方式，不改写计划内容或已记录事实。", "muted"),
      field("收窄原因", reason),
      note,
      div(
        "hero-actions",
        button("取消", () => ctx.closeDialog(), { variant: "ghost" }),
        submit,
      ),
    ),
  );
}

async function controlRun(ctx: AppContext, action: string, runId: string, base: string): Promise<void> {
  const parameters = { run_id: runId, base_snapshot_commit_id: base };
  const outcome = await ctx.write(action, parameters, {
    target: runId,
    expectedRevision: 0,
    intentId: ctx.intent(action, parameters, runId, 0),
  });
  ctx.setNotice(outcome.status === "ok" ? "运行控制请求已返回。" : (outcome.message ?? "运行控制未完成。"), outcome.status === "ok" ? "info" : "danger");
  await ctx.refreshCurrent();
}

function stepRail(ctx: AppContext, steps: Record<string, unknown>[], selectedId: string): HTMLElement {
  const rail = div("step-list");
  rail.setAttribute("aria-label", "运行步骤");
  steps.forEach((step, index) => {
    const stepId = String(step["step_id"] ?? "");
    const state = String(step["state"] ?? "");
    const item = document.createElement("button");
    item.type = "button";
    item.className = `step-card${stepId === selectedId ? " selected" : ""}`;
    item.onclick = () => {
      ctx.state.workbench.selectedStep = stepId;
      ctx.state.workbench.note = "";
      ctx.render();
    };
    const dot = div("step-dot-mini");
    dot.dataset.state = state;
    item.append(
      dot,
      div(
        "step-card-main",
        div("step-card-title", `第 ${index + 1} 步 · ${shortId(step["case_id"], 10)}`),
        div("step-card-sub", `${label(state)}${step["invalidated"] === true ? " · 已过期" : ""}`),
      ),
    );
    rail.appendChild(item);
  });
  return rail;
}

function stepDetail(ctx: AppContext, run: Record<string, unknown>, step: Record<string, unknown>): HTMLElement {
  const stepId = String(step["step_id"] ?? "");
  const state = String(step["state"] ?? "");
  const caseId = String(step["case_id"] ?? "");
  const stepIndex = asRecords(run["steps"]).findIndex(item => item["step_id"] === stepId);
  const required = asStrings(run["required_case_ids"]).includes(caseId);
  const invalidated = step["invalidated"] === true;
  const attempt = typeof step["current_attempt_id"] === "string" ? step["current_attempt_id"] : null;
  const conclusion = ctx.state.workbench.conclusions[stepId];
  const executions = ctx.state.workbench.executions;
  const execution: ExecutionState = executions[stepId] ?? {
    intent: ctx.intent("step-flow", { step_id: stepId }, stepId, 0),
  };
  executions[stepId] = execution;

  const gaps = asStrings(step["gap_ids"]);
  const gapList = div("stack-sm");
  for (const code of gaps) {
    gapList.appendChild(
      div(
        "row",
        div("row-icon", icon("alert", 15)),
        div("row-main", div("row-title", gapText(code)), gapHint(code) ? div("row-sub", gapHint(code)) : null),
      ),
    );
  }

  const actions = div("hero-actions");
  if (state === "completed") {
    actions.appendChild(banner("该步骤已完成并取得有效核验。", "success"));
  } else if (!execution.actionId) {
    actions.appendChild(button("查看将执行的操作", async () => {
      await prepareExecution(ctx, run, stepId, execution, caseId);
    }, { variant: "primary", icon: "search", disabled: ctx.state.busy !== null }));
  } else if (!execution.authorized) {
    actions.appendChild(button("确认并授权", async () => {
      await authorizeStep(ctx, execution, stepId);
    }, { variant: "primary", icon: "shield", disabled: ctx.state.busy !== null }));
  } else if (!execution.executed) {
    actions.appendChild(button("执行已确认的操作", async () => {
      await executeStep(ctx, execution, stepId);
    }, { variant: "primary", icon: "play", disabled: ctx.state.busy !== null }));
  } else if (attempt) {
    actions.appendChild(button("核验这次执行结果", async () => {
      await verifyStep(ctx, run, stepId, attempt);
    }, { variant: "primary", icon: "check", disabled: ctx.state.busy !== null }));
  } else {
    actions.appendChild(button("核实执行状态", () => void ctx.refreshCurrent(), { variant: "primary", icon: "refresh" }));
  }
  actions.appendChild(button("核实执行状态", () => void ctx.refreshCurrent(), { icon: "refresh", disabled: ctx.state.busy !== null }));

  const evidenceIds = asStrings(step["evidence_ids"]);
  const evidenceRevisions = Array.isArray(step["evidence_revisions"]) ? step["evidence_revisions"] : [];
  const evidenceList = div("stack-sm");
  evidenceIds.forEach((evidenceId, index) => {
    const revision = asNumber(evidenceRevisions[index], 1);
    evidenceList.appendChild(
      button(`查看证据 ${shortId(evidenceId, 12)} · 修订 ${revision}`, () => void openEvidence(ctx, evidenceId, revision), {
        icon: "eye",
        className: "row",
      }),
    );
  });

  const external = details(
    "导入外部结果（替代路径）",
    p("记录实际操作、输出与断言值；提交后由核心判定缺口，不会自动写成通过。", "muted subtle"),
    externalImportForm(ctx, run, step),
  );

  return div(
    "stack",
    card(
      "",
      cardHead(
        `第 ${stepIndex + 1} 步 · ${caseId || "未命名用例"}`,
        [label(step["adapter_kind"]), required ? "必测" : "选用", step["invalidated"] === true ? "依据已过期" : null]
          .filter(value => typeof value === "string" && value)
          .map(value => String(value))
          .join(" · ") || "步骤事实来自已提交运行视图",
        chip(label(state), toneForState(state)),
      ),
      facts([
        ["步骤状态", chip(label(state), toneForState(state))],
        ["本次尝试", attempt ? label(step["attempt_state"]) : "尚无执行记录"],
        ["核验结论", conclusion ? label(conclusion) : "尚未核验"],
        ["失效", invalidated ? "已过期，需要处理上游后再核对" : "以已保存计划为准"],
        ["操作编号", execution.actionId ? shortId(execution.actionId, 18) : "尚未解析"],
      ]),
      actions,
      state === "blocked" ? banner("该步骤被阻塞。先处理缺口或上游依赖，再重新解析操作。", "warning") : null,
      invalidated ? banner("该步骤的依据已过期，不能直接当作本轮有效结果。", "warning") : null,
    ),
    recoveryCard(ctx, run, step, attempt, execution),
    gaps.length ? card("", cardHead("当前缺口", "缺口代码来自核心；面板只做中文说明。"), gapList) : null,
    card(
      "",
      cardHead("证据", "证据编号来自当前尝试；正文按精确修订读取。"),
      evidenceIds.length ? evidenceList : p("当前尝试还没有可读取的证据。", "muted subtle"),
    ),
    external,
    ctx.state.workbench.note ? banner(ctx.state.workbench.note, "info") : null,
  );
}

function recoveryCard(
  ctx: AppContext,
  run: Record<string, unknown>,
  step: Record<string, unknown>,
  attempt: string | null,
  execution: ExecutionState,
): HTMLElement {
  const attemptCompleted = step["attempt_state"] === "completed" || step["state"] === "completed";
  const canAttempt = attempt !== null && ctx.state.busy === null;
  return card(
    "",
    cardHead("执行不顺利？", "重试/退回会产生新尝试；旧尝试与证据保留。补证只绑定当前已完成的尝试。"),
    div(
      "hero-actions",
      button("重试这一步", () => confirmRecovery(ctx, run, step, "retry"), {
        icon: "refresh",
        disabled: !canAttempt,
        title: attempt ? "重新解析并执行一个新尝试" : "先完成一次执行",
      }),
      button("退回上游", () => confirmRecovery(ctx, run, step, "return"), {
        icon: "arrow-left",
        disabled: !canAttempt,
        title: attempt ? "创建新尝试并计算下游需重跑" : "先完成一次执行",
      }),
      button("补录证据", () => openAttachEvidence(ctx, run, step, attempt), {
        icon: "file",
        disabled: !attempt || !attemptCompleted || ctx.state.busy !== null,
        title: !attempt
          ? "先完成一次执行"
          : attemptCompleted ? "绑定当前尝试补录人工证据" : "当前尝试尚未完成，暂不能补证",
      }),
    ),
    p("重试和退回都会重新解析动作；旧执行与证据保留，依赖下游以核心回执为准。", "muted subtle"),
    execution.actionId ? p(`当前已解析动作：${shortId(execution.actionId, 18)}`, "muted tiny") : null,
  );
}

function confirmRecovery(
  ctx: AppContext,
  run: Record<string, unknown>,
  step: Record<string, unknown>,
  mode: "retry" | "return",
): void {
  const stepId = String(step["step_id"] ?? "");
  const attempt = typeof step["current_attempt_id"] === "string" ? step["current_attempt_id"] : null;
  if (!attempt) {
    ctx.setNotice("先完成一次执行，再重试或退回。", "warning");
    return;
  }
  const content = div(
    "stack",
    p(
      mode === "retry"
        ? "将为这一步重新解析并执行一个新尝试。旧尝试、旧证据保留；依赖此步的下游步骤会由核心按实际依赖标记。"
        : "将为这一步重新解析并执行一个新尝试，并计算依赖它的下游步骤。旧尝试、旧证据保留；实际需重跑清单以核心回执为准。",
      "muted",
    ),
    facts([
      ["步骤", stepId],
      ["当前尝试", shortId(attempt, 14)],
      ["操作", mode === "retry" ? "重试（新尝试）" : "退回上游（新尝试）"],
    ]),
    div(
      "hero-actions",
      button("取消", () => ctx.closeDialog(), { variant: "ghost" }),
      button(mode === "retry" ? "确认重试" : "确认退回", async () => {
        ctx.closeDialog();
        await runRecovery(ctx, run, step, mode);
      }, { variant: "primary", icon: "shield" }),
    ),
  );
  ctx.showDialog(mode === "retry" ? "重试这一步" : "退回上游", content);
}

async function runRecovery(
  ctx: AppContext,
  run: Record<string, unknown>,
  step: Record<string, unknown>,
  mode: "retry" | "return",
): Promise<void> {
  const runId = typeof run["run_id"] === "string" ? run["run_id"] : "";
  const stepId = String(step["step_id"] ?? "");
  const attemptId = typeof step["current_attempt_id"] === "string" ? step["current_attempt_id"] : "";
  if (!runId || !stepId || !attemptId) return;
  const intent = ctx.intent(`${mode}-step`, { run_id: runId, step_id: stepId, attempt_id: attemptId }, stepId, 0);
  ctx.state.busy = "recovery";
  ctx.state.workbench.note = mode === "retry" ? "正在准备重试…" : "正在准备退回…";
  ctx.render();
  const prepared = await ctx.write(
    "prepare_execution",
    { run_id: runId, step_id: stepId },
    { target: stepId, expectedRevision: 0, intentId: intent },
  );
  const preparedResult = asRecord(prepared.response?.["result"]) ?? {};
  const actionId = typeof preparedResult["execution_action_id"] === "string"
    ? preparedResult["execution_action_id"]
    : "";
  if (prepared.status !== "ok" || !actionId) {
    ctx.state.busy = null;
    ctx.state.workbench.note = prepared.message ?? "没有解析出新的执行动作。";
    ctx.render();
    return;
  }
  const outcome = await ctx.write(
    mode === "retry" ? "retry_step" : "return_stage",
    { execution_action_id: actionId, record_revision: 1 },
    { target: stepId, expectedRevision: 0, intentId: intent },
  );
  ctx.state.busy = null;
  if (outcome.status !== "ok") {
    ctx.state.workbench.note = outcome.message ?? (mode === "retry" ? "重试未完成。" : "退回未完成。");
    ctx.render();
    return;
  }
  const result = asRecord(outcome.response?.["result"]) ?? {};
  let message: string;
  if (mode === "retry") {
    const previous = asNumber(result["retried_from_attempt_index"], 0);
    message = previous > 0
      ? `已重试（从第 ${previous + 1} 次尝试开始）；旧尝试与证据保留。`
      : "重试已执行；旧尝试与证据保留。";
  } else {
    const downstream = asStrings(result["downstream_requires_rerun"]);
    const note = typeof result["downstream_note"] === "string" ? result["downstream_note"] : "";
    message = downstream.length
      ? `已退回；下游需重跑：${downstream.join("、")}`
      : (note || "已退回；没有登记下游需重跑。");
  }
  // 新尝试已经执行；保留动作引用，让下一步直接进入核验。
  ctx.state.workbench.executions[stepId] = { intent, actionId, authorized: true, executed: true };
  ctx.state.workbench.note = message;
  ctx.setNotice(message, "success");
  await ctx.refreshCurrent();
}

function openAttachEvidence(
  ctx: AppContext,
  run: Record<string, unknown>,
  step: Record<string, unknown>,
  attempt: string | null,
): void {
  if (!attempt) {
    ctx.setNotice("先完成一次执行，再补录证据。", "warning");
    return;
  }
  const runId = typeof run["run_id"] === "string" ? run["run_id"] : "";
  const stepId = String(step["step_id"] ?? "");
  const operation = select([
    { value: "form", label: "表单操作" },
    { value: "button", label: "按钮操作" },
    { value: "pagination", label: "分页" },
    { value: "navigation", label: "页面导航" },
    { value: "scroll", label: "滚动" },
    { value: "mouse", label: "鼠标操作" },
    { value: "error_prompt", label: "错误提示" },
    { value: "data_display", label: "数据展示" },
  ], "form", () => {});
  const target = textInput("", () => {}, { placeholder: "页面、元素或接口" });
  const observed = textArea("", () => {}, { rows: 3, placeholder: "实际观察到的结果" });
  const business = textInput("", () => {}, { placeholder: "业务对象编号" });
  const summary = textInput("", () => {}, { placeholder: "输入摘要（可留空）" });
  const note = p("", "field-help");
  const submit = button("提交人工证据", async () => {
    note.textContent = "";
    if (!target.value.trim() || !observed.value.trim() || !business.value.trim()) {
      note.textContent = "请填写操作目标、实际结果和业务对象编号。";
      return;
    }
    const parameters = {
      run_id: runId,
      step_id: stepId,
      attempt_id: attempt,
      operation_type: operation.value,
      target_ref: target.value.trim(),
      observed_result: observed.value.trim(),
      business_object_id: business.value.trim(),
      ...(summary.value.trim() ? { input_summary: summary.value.trim() } : {}),
    };
    const outcome = await ctx.write("attach_evidence", parameters, {
      target: stepId,
      expectedRevision: 0,
      intentId: ctx.intent("attach_evidence", parameters, stepId, 0),
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "人工证据未保存。";
      return;
    }
    const result = asRecord(outcome.response?.["result"]) ?? {};
    const evidenceId = typeof result["evidence_id"] === "string" ? result["evidence_id"] : "";
    ctx.closeDialog();
    ctx.state.workbench.note = evidenceId
      ? `人工证据已保存：${shortId(evidenceId, 14)}`
      : "人工证据已保存。";
    ctx.setNotice(ctx.state.workbench.note, "success");
    await ctx.refreshCurrent();
  }, { variant: "primary", icon: "shield" });
  ctx.showDialog(
    "补录人工证据",
    div(
      "stack",
      p(`绑定当前尝试：${shortId(runId, 10)} / ${shortId(stepId, 10)} / ${shortId(attempt, 10)}`, "muted"),
      field("操作类型", operation),
      field("操作目标", target),
      field("实际结果", observed),
      field("业务对象编号", business),
      field("输入摘要（可留空）", summary),
      note,
      div(
        "hero-actions",
        button("取消", () => ctx.closeDialog(), { variant: "ghost" }),
        submit,
      ),
    ),
  );
}

async function prepareExecution(
  ctx: AppContext,
  run: Record<string, unknown>,
  stepId: string,
  execution: ExecutionState,
  caseId: string,
): Promise<void> {
  const runId = typeof run["run_id"] === "string" ? run["run_id"] : "";
  const outcome = await ctx.write(
    "prepare_execution",
    { run_id: runId, step_id: stepId },
    { target: stepId, expectedRevision: 0, intentId: execution.intent },
  );
  if (outcome.status === "ok") {
    const result = asRecord(outcome.response?.["result"]) ?? {};
    if (typeof result["execution_action_id"] === "string") {
      execution.actionId = result["execution_action_id"];
      execution.authorized = false;
      execution.executed = false;
      ctx.state.workbench.note = `已解析出操作。核对后由核心原生窗口确认授权（用例 ${caseId}）。`;
      ctx.setNotice("操作已解析，请核对后确认授权。", "info");
    } else {
      ctx.state.workbench.note = "核心没有返回可核对的操作编号，请刷新后重试。";
    }
  } else {
    ctx.state.workbench.note = outcome.message ?? "操作解析未完成。";
  }
  ctx.render();
}

async function authorizeStep(ctx: AppContext, execution: ExecutionState, stepId: string): Promise<void> {
  if (!execution.actionId) return;
  const outcome = await ctx.write(
    "authorize_step",
    { execution_action_id: execution.actionId, record_revision: 1 },
    { target: stepId, expectedRevision: 0, intentId: execution.intent, controlled: true },
  );
  if (outcome.status === "ok") {
    const result = asRecord(outcome.response?.["result"]) ?? {};
    if (typeof result["authorization_id"] === "string") {
      execution.authorized = true;
      ctx.state.workbench.note = "授权请求已返回。执行时核心仍会核对已保存授权。";
      ctx.setNotice("授权请求已返回，下一步执行。", "success");
    } else {
      ctx.state.workbench.note = "核心没有返回授权编号，请核对确认窗口结果。";
    }
  } else {
    ctx.state.workbench.note = outcome.message ?? "授权未完成。";
  }
  ctx.render();
}

async function executeStep(ctx: AppContext, execution: ExecutionState, stepId: string): Promise<void> {
  if (!execution.actionId) return;
  const outcome = await ctx.write(
    "execute_step",
    { execution_action_id: execution.actionId, record_revision: 1 },
    { target: stepId, expectedRevision: 0, intentId: execution.intent },
  );
  if (outcome.status === "ok") {
    execution.executed = true;
    ctx.state.workbench.note = "执行请求已返回。请刷新步骤，按最新结果进行独立核验。";
    ctx.setNotice("执行请求已返回，下一步独立核验。", "info");
    await ctx.refreshCurrent();
  } else {
    ctx.state.workbench.note = outcome.message ?? "执行未完成。";
    ctx.render();
  }
}

async function verifyStep(
  ctx: AppContext,
  run: Record<string, unknown>,
  stepId: string,
  attemptId: string,
): Promise<void> {
  const runId = typeof run["run_id"] === "string" ? run["run_id"] : "";
  const base = typeof run["snapshot_commit_id"] === "string" ? run["snapshot_commit_id"] : "";
  const parameters = {
    run_id: runId,
    step_id: stepId,
    attempt_id: attemptId,
    base_snapshot_commit_id: base,
  };
  const outcome = await ctx.write("verify_pending", parameters, {
    target: stepId,
    expectedRevision: 0,
    intentId: ctx.intent("verify", parameters, stepId, 0),
  });
  if (outcome.status === "ok") {
    const result = asRecord(outcome.response?.["result"]) ?? {};
    const verification = asRecord(result["verification"]) ?? {};
    const observation = verification["observation"];
    if (typeof observation === "string") ctx.state.workbench.conclusions[stepId] = observation;
    ctx.state.workbench.note = "核验请求已返回。请刷新后查看实际核验结论与未完成原因。";
    ctx.setNotice("核验请求已返回。", "success");
    await ctx.refreshCurrent();
  } else {
    ctx.state.workbench.note = outcome.message ?? "核验未完成。";
    ctx.render();
  }
}

async function openEvidence(ctx: AppContext, evidenceId: string, revision: number): Promise<void> {
  const view = await ctx.read("evidence", { record_id: evidenceId, revision });
  if (!view) return;
  const record = asRecord(view["record"]) ?? {};
  const content = div("stack");
  content.appendChild(
    facts([
      ["证据编号", evidenceId],
      ["修订", String(revision)],
      ["类型", String(record["evidence_kind"] ?? "—")],
      ["等级", label(record["evidence_level"])],
      ["采集来源", String(record["capture_source"] ?? "—")],
      ["完整性", String(record["integrity"] ?? "—")],
    ]),
  );
  if (typeof view["body"] === "string") {
    const pre = document.createElement("pre");
    pre.className = "evidence";
    pre.textContent = view["body"];
    content.appendChild(pre);
  } else {
    content.appendChild(banner(omittedReason(view["body_omitted_reason"]), "warning"));
  }
  ctx.showDialog(`证据 ${shortId(evidenceId, 12)}`, content);
}

function externalImportForm(
  ctx: AppContext,
  run: Record<string, unknown>,
  step: Record<string, unknown>,
): HTMLElement {
  let output = "";
  let assertions = "";
  let identity = "";
  let note = "";
  const noteNode = p("", "field-help");
  const submit = button("提交外部结果", async () => {
    const parse = (raw: string): unknown => {
      try {
        return JSON.parse(raw.trim() || "{}");
      } catch {
        return undefined;
      }
    };
    const actualOutput = parse(output);
    const assertionValues = parse(assertions);
    const sourceIdentity = parse(identity);
    if (!actualOutput || typeof actualOutput !== "object" || Array.isArray(actualOutput)) {
      note = "实际输出必须是 JSON 对象。";
      noteNode.textContent = note;
      return;
    }
    if (!assertionValues || typeof assertionValues !== "object" || Array.isArray(assertionValues)) {
      note = "断言值必须是 JSON 对象。";
      noteNode.textContent = note;
      return;
    }
    const documentBody = {
      schema_version: "aitest.external-result/1.0",
      source_instance_id: null,
      source_record_id: null,
      source_identity: sourceIdentity && typeof sourceIdentity === "object" && !Array.isArray(sourceIdentity)
        ? sourceIdentity
        : null,
      run_id: run["run_id"] ?? null,
      step_id: step["step_id"] ?? null,
      attempt_id: step["current_attempt_id"] ?? null,
      layer: "L2",
      actual_output: actualOutput,
      assertion_values: assertionValues,
      mock_declarations: [],
      attachments: [],
    };
    const outcome = await ctx.write(
      "import_external_result",
      { document: documentBody },
      {
        target: "external-result",
        expectedRevision: 0,
        intentId: ctx.intent("import_external_result", { step_id: step["step_id"], document: documentBody }, "external-result", 0),
      },
    );
    if (outcome.status === "ok") {
      ctx.state.workbench.note = "外部结果已提交。请核实执行状态后重新核验。";
      ctx.setNotice("外部结果已提交。", "success");
      await ctx.refreshCurrent();
    } else {
      ctx.state.workbench.note = outcome.message ?? "外部结果未提交。";
      ctx.render();
    }
  }, { icon: "file" });
  return div(
    "stack-sm",
    textArea("", value => { output = value; }, { rows: 3, placeholder: '实际输出（JSON 对象），例如 {"status":"resolved"}', ariaLabel: "实际输出 JSON" }),
    textArea("", value => { assertions = value; }, { rows: 3, placeholder: '自报断言值（JSON 对象），例如 {"status":"resolved"}', ariaLabel: "断言值 JSON" }),
    textArea("", value => { identity = value; }, { rows: 2, placeholder: "来源身份（JSON 对象，可留空）", ariaLabel: "来源身份 JSON" }),
    noteNode,
    submit,
  );
}
