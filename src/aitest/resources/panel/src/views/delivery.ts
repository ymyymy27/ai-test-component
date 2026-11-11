/** Delivery draft and formal submission: self-report is never execution evidence. */

import type { AppContext } from "../context";
import { icon } from "../icons";
import {
  asNumber,
  asRecord,
  asStrings,
  shortId,
  toneForState,
} from "../labels";
import {
  banner,
  button,
  card,
  cardHead,
  chip,
  details,
  div,
  empty,
  facts,
  field,
  loading,
  p,
  select,
  textArea,
  textInput,
} from "../ui";

function lines(value: string): string[] {
  return value
    .split("\n")
    .map(item => item.trim())
    .filter(Boolean);
}

export function renderDelivery(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const page = div("stack");
  if (state.selectedDelivery && state.deliveryDetail) {
    page.appendChild(deliveryDetail(ctx, state.deliveryDetail));
    return page;
  }
  page.appendChild(deliveryList(ctx));
  page.appendChild(taskDraft(ctx));
  page.appendChild(deliveryDraft(ctx));
  return page;
}

function deliveryList(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const actions = div(
    "hero-actions",
    button("读取交付说明", () => void ctx.loadDeliveries(), {
      icon: "refresh",
      disabled: state.busy !== null,
    }),
  );
  if (!state.deliveriesLoaded) return card("", actions, loading("正在读取交付说明…"));
  if (state.deliveries.length === 0) {
    return card(
      "",
      actions,
      empty("report", "还没有交付说明", "交付说明是自述，不是验证结果；保存后仍须由执行事实派生已验证范围。"),
    );
  }
  const list = div("list");
  for (const row of state.deliveries) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "row";
    item.onclick = () => void openDelivery(ctx, row.record_id, row.revision);
    item.append(
      div("row-icon", icon("report", 15)),
      div(
        "row-main",
        div("row-title", `交付说明 ${shortId(row.record_id, 14)}`),
        div("row-sub", `记录修订 ${row.revision}`),
      ),
      chip("读取", "neutral"),
    );
    list.appendChild(item);
  }
  return card(
    "",
    cardHead("交付说明", "自述完成与已验证范围分开显示；自述从不写成业务通过。"),
    actions,
    list,
  );
}

async function openDelivery(ctx: AppContext, deliveryId: string, revision: number): Promise<void> {
  ctx.state.selectedDelivery = { record_id: deliveryId, revision };
  ctx.state.deliveryDetail = await ctx.read("delivery", {
    delivery_id: deliveryId,
    revision,
  });
  ctx.state.deliverySubmit = null;
  ctx.render();
}

function deliveryDetail(ctx: AppContext, view: Record<string, unknown>): HTMLElement {
  const deliveryId = String(view["delivery_id"] ?? "");
  const submitted = view["submitted"] === true;
  const verification = String(view["verification_state"] ?? "");
  const submission = asRecord(view["submission"]);
  const back = button("返回交付列表", () => {
    ctx.state.selectedDelivery = null;
    ctx.state.deliveryDetail = null;
    ctx.state.deliverySubmit = null;
    ctx.render();
  }, { icon: "arrow-left" });
  const submit = deliverySubmitCard(ctx, view);
  return div(
    "stack",
    card(
      "",
      cardHead(
        `交付说明 ${shortId(deliveryId, 18)}`,
        `任务 ${String(view["task_id"] ?? "—")} · 版本 ${String(view["version"] ?? "—")}`,
        chip(submitted ? "已提测" : "未提测", submitted ? "success" : "neutral"),
      ),
      facts([
        ["核验状态", chip(verification ? verification : "未返回", toneForState(verification))],
        ["运行方式", String(view["run_method"] ?? "—")],
        ["自述完成", asStrings(view["self_report_completed"]).join("、") || "无"],
        ["自述未完成", asStrings(view["self_report_incomplete"]).join("、") || "无"],
        ["已验证范围", asStrings(view["verified_in_scope"]).join("、") || "无（由执行事实派生）"],
        ["未验证范围", asStrings(view["unverified_scope"]).join("、") || "无"],
      ]),
      submission
        ? facts([
          ["提测编号", String(submission["submission_id"] ?? "—")],
          ["提测修订", String(submission["revision"] ?? "—")],
          ["内容身份", String(submission["content_identity"] ?? "—")],
        ])
        : p("尚未正式提测。提测只引用已保存的交付说明、任务和源码快照。", "muted subtle"),
      banner("自述完成不等于验证完成；页面不会把自述复制到已验证范围。", "info"),
      div("hero-actions", back),
    ),
    submitted ? null : submit,
  );
}

