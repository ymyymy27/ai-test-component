/** Rule drafts and immutable versions (P1-FR05): edit, import/export Markdown, publish. */

import type { AppContext } from "../context";
import { icon } from "../icons";
import { testTabs } from "../test-tabs";
import {
  asNumber,
  asRecord,
  asRecords,
  asStrings,
  shortId,
} from "../labels";
import {
  banner,
  button,
  card,
  cardHead,
  chip,
  codeBlock,
  details,
  div,
  empty,
  facts,
  field,
  loading,
  pageHead,
  p,
  textArea,
  textInput,
} from "../ui";

function lines(value: string): string[] {
  return value.split("\n").map(item => item.trim()).filter(Boolean);
}

export function renderRules(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const page = div("page");
  page.appendChild(testTabs(ctx, "rules"));
  page.appendChild(
    pageHead(
      "规则",
      "测试规范与版本",
      "草稿可编辑；发布会生成不可变版本。新测试绑定新版本，历史运行保留原版。",
      button("刷新规则", () => void ctx.loadRules(), {
        icon: "refresh",
        disabled: state.busy !== null,
      }),
    ),
  );
  if (!state.project?.project_id) {
    page.appendChild(card("", empty("report", "尚未取得项目上下文", "连接核心并选择项目后才能编辑规则。")));
    return page;
  }
  if (!state.rulesLoaded) {
    page.appendChild(card("", loading("正在读取规则草稿与版本…")));
    return page;
  }
  if (state.ruleDetail && state.ruleDetailKind) {
    page.appendChild(
      state.ruleDetailKind === "rule_draft"
        ? draftEditor(ctx, state.ruleDetail)
        : versionView(ctx, state.ruleDetail),
    );
  }
  page.appendChild(ruleLists(ctx));
  page.appendChild(newDraftCard(ctx));
  page.appendChild(markdownCard(ctx));
  return page;
}

function ruleLists(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const stack = div("stack");
  const draftList = div("list");
  for (const row of state.ruleDrafts) {
    draftList.appendChild(ruleRow(ctx, "rule_draft", row, "草稿"));
  }
  stack.appendChild(
    card(
      "",
      cardHead("规则草稿", "草稿未确认不生效；保存草稿不等于发布。"),
      draftList.childElementCount
        ? draftList
        : p("还没有规则草稿。可以新建或从 Markdown 导入。", "muted subtle"),
    ),
  );
  const versionList = div("list");
  for (const row of state.ruleVersions) {
    versionList.appendChild(ruleRow(ctx, "rule_version", row, "已发布"));
  }
  stack.appendChild(
    card(
      "",
      cardHead("已发布版本", "发布后不可修改；需要变化时新建草稿并再次发布。"),
      versionList.childElementCount
        ? versionList
        : p("还没有已发布规则版本。", "muted subtle"),
    ),
  );
  return stack;
}

function ruleRow(
  ctx: AppContext,
  kind: "rule_draft" | "rule_version",
  row: { record_id: string; revision: number },
  stateLabel: string,
): HTMLElement {
  const item = document.createElement("button");
  item.type = "button";
  item.className = "row";
  item.onclick = () => void openRule(ctx, kind, row.record_id, row.revision);
  item.append(
    div("row-icon", icon("list", 15)),
    div(
      "row-main",
      div("row-title", `规则 ${shortId(row.record_id, 16)}`),
      div("row-sub", `记录修订 ${row.revision}`),
    ),
    chip(stateLabel, kind === "rule_version" ? "success" : "neutral"),
  );
  return item;
}

async function openRule(
  ctx: AppContext,
  kind: "rule_draft" | "rule_version",
  ruleId: string,
  revision: number,
): Promise<void> {
  const view = await ctx.read(kind, { record_id: ruleId, revision });
  if (!view) return;
  ctx.state.ruleDetail = view;
  ctx.state.ruleDetailKind = kind;
  ctx.render();
}

function closeRule(ctx: AppContext): void {
  ctx.state.ruleDetail = null;
  ctx.state.ruleDetailKind = null;
  ctx.render();
}

