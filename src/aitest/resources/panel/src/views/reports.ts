/** Reports and issues: read snapshots, save a report revision, export, and copy a repair brief. */

import type { AppContext } from "../context";
import { icon } from "../icons";
import {
  asNumber,
  asRecord,
  asRecords,
  asStrings,
  formatTime,
  gapText,
  label,
  omittedReason,
  shortId,
  toneForState,
} from "../labels";
import {
  banner,
  button,
  card,
  cardHead,
  chip,
  codeBlock,
  div,
  empty,
  facts,
  h2,
  loading,
  pageHead,
  p,
  stat,
} from "../ui";

export function renderReports(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const page = div("page");
  page.appendChild(
    pageHead(
      "问题与报告",
      "报告快照与问题记录",
      "报告按运行与内容修订保存；问题关闭需要新的实际证据，界面不替你判定。",
      button("刷新", () => {
        if (state.reportsTab === "reports") void ctx.loadReports();
        else void ctx.loadIssues();
      }, { icon: "refresh", disabled: state.busy !== null }),
    ),
  );
  const tabs = div("segmented");
  const reportsButton = button("报告", () => {
    state.reportsTab = "reports";
    ctx.render();
    void ctx.loadReports();
  }, { className: state.reportsTab === "reports" ? "active" : "" });
  const issuesButton = button("问题", () => {
    state.reportsTab = "issues";
    ctx.render();
    void ctx.loadIssues();
  }, { className: state.reportsTab === "issues" ? "active" : "" });
  tabs.append(reportsButton, issuesButton);
  page.appendChild(tabs);

  if (state.reportsTab === "reports") {
    if (state.selectedReport && state.reportDetail) page.appendChild(reportDetail(ctx, state.reportDetail));
    else page.appendChild(reportsBody(ctx));
  } else {
    if (state.selectedIssue && state.issueDetail) page.appendChild(issueDetail(ctx, state.issueDetail));
    else page.appendChild(issuesBody(ctx));
  }
  return page;
}

function reportsBody(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const supported = asStrings(state.doctor?.supported_actions);
  const actions = div(
    "hero-actions",
    button("保存本次报告", () => void saveReport(ctx), {
      variant: "primary",
      icon: "report",
      disabled: state.busy !== null || !state.project?.run_id || !supported.includes("create_report"),
    }),
    button("读取报告修订", () => void ctx.loadReports(), { icon: "refresh", disabled: state.busy !== null }),
  );
  if (!state.reportsLoaded) return card("", actions, loading("正在读取报告…"));
  if (state.reports.length === 0) {
    return card(
      "",
      actions,
      empty("report", "还没有报告修订", "运行结束或告一段落后保存一次报告；报告是内容不可变的快照。"),
    );
  }
  const list = div("list");
  for (const row of state.reports) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "row";
    item.onclick = () => void openReport(ctx, row.record_id, row.revision);
    item.append(
      div("row-icon", icon("report", 15)),
      div("row-main", div("row-title", `报告 ${shortId(row.record_id, 12)}`), div("row-sub", `内容修订 ${row.revision}`)),
      chip("读取", "neutral"),
    );
    list.appendChild(item);
  }
  return div("stack", card("", actions), card("", cardHead("报告修订", "同一运行的多份报告会保留修订；旧结论不回退为当前。 "), list));
}

async function openReport(ctx: AppContext, recordId: string, revision: number): Promise<void> {
  ctx.state.selectedReport = { record_id: recordId, revision };
  const view = await ctx.read("report", { record_id: recordId, revision });
  ctx.state.reportDetail = view;
  ctx.render();
}

async function saveReport(ctx: AppContext): Promise<void> {
  const runId = ctx.state.project?.run_id;
  if (!runId) {
    ctx.setNotice("当前没有运行，不能保存报告。", "warning");
    return;
  }
  const existing = ctx.state.reports[0];
  const reportId = existing?.record_id ?? `report-${randomSuffix()}`;
  const expected = existing?.revision ?? 0;
  const parameters = { run_id: runId, report_id: reportId, issue_ids: [] };
  const outcome = await ctx.write("create_report", parameters, {
    expectedRevision: expected,
    intentId: ctx.intent("create_report", parameters, undefined, expected),
  });
  if (outcome.status === "ok") {
    ctx.setNotice("报告已保存。正在读取最新修订…", "success");
    await ctx.loadReports();
    const row = ctx.state.reports.find(item => item.record_id === reportId);
    if (row) await openReport(ctx, row.record_id, row.revision);
    else ctx.render();
  } else {
    ctx.setNotice(outcome.message ?? "报告未保存。", "danger");
    ctx.render();
  }
}