function deliverySubmitCard(ctx: AppContext, view: Record<string, unknown>): HTMLElement {
  const state = ctx.state;
  const deliveryId = String(view["delivery_id"] ?? "");
  const revision = state.selectedDelivery?.revision ?? asNumber(view["revision"], 0);
  const input = state.deliverySubmit;
  const prepare = button("准备提交材料", async () => {
    const projectId = state.project?.project_id ?? "";
    if (!projectId) {
      ctx.setNotice("尚未取得项目上下文。", "warning");
      return;
    }
    state.busy = "delivery-materials";
    ctx.render();
    const projectRows = await ctx.query({
      aggregate_kind: "project",
      record_id: projectId,
      limit: 1,
    });
    const snapshots = await ctx.query({ aggregate_kind: "source_snapshot", limit: 20 });
    snapshots.sort((left, right) => right.revision - left.revision);
    state.busy = null;
    if (projectRows.length === 0) {
      state.deliverySubmit = {
        projectRevision: 0,
        snapshots,
        snapshotId: snapshots[0]?.record_id ?? "",
        note: "没有读取到项目记录修订，不能提交正式提测。",
      };
      ctx.render();
      return;
    }
    state.deliverySubmit = {
      projectRevision: projectRows[0].revision,
      snapshots,
      snapshotId: snapshots[0]?.record_id ?? "",
      note: snapshots.length === 0 ? "没有可引用的源码快照，不能提交正式提测。" : "",
    };
    ctx.render();
  }, { icon: "refresh", disabled: state.busy !== null });
  const snapshotOptions = input
    ? input.snapshots.map(row => ({
      value: row.record_id,
      label: `${shortId(row.record_id, 16)} · 记录修订 ${row.revision}`,
    }))
    : [];
  const snapshotSelect = select(
    snapshotOptions,
    input?.snapshotId ?? "",
    value => {
      if (input) input.snapshotId = value;
    },
    !input || snapshotOptions.length === 0,
  );
  const note = p(input?.note ?? "", "field-help");
  const submit = button("确认正式提测", async () => {
    if (!input || !input.snapshotId || input.projectRevision < 1) return;
    const snapshot = input.snapshots.find(row => row.record_id === input.snapshotId);
    if (!snapshot) return;
    const submissionId = `submission-${deliveryId}-${revision}`
      .replace(/[^A-Za-z0-9._-]/g, "-")
      .slice(0, 128);
    const parameters = {
      project_revision: input.projectRevision,
      expected_revision: 0,
      submission_id: submissionId,
      delivery_ref: { delivery_id: deliveryId, record_revision: revision },
      source_ref: { snapshot_id: snapshot.record_id, record_revision: snapshot.revision },
    };
    state.busy = "delivery-submit";
    ctx.render();
    const outcome = await ctx.write("submit_delivery", parameters, {
      target: submissionId,
      expectedRevision: 0,
      intentId: ctx.intent("submit_delivery", parameters, submissionId, 0),
      controlled: true,
    });
    state.busy = null;
    if (outcome.status !== "ok") {
      state.deliverySubmit = { ...input, note: outcome.message ?? "正式提测未完成。" };
      ctx.setNotice(state.deliverySubmit.note, "danger");
      ctx.render();
      return;
    }
    state.deliverySubmit = null;
    ctx.setNotice("正式提测请求已返回；请读取交付视图核对回执。", "success");
    const refreshed = await ctx.read("delivery", {
      delivery_id: deliveryId,
      revision,
    });
    if (refreshed) state.deliveryDetail = refreshed;
    ctx.render();
  }, {
    variant: "primary",
    icon: "shield",
    disabled: !input || !input.snapshotId || input.projectRevision < 1 || state.busy !== null,
  });
  return card(
    "",
    cardHead("正式提测", "只引用已保存的交付说明、任务和源码快照；提交不可改写历史。"),
    p("先准备材料，核对项目记录修订和源码快照，再在面板内确认提交。", "muted subtle"),
    div("hero-actions", prepare),
    input
      ? div(
        "stack-sm",
        facts([
          ["项目记录修订", String(input.projectRevision)],
          ["源码快照", snapshotSelect],
        ]),
        note,
        div("hero-actions", submit),
      )
      : null,
  );
}