function draftEditor(ctx: AppContext, view: Record<string, unknown>): HTMLElement {
  const record = asRecord(view["record"]) ?? {};
  const recordRevision = asNumber(view["record_revision"], 1);
  const ruleId = String(record["rule_id"] ?? "");
  const scope = textInput(String(record["scope"] ?? ""), () => {});
  const revision = textInput(String(record["revision"] ?? 1), () => {}, { type: "number" });
  const source = textInput(String(record["source"] ?? "manual"), () => {});
  const text = textArea(String(record["text"] ?? ""), () => {}, { rows: 5 });
  const steps = textArea(asStrings(record["steps"]).join("\n"), () => {}, { rows: 4 });
  const evidence = textArea(asStrings(record["evidence_requirements"]).join("\n"), () => {}, { rows: 3 });
  const unknown = textArea(asStrings(record["unknown_extension_fields"]).join("\n"), () => {}, { rows: 2 });
  const note = p("", "field-help");
  // 草稿的确认/启用状态不落盘：保存恒为未确认、未启用；发布才由核心原生确认。
  const draftPayload = (): Record<string, unknown> => ({
    rule_id: ruleId,
    revision: Number(revision.value),
    scope: scope.value.trim(),
    text: text.value,
    source: source.value.trim(),
    steps: lines(steps.value),
    evidence_requirements: lines(evidence.value),
    enablement: "disabled",
    confirmed: false,
    unknown_extension_fields: lines(unknown.value),
  });
  const validate = (): string => {
    if (!scope.value.trim() || !text.value.trim() || !source.value.trim()) {
      return "请填写适用范围、正文和来源。";
    }
    const number = Number(revision.value);
    if (!Number.isInteger(number) || number < 1) return "规则修订必须是正整数。";
    return "";
  };
  const save = button("保存草稿", async () => {
    note.textContent = validate();
    if (note.textContent) return;
    const parameters = { draft: draftPayload() };
    const outcome = await ctx.write("save_rules", parameters, {
      target: ruleId,
      expectedRevision: recordRevision,
      intentId: ctx.intent("save_rules", parameters, ruleId, recordRevision),
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "规则草稿未保存。";
      return;
    }
    ctx.setNotice("规则草稿已保存；尚未发布。", "success");
    ctx.state.ruleDetail = null;
    ctx.state.ruleDetailKind = null;
    await ctx.loadRules();
  }, { variant: "primary", icon: "check" });
  const publish = button("确认并发布", async () => {
    note.textContent = validate();
    if (note.textContent) return;
    const projectId = ctx.state.project?.project_id ?? "";
    const projectRows = await ctx.query({
      aggregate_kind: "project",
      record_id: projectId,
      limit: 1,
    });
    if (projectRows.length === 0) {
      note.textContent = "没有读取到项目记录修订，不能发布规则。";
      return;
    }
    const parameters = {
      project_revision: projectRows[0].revision,
      expected_revision: 0,
      draft: { ...draftPayload(), confirmed: true },
      context_gaps: [],
    };
    const outcome = await ctx.write("publish_rules", parameters, {
      target: ruleId,
      expectedRevision: 0,
      intentId: ctx.intent("publish_rules", parameters, ruleId, 0),
      controlled: true,
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "规则发布未完成。";
      return;
    }
    ctx.setNotice("规则已发布为不可变版本。", "success");
    ctx.state.ruleDetail = null;
    ctx.state.ruleDetailKind = null;
    await ctx.loadRules();
  }, { variant: "primary", icon: "shield" });
  return card(
    "",
    cardHead(
      `规则草稿 ${shortId(ruleId, 18)}`,
      "草稿可修改；发布后生成不可变版本。",
      chip("草稿", "neutral"),
    ),
    div("stack-sm", field("适用范围", scope), field("正文说明", text),
      field("步骤（每行一项）", steps), field("证据要求（每行一项）", evidence),
      field("来源", source), field("正文修订", revision),
      field("未知扩展字段（每行一项，仅保留）", unknown)),
    note,
    div(
      "hero-actions",
      button("返回规则列表", () => closeRule(ctx), { icon: "arrow-left" }),
      save,
      publish,
    ),
    banner("发布前请核对：未确认的草稿不能发布；发布后不能原地修改。", "info"),
  );
}

function versionView(ctx: AppContext, view: Record<string, unknown>): HTMLElement {
  const record = asRecord(view["record"]) ?? {};
  const ruleId = String(record["rule_id"] ?? "");
  const revision = asNumber(record["revision"], 1);
  return card(
    "",
    cardHead(
      `规则版本 ${shortId(ruleId, 18)}`,
      "已发布版本不可修改；历史运行仍引用原版。",
      chip("已发布", "success"),
    ),
    facts([
      ["规则编号", ruleId],
      ["正文修订", String(revision)],
      ["记录修订", String(view["record_revision"] ?? "—")],
      ["适用范围", String(record["scope"] ?? "—")],
      ["来源", String(record["source"] ?? "—")],
      ["确认编号", String(record["confirmation_id"] ?? "—")],
      ["摘要", String(record["digest"] ?? "—")],
    ]),
    details(
      "正文与步骤",
      p(String(record["text"] ?? ""), "muted"),
      p(`步骤：${asStrings(record["steps"]).join("；") || "无"}`, "muted subtle"),
      p(`证据要求：${asStrings(record["evidence_requirements"]).join("；") || "无"}`, "muted subtle"),
    ),
    div(
      "hero-actions",
      button("返回规则列表", () => closeRule(ctx), { icon: "arrow-left" }),
      button("导出 Markdown", () => void exportMarkdown(ctx, ruleId, revision), { icon: "file" }),
      button("以此新建草稿", () => void copyToDraft(ctx, record), { icon: "copy" }),
    ),
  );
}

async function exportMarkdown(ctx: AppContext, ruleId: string, revision: number): Promise<void> {
  const result = await ctx.readOnly("export_rules_markdown", {
    rule_versions: [{ rule_id: ruleId, revision }],
  });
  if (!result) return;
  const documents = asRecords(result["documents"]);
  const markdown = documents.map(item => String(item["markdown"] ?? "")).filter(Boolean).join("\n\n---\n\n");
  ctx.showDialog("规则 Markdown", codeBlock(markdown || "核心没有返回 Markdown 文档。"));
}

async function copyToDraft(ctx: AppContext, record: Record<string, unknown>): Promise<void> {
  const ruleId = String(record["rule_id"] ?? "");
  const nextRevision = asNumber(record["revision"], 1) + 1;
  const draft = {
    rule_id: ruleId,
    revision: nextRevision,
    scope: String(record["scope"] ?? ""),
    text: String(record["text"] ?? ""),
    source: "copy:" + String(record["source"] ?? "published"),
    steps: asStrings(record["steps"]),
    evidence_requirements: asStrings(record["evidence_requirements"]),
    enablement: "disabled",
    confirmed: false,
    unknown_extension_fields: [],
  };
  const parameters = { draft };
  const outcome = await ctx.write("save_rules", parameters, {
    target: ruleId,
    expectedRevision: 0,
    intentId: ctx.intent("save_rules", parameters, ruleId, 0),
  });
  if (outcome.status !== "ok") {
    ctx.setNotice(outcome.message ?? "复制规则草稿未完成。", "danger");
    return;
  }
  ctx.setNotice(`已创建规则草稿 ${ruleId} 修订 ${nextRevision}。`, "success");
  await ctx.loadRules();
}

function newDraftCard(ctx: AppContext): HTMLElement {
  const ruleId = textInput("", () => {}, { placeholder: "规则编号，例如 rule-order-api" });
  const scope = textInput("", () => {}, { placeholder: "适用范围" });
  const source = textInput("manual", () => {}, { placeholder: "来源" });
  const text = textArea("", () => {}, { rows: 4, placeholder: "给人看的测试规范" });
  const steps = textArea("", () => {}, { rows: 3, placeholder: "每行一个步骤" });
  const evidence = textArea("", () => {}, { rows: 3, placeholder: "每行一个证据要求" });
  const note = p("", "field-help");
  const save = button("保存新草稿", async () => {
    note.textContent = "";
    if (!ruleId.value.trim() || !scope.value.trim() || !text.value.trim() || !source.value.trim()) {
      note.textContent = "请填写规则编号、适用范围、正文和来源。";
      return;
    }
    const draft = {
      rule_id: ruleId.value.trim(),
      revision: 1,
      scope: scope.value.trim(),
      text: text.value,
      source: source.value.trim(),
      steps: lines(steps.value),
      evidence_requirements: lines(evidence.value),
      enablement: "disabled",
      confirmed: false,
      unknown_extension_fields: [],
    };
    const parameters = { draft };
    const outcome = await ctx.write("save_rules", parameters, {
      target: draft.rule_id,
      expectedRevision: 0,
      intentId: ctx.intent("save_rules", parameters, draft.rule_id, 0),
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "规则草稿未保存。";
      return;
    }
    ctx.setNotice("规则草稿已保存；尚未发布。", "success");
    await ctx.loadRules();
    const rows = ctx.state.ruleDrafts;
    const saved = rows.find(row => row.record_id === draft.rule_id);
    if (saved) await openRule(ctx, "rule_draft", saved.record_id, saved.revision);
  }, { variant: "primary", icon: "check" });
  return card(
    "",
    cardHead("新建规则草稿", "未确认的草稿不会生效；保存后可继续编辑。"),
    details(
      "填写规则",
      field("规则编号", ruleId),
      field("适用范围", scope),
      field("正文说明", text),
      field("步骤（每行一项）", steps),
      field("证据要求（每行一项）", evidence),
      field("来源", source),
      note,
      div("hero-actions", save),
    ),
  );
}

function markdownCard(ctx: AppContext): HTMLElement {
  const markdown = textArea("", () => {}, {
    rows: 6,
    placeholder: "粘贴规则 Markdown；导入只会创建未确认草稿。",
  });
  const note = p("", "field-help");
  const importButton = button("导入 Markdown 草稿", async () => {
    note.textContent = "";
    if (!markdown.value.trim()) {
      note.textContent = "请粘贴规则 Markdown。";
      return;
    }
    const parameters = { markdown: markdown.value };
    const outcome = await ctx.write("import_rules_markdown", parameters, {
      target: "rules-import",
      expectedRevision: 0,
      intentId: ctx.intent("import_rules_markdown", parameters, "rules-import", 0),
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "规则 Markdown 导入未完成。";
      return;
    }
    const result = asRecord(outcome.response?.["result"]) ?? {};
    const imported = asRecords(result["imported"]);
    note.textContent = `已导入 ${imported.length} 条草稿；尚未确认发布。`;
    ctx.setNotice(note.textContent, "success");
    await ctx.loadRules();
  }, { variant: "primary", icon: "file" });
  return card(
    "",
    cardHead("Markdown 导入", "导入只创建草稿；文档正文不会执行命令或自动发布。"),
    field("规则 Markdown", markdown),
    note,
    div("hero-actions", importButton),
  );
}