function reportDetail(ctx: AppContext, view: Record<string, unknown>): HTMLElement {
  const record = asRecord(view["record"]) ?? {};
  const decision = asRecord(record["decision"]) ?? {};
  const summary = asRecord(decision["selected_summary"]) ?? {};
  const gaps = asRecords(decision["gaps"]);
  const evidenceRevisions = asRecord(record["evidence_revisions"]) ?? {};
  const evidenceRefs = asStrings(record["evidence_refs"]);
  const issueRevisions = asRecord(record["issue_revisions"]) ?? {};
  const revision = asNumber(record["content_revision"], ctx.state.selectedReport?.revision ?? 0);
  const outcome = record["business_outcome"] ?? decision["business_outcome"];
  const grade = record["evidence_grade"] ?? decision["evidence_grade"];

  const evidenceList = div("stack-sm");
  for (const ref of evidenceRefs.slice(0, 20)) {
    const evidenceRevision = asNumber(evidenceRevisions[ref], 1);
    evidenceList.appendChild(
      button(`证据 ${shortId(ref, 12)} · 修订 ${evidenceRevision}`, async () => {
        const evidence = await ctx.read("evidence", { record_id: ref, revision: evidenceRevision });
        if (!evidence) return;
        const body = typeof evidence["body"] === "string" ? evidence["body"] : null;
        const content = div("stack");
        if (body) content.appendChild(codeBlock(body));
        else content.appendChild(banner(omittedReason(evidence["body_omitted_reason"]), "warning"));
        ctx.showDialog(`证据 ${shortId(ref, 12)}`, content);
      }, { icon: "eye" }),
    );
  }

  const gapList = div("stack-sm");
  for (const gap of gaps) {
    gapList.appendChild(
      div(
        "row",
        div("row-icon", icon("alert", 15)),
        div("row-main", div("row-title", gapText(gap["code"], gap["safe_reason"])), div("row-sub", String(gap["safe_reason"] ?? ""))),
      ),
    );
  }

  return div(
    "stack",
    card(
      "",
      cardHead(
        `报告 ${shortId(record["report_id"], 14)}`,
        `内容修订 ${revision} · ${formatTime(record["created_at"])}`,
        chip(`业务结论：${label(outcome)}`, toneForState(outcome)),
      ),
      div(
        "stats",
        stat(asNumber(summary["denominator"]), "范围"),
        stat(asNumber(summary["executed_count"]), "已执行"),
        stat(asNumber(summary["verified_count"]), "已验证"),
        stat(asNumber(summary["unverified_count"]), "未验证"),
      ),
      facts([
        ["证据等级", grade === null || grade === undefined ? "本轮未评级" : label(grade)],
        ["运行", String(record["run_id"] ?? "—")],
        ["证据引用", `${evidenceRefs.length} 项`],
        ["关联问题", Object.keys(issueRevisions).length ? Object.keys(issueRevisions).join("、") : "无"],
        ["阻塞问题", asStrings(record["blocking_issue_ids"]).join("、") || "无"],
        ["执行快照", String(record["execution_snapshot_ref"] ?? "—")],
      ]),
      div(
        "hero-actions",
        button("导出 Markdown 摘要", () => void exportReport(ctx, String(record["report_id"] ?? ""), revision, "markdown_summary"), { icon: "file" }),
        button("导出证据包", () => void exportReport(ctx, String(record["report_id"] ?? ""), revision, "evidence_bundle"), { icon: "shield" }),
        button("返回报告列表", () => {
          ctx.state.selectedReport = null;
          ctx.state.reportDetail = null;
          ctx.render();
        }, { icon: "arrow-left" }),
      ),
    ),
    gaps.length ? card("", cardHead("缺口", "报告与面板共用同一次领域判定。"), gapList) : null,
    card("", cardHead("证据索引", "按精确证据修订读取正文；路径与敏感字段不会进入面板。"), evidenceRefs.length ? evidenceList : p("报告没有证据引用。", "muted subtle")),
  );
}

async function exportReport(ctx: AppContext, reportId: string, revision: number, kind: string): Promise<void> {
  if (!reportId) return;
  const parameters = { report_id: reportId, report_revision: revision, kind, review_ids: [] };
  const outcome = await ctx.write("export_report", parameters, {
    expectedRevision: 0,
    intentId: ctx.intent("export_report", parameters, undefined, 0),
  });
  if (outcome.status === "ok") {
    const result = asRecord(outcome.response?.["result"]) ?? {};
    ctx.showDialog(
      "导出请求已返回",
      div(
        "stack",
        banner("制品已经由核心保存；请按回执编号核对，不要把“导出成功”当作证据完整。", "info"),
        codeBlock(JSON.stringify(result, null, 2)),
      ),
    );
  } else {
    ctx.setNotice(outcome.message ?? "导出未完成。", "danger");
    ctx.render();
  }
}

function issuesBody(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  if (!state.issuesLoaded) return card("", loading("正在读取问题记录…"));
  if (state.issues.length === 0) {
    return card("", empty("bug", "没有开放问题", "问题详情需要从报告或运行中登记；关闭问题还要新的实际证据。"));
  }
  const list = div("list");
  for (const row of state.issues) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "row";
    item.onclick = () => void openIssue(ctx, row.record_id, row.revision);
    item.append(
      div("row-icon", icon("bug", 15)),
      div("row-main", div("row-title", `问题 ${shortId(row.record_id, 12)}`), div("row-sub", `记录修订 ${row.revision}`)),
      chip("读取", "neutral"),
    );
    list.appendChild(item);
  }
  return card("", cardHead("开放问题", "列表只读摘要；详情按准确修订读取。"), list);
}