function taskDraft(ctx: AppContext): HTMLElement {
  const taskId = textInput("", () => {}, { placeholder: "任务编号，例如 task-1" });
  const goal = textInput("", () => {}, { placeholder: "本次目标" });
  const scope = textInput("", () => {}, { placeholder: "业务范围" });
  const acceptance = textArea("", () => {}, {
    rows: 4,
    placeholder: "每行一个验收项：AC-1 | 可观察结果 | true",
  });
  const owner = textInput("", () => {}, { placeholder: "负责人（可留空）" });
  const note = p("", "field-help");
  const save = button("保存任务草稿", async () => {
    note.textContent = "";
    const identity = taskId.value.trim();
    if (!identity || !goal.value.trim() || !scope.value.trim()) {
      note.textContent = "请填写任务编号、目标和范围。";
      return;
    }
    const acceptanceItems = lines(acceptance.value).map(line => {
      const [id, observable, required] = line.split("|").map(value => value.trim());
      return {
        acceptance_item_id: id ?? "",
        observable_result: observable ?? "",
        required: (required ?? "true").toLowerCase() !== "false",
      };
    });
    if (
      acceptanceItems.length === 0
      || acceptanceItems.some(item => !item.acceptance_item_id || !item.observable_result)
    ) {
      note.textContent = "至少一个验收项，格式为：编号 | 可观察结果 | true/false。";
      return;
    }
    const projectId = ctx.state.project?.project_id ?? "";
    const parameters = {
      task: {
        project_id: projectId,
        task_id: identity,
        goal: goal.value.trim(),
        scope: scope.value.trim(),
        acceptance_items: acceptanceItems,
        inputs: [],
        outputs: [],
        preconditions: [],
        ...(owner.value.trim() ? { owner: owner.value.trim() } : {}),
        schema_version: "aitest.task/2.0",
        revision: 1,
      },
    };
    const outcome = await ctx.write("save_task", parameters, {
      target: identity,
      expectedRevision: 0,
      intentId: ctx.intent("save_task", parameters, identity, 0),
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "任务草稿未保存。";
      return;
    }
    const result = asRecord(outcome.response?.["result"]) ?? {};
    note.textContent = `任务已保存：${String(result["record_id"] ?? identity)}，修订 ${String(result["revision"] ?? 1)}。`;
    ctx.setNotice(note.textContent, "success");
  }, { variant: "primary", icon: "check" });
  return card(
    "",
    cardHead("新建任务草稿", "任务定义验收项；保存草稿不等于发布计划或正式提测。"),
    details(
      "填写任务",
      field("任务编号", taskId),
      field("目标", goal),
      field("范围", scope),
      field("验收项", acceptance, "每行格式：编号 | 可观察结果 | true/false"),
      field("负责人（可留空）", owner),
      note,
      div("hero-actions", save),
    ),
  );
}

function deliveryDraft(ctx: AppContext): HTMLElement {
  const deliveryId = textInput("", () => {}, { placeholder: "交付编号，例如 delivery-1" });
  const taskId = textInput("", () => {}, { placeholder: "任务编号" });
  const taskRevision = textInput("1", () => {}, { type: "number" });
  const version = textInput("", () => {}, { placeholder: "版本，例如 1.0.0" });
  const runMethod = textInput("", () => {}, { placeholder: "运行方式" });
  const completed = textArea("", () => {}, { rows: 3, placeholder: "自述已完成（每行一项）" });
  const incomplete = textArea("", () => {}, { rows: 3, placeholder: "自述未完成（每行一项）" });
  const unverified = textArea("", () => {}, { rows: 3, placeholder: "未验证范围（每行一项）" });
  const changed = textArea("", () => {}, { rows: 2, placeholder: "变更模块（每行一项，可留空）" });
  const known = textArea("", () => {}, { rows: 2, placeholder: "已知问题（每行一项，可留空）" });
  const note = p("", "field-help");
  const save = button("保存交付说明", async () => {
    note.textContent = "";
    const identity = deliveryId.value.trim();
    const task = taskId.value.trim();
    const taskRev = Number(taskRevision.value);
    if (!identity || !task || !version.value.trim() || !runMethod.value.trim()) {
      note.textContent = "请填写交付编号、任务编号、版本和运行方式。";
      return;
    }
    if (!Number.isInteger(taskRev) || taskRev < 1) {
      note.textContent = "任务修订必须是正整数。";
      return;
    }
    const projectId = ctx.state.project?.project_id ?? "";
    const parameters = {
      delivery: {
        project_id: projectId,
        delivery_id: identity,
        task_id: task,
        version: version.value.trim(),
        run_method: runMethod.value.trim(),
        self_report: {
          completed: lines(completed.value),
          incomplete: lines(incomplete.value),
        },
        verified_in_scope: [],
        unverified_scope: lines(unverified.value),
        changed_modules: lines(changed.value),
        known_issues: lines(known.value),
        schema_version: "aitest.delivery/2.0",
        revision: 1,
      },
      task_revision: taskRev,
    };
    const outcome = await ctx.write("save_delivery", parameters, {
      target: identity,
      expectedRevision: 0,
      intentId: ctx.intent("save_delivery", parameters, identity, 0),
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "交付说明未保存。";
      return;
    }
    const result = asRecord(outcome.response?.["result"]) ?? {};
    const savedId = typeof result["record_id"] === "string" ? result["record_id"] : identity;
    const savedRevision = asNumber(result["revision"], 1);
    await openDelivery(ctx, savedId, savedRevision);
    ctx.setNotice("交付说明已保存；已验证范围仍由执行事实派生。", "success");
  }, { variant: "primary", icon: "check" });
  return card(
    "",
    cardHead("新建交付说明", "自述与验证事实分列保存；这里不会写入已验证范围。"),
    details(
      "填写交付说明",
      field("交付编号", deliveryId),
      field("任务编号", taskId),
      field("任务修订", taskRevision),
      field("版本", version),
      field("运行方式", runMethod),
      field("自述已完成（每行一项）", completed),
      field("自述未完成（每行一项）", incomplete),
      field("未验证范围（每行一项）", unverified),
      field("变更模块（每行一项，可留空）", changed),
      field("已知问题（每行一项，可留空）", known),
      note,
      div("hero-actions", save),
    ),
  );
}