async function openIssue(ctx: AppContext, recordId: string, revision: number): Promise<void> {
  ctx.state.selectedIssue = { record_id: recordId, revision };
  const view = await ctx.read("issue", { record_id: recordId, revision });
  ctx.state.issueDetail = view;
  ctx.render();
}

function issueDetail(ctx: AppContext, view: Record<string, unknown>): HTMLElement {
  const record = asRecord(view["record"]) ?? {};
  const factsList = linesFrom(record["facts"]);
  const hypotheses = linesFrom(record["hypotheses"]);
  const evidenceRevisions = asRecord(record["evidence_revisions"]) ?? {};
  const evidenceRefs = asStrings(record["evidence_refs"]);
  return div(
    "stack",
    card(
      "",
      cardHead(
        String(record["title"] ?? "问题详情"),
        `问题 ${shortId(record["issue_id"], 14)} · 修订 ${record["revision"] ?? "—"}`,
        chip(label(record["status"]), toneForState(record["status"])),
      ),
      facts([
        ["严重度", label(record["severity"])],
        ["处置", label(record["disposition"])],
        ["责任人", String(record["owner"] ?? "未指派")],
        ["运行", String(record["run_id"] ?? "—")],
        ["用例", String(record["case_id"] ?? "—")],
        ["步骤", String(record["step_id"] ?? "—")],
        ["尝试", String(record["attempt_id"] ?? "—")],
      ]),
      record["description"] ? p(String(record["description"])) : null,
      div(
        "hero-actions",
        button("复制复测简报", () => void copyRepairBrief(ctx, String(record["issue_id"] ?? ""), record["run_id"], record["attempt_id"]), { variant: "primary", icon: "copy" }),
        button("返回问题列表", () => {
          ctx.state.selectedIssue = null;
          ctx.state.issueDetail = null;
          ctx.render();
        }, { icon: "arrow-left" }),
      ),
    ),
    card(
      "",
      cardHead("预期与实际", "事实与假设分列；不要用假设关闭问题。"),
      facts([
        ["预期", String(record["expected"] ?? "未记录")],
        ["实际", String(record["actual"] ?? "未记录")],
      ]),
      factsList.length ? h2("事实") : null,
      factsList.length ? listLines(factsList) : null,
      hypotheses.length ? h2("假设") : null,
      hypotheses.length ? listLines(hypotheses) : null,
    ),
    evidenceRefs.length
      ? card(
          "",
          cardHead("关联证据", "证据正文按修订读取。"),
          div(
            "stack-sm",
            ...evidenceRefs.slice(0, 20).map(ref =>
              button(`证据 ${shortId(ref, 12)} · 修订 ${asNumber(evidenceRevisions[ref], 1)}`, async () => {
                const evidence = await ctx.read("evidence", { record_id: ref, revision: asNumber(evidenceRevisions[ref], 1) });
                if (!evidence) return;
                const body = typeof evidence["body"] === "string" ? evidence["body"] : null;
                ctx.showDialog(
                  `证据 ${shortId(ref, 12)}`,
                  body ? codeBlock(body) : banner(omittedReason(evidence["body_omitted_reason"]), "warning"),
                );
              }, { icon: "eye" }),
            ),
          ),
        )
      : null,
  );
}

function listLines(values: string[]): HTMLElement {
  const list = document.createElement("ul");
  list.className = "stack-sm";
  for (const value of values) {
    const item = document.createElement("li");
    item.textContent = value;
    list.appendChild(item);
  }
  return list;
}

function linesFrom(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(item => typeof item === "string" ? item : JSON.stringify(item, null, 2));
}

async function copyRepairBrief(ctx: AppContext, issueId: string, runId: unknown, attemptId: unknown): Promise<void> {
  const parameters: Record<string, unknown> = { issue_id: issueId };
  if (typeof runId === "string" && runId) parameters["run_id"] = runId;
  if (typeof attemptId === "string" && attemptId) parameters["attempt_id"] = attemptId;
  const result = await ctx.readOnly("copy_repair_brief", parameters);
  if (!result) return;
  const brief = typeof result["brief"] === "string" ? result["brief"] : "";
  if (!brief) {
    ctx.setNotice("核心没有返回可复制的复测简报。", "warning");
    ctx.render();
    return;
  }
  const clipboard = (globalThis as { navigator?: { clipboard?: { writeText?: (value: string) => Promise<void> } } })
    .navigator?.clipboard;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(brief);
      ctx.setNotice("复测简报已复制（未执行修复、未派发核心动作）。", "success");
    } catch {
      ctx.setNotice("当前环境没有剪贴板权限：未复制，也未执行任何动作。", "warning");
    }
  } else {
    ctx.setNotice("当前环境没有剪贴板权限：未复制，也未执行任何动作。", "warning");
  }
  ctx.showDialog("复测简报", div("stack", p("以下内容由核心生成，未执行修复或关闭问题。", "muted"), codeBlock(brief)));
}

function randomSuffix(): string {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
}
