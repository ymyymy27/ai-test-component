/** Reports and issues: read snapshots, save a report revision, export, and copy a repair brief. */

import type { AppContext } from "../context";
import { icon } from "../icons";
import { renderDelivery } from "./delivery";
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
  details,
  div,
  empty,
  facts,
  field,
  h2,
  loading,
  pageHead,
  p,
  select,
  stat,
  textArea,
  textInput,
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
        else if (state.reportsTab === "issues") void ctx.loadIssues();
        else void ctx.loadDeliveries();
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
  const deliveryButton = button("提测", () => {
    state.reportsTab = "delivery";
    ctx.render();
    void ctx.loadDeliveries();
  }, { className: state.reportsTab === "delivery" ? "active" : "" });
  tabs.append(reportsButton, issuesButton, deliveryButton);
  page.appendChild(tabs);

  if (state.reportsTab === "reports") {
    if (state.selectedReport && state.reportDetail) page.appendChild(reportDetail(ctx, state.reportDetail));
    else page.appendChild(reportsBody(ctx));
  } else if (state.reportsTab === "issues") {
    if (state.selectedIssue && state.issueDetail) page.appendChild(issueDetail(ctx, state.issueDetail));
    else page.appendChild(issuesBody(ctx));
  } else {
    page.appendChild(renderDelivery(ctx));
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
    localReviewCard(ctx, record),
    gaps.length ? card("", cardHead("缺口", "报告与面板共用同一次领域判定。"), gapList) : null,
    card("", cardHead("证据索引", "按精确证据修订读取正文；路径与敏感字段不会进入面板。"), evidenceRefs.length ? evidenceList : p("报告没有证据引用。", "muted subtle")),
  );
}

function localReviewCard(ctx: AppContext, record: Record<string, unknown>): HTMLElement {
  const reportId = typeof record["report_id"] === "string" ? record["report_id"] : "";
  const revision = asNumber(record["content_revision"], ctx.state.selectedReport?.revision ?? 0);
  if (!reportId) return div("");
  const saved = ctx.state.reviewIds[reportId] ?? [];
  const details = ctx.state.reviewDetails[reportId] ?? [];
  let conclusion = "";
  let explanation = "";
  let pendingReviewId: string | null = null;
  const conclusionInput = textInput("", value => {
    conclusion = value;
    pendingReviewId = null;
  }, { placeholder: "例如：通过 / 需修改 / 拒绝" });
  const explanationInput = textArea("", value => {
    explanation = value;
    pendingReviewId = null;
  }, { rows: 3, placeholder: "复核说明" });
  const note = p("", "field-help");
  const submit = button("保存本地复核", async () => {
    note.textContent = "";
    if (!conclusion.trim() || !explanation.trim()) {
      note.textContent = "请填写复核结论和说明。";
      return;
    }
    const reviewId = pendingReviewId ?? `review-${randomSuffix()}`;
    pendingReviewId = reviewId;
    const parameters = {
      review_id: reviewId,
      report_id: reportId,
      conclusion: conclusion.trim(),
      explanation: explanation.trim(),
      report_revision: revision,
    };
    const outcome = await ctx.write("record_local_review", parameters, {
      expectedRevision: 0,
      intentId: ctx.intent("record_local_review", parameters, undefined, 0),
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "本地复核未保存。";
      return;
    }
    ctx.state.reviewIds[reportId] = [...(ctx.state.reviewIds[reportId] ?? []), reviewId];
    pendingReviewId = null;
    ctx.setNotice(`本地复核已保存：${shortId(reviewId, 16)}`, "success");
    ctx.render();
  }, { variant: "primary", icon: "check" });
  return card(
    "",
    cardHead("本地复核", "复核绑定准确报告修订；不改变报告正文，导出时随报告引用。"),
    saved.length
      ? p(`本次会话已保存复核：${saved.join("、")}`, "muted tiny")
      : p("尚未保存复核。", "muted subtle"),
    details.length
      ? div("stack-sm", ...details.map(item => p(
        `${String(item["reviewer"] ?? "复核人")} · ${String(item["conclusion"] ?? "")}：${String(item["explanation"] ?? "")}`,
        "muted subtle",
      )))
      : null,
    div(
      "hero-actions",
      button("读取历史复核", () => void loadLocalReviews(ctx, reportId), { icon: "refresh" }),
    ),
    field("复核结论", conclusionInput),
    field("复核说明", explanationInput),
    note,
    div("hero-actions", submit),
  );
}

async function loadLocalReviews(ctx: AppContext, reportId: string): Promise<void> {
  const rows = await ctx.query({ aggregate_kind: "local_review", limit: 20 });
  const views = await Promise.all(rows.map(row =>
    ctx.read("local_review", { record_id: row.record_id, revision: row.revision })));
  const details = views
    .map(view => asRecord(view?.["record"]))
    .filter((item): item is Record<string, unknown> => item !== undefined)
    .filter(item => item["report_id"] === reportId);
  ctx.state.reviewDetails[reportId] = details;
  const ids = details
    .map(item => typeof item["review_id"] === "string" ? item["review_id"] : "")
    .filter(Boolean);
  if (ids.length) ctx.state.reviewIds[reportId] = ids;
  ctx.render();
}

async function exportReport(ctx: AppContext, reportId: string, revision: number, kind: string): Promise<void> {
  if (!reportId) return;
  const parameters = {
    report_id: reportId,
    report_revision: revision,
    kind,
    review_ids: ctx.state.reviewIds[reportId] ?? [],
  };
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
        `问题 ${shortId(record["issue_id"], 14)} · 修订 ${record["revision"] ?? ctx.state.selectedIssue?.revision ?? "—"}`,
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
    issueActions(ctx, record),
  );
}

type IssueAction = "confirm" | "start" | "record_fix" | "regression" | "close" | "disposition";

function issueActions(ctx: AppContext, record: Record<string, unknown>): HTMLElement {
  const issueId = typeof record["issue_id"] === "string" ? record["issue_id"] : "";
  const revision = asNumber(record["revision"], ctx.state.selectedIssue?.revision ?? 0);
  if (!issueId) return div("");
  let action: IssueAction = "confirm";

  const severity = select([
    { value: "P0", label: "P0 · 阻断" },
    { value: "P1", label: "P1 · 严重" },
    { value: "P2", label: "P2 · 一般" },
    { value: "P3", label: "P3 · 轻微" },
  ], "P1", () => {});
  const owner = textInput("", () => {}, { placeholder: "责任人" });
  const fixVersion = textInput("", () => {}, { placeholder: "修复版本" });
  const regressionMethod = textInput("", () => {}, { placeholder: "回归方式" });
  const closureCriteria = textArea("", () => {}, { rows: 2, placeholder: "关闭条件" });
  const regressionNote = textInput("", () => {}, { placeholder: "回归说明（可留空）" });
  const caseId = textInput(typeof record["case_id"] === "string" ? record["case_id"] : "", () => {});
  const runId = textInput(typeof record["run_id"] === "string" ? record["run_id"] : "", () => {});
  const attemptId = textInput("", () => {}, { placeholder: "新的回归尝试编号" });
  const assertionRef = textInput("", () => {}, { placeholder: "断言引用" });
  const assertionOutcome = select([
    { value: "passed", label: "通过" },
    { value: "failed", label: "失败" },
    { value: "unknown", label: "未知（不声明结果）" },
  ], "passed", () => {});
  const dispositionKind = select([
    { value: "non_defect", label: "非缺陷" },
    { value: "duplicate", label: "重复问题" },
    { value: "deferred", label: "推迟处理" },
  ], "non_defect", () => {});
  const dispositionReason = textInput("", () => {}, { placeholder: "处置原因" });
  const evidenceRefs = textInput("", () => {}, { placeholder: "证据编号，逗号分隔（可留空）" });
  const canonicalId = textInput("", () => {}, { placeholder: "主问题编号（仅重复时必填）" });

  const groups: Record<IssueAction, HTMLElement> = {
    confirm: div("stack-sm", field("严重度", severity), field("责任人", owner)),
    start: p("登记“开始处理”。本操作不执行测试、不关闭问题。", "muted subtle"),
    record_fix: div(
      "stack-sm",
      field("修复版本", fixVersion),
      field("回归方式", regressionMethod),
      field("关闭条件", closureCriteria),
    ),
    regression: div("stack-sm", p("登记回归安排；新执行仍由工作台产生。", "muted subtle"), field("说明", regressionNote)),
    close: div(
      "stack-sm",
      p("关闭需要原用例上的新的当前回归尝试和已确认依据；旧尝试不能当成本轮回归。", "muted subtle"),
      field("原用例编号", caseId),
      field("回归运行编号", runId),
      field("回归尝试编号", attemptId),
      field("断言引用", assertionRef),
      field("断言结果", assertionOutcome),
    ),
    disposition: div(
      "stack-sm",
      field("处置类型", dispositionKind),
      field("原因", dispositionReason),
      field("证据编号（逗号分隔）", evidenceRefs),
      field("主问题编号（仅重复时必填）", canonicalId),
    ),
  };
  for (const [key, node] of Object.entries(groups)) node.hidden = key !== action;

  const actionSelect = select([
    { value: "confirm", label: "确认问题" },
    { value: "start", label: "开始处理" },
    { value: "record_fix", label: "记录修复" },
    { value: "regression", label: "请求回归" },
    { value: "close", label: "关闭问题" },
    { value: "disposition", label: "标记重复 / 非缺陷 / 推迟" },
  ], action, value => {
    action = value as IssueAction;
    for (const [key, node] of Object.entries(groups)) node.hidden = key !== action;
  });
  const note = p("", "field-help");
  const submit = button("提交问题操作", async () => {
    note.textContent = "";
    let command = "update_issue";
    let parameters: Record<string, unknown>;
    if (action === "confirm") {
      if (!owner.value.trim()) {
        note.textContent = "请填写责任人。";
        return;
      }
      parameters = { issue_id: issueId, confirm: { severity: severity.value, owner: owner.value.trim() } };
    } else if (action === "start") {
      parameters = { issue_id: issueId, start: true };
    } else if (action === "record_fix") {
      if (!fixVersion.value.trim() || !regressionMethod.value.trim() || !closureCriteria.value.trim()) {
        note.textContent = "请填写修复版本、回归方式和关闭条件。";
        return;
      }
      command = "record_fix";
      parameters = {
        issue_id: issueId,
        fix_version: fixVersion.value.trim(),
        regression_method: regressionMethod.value.trim(),
        closure_criteria: closureCriteria.value.trim(),
      };
    } else if (action === "regression") {
      command = "request_regression";
      parameters = { issue_id: issueId, ...(regressionNote.value.trim() ? { note: regressionNote.value.trim() } : {}) };
    } else if (action === "close") {
      if (!caseId.value.trim() || !runId.value.trim() || !attemptId.value.trim() || !assertionRef.value.trim()) {
        note.textContent = "关闭问题需要原用例、回归运行、新的回归尝试和断言引用。";
        return;
      }
      parameters = {
        issue_id: issueId,
        case_id: caseId.value.trim(),
        run_id: runId.value.trim(),
        attempt_id: attemptId.value.trim(),
        assertion_ref: assertionRef.value.trim(),
        ...(assertionOutcome.value === "unknown" ? {} : { assertion_outcome: assertionOutcome.value }),
      };
    } else {
      const refs = evidenceRefs.value.split(",").map(value => value.trim()).filter(Boolean);
      if (!dispositionReason.value.trim()) {
        note.textContent = "请填写处置原因。";
        return;
      }
      if (dispositionKind.value === "duplicate" && !canonicalId.value.trim()) {
        note.textContent = "标记重复时请填写主问题编号。";
        return;
      }
      parameters = {
        issue_id: issueId,
        disposition: {
          kind: dispositionKind.value,
          reason: dispositionReason.value.trim(),
          evidence_refs: refs,
          ...(dispositionKind.value === "duplicate" ? { canonical_issue_id: canonicalId.value.trim() } : {}),
        },
      };
    }
    const outcome = await ctx.write(command, parameters, {
      target: issueId,
      expectedRevision: revision,
      intentId: ctx.intent(command, parameters, issueId, revision),
    });
    if (outcome.status !== "ok") {
      note.textContent = outcome.message ?? "问题操作未完成。";
      ctx.setNotice(note.textContent, "danger");
      return;
    }
    const result = asRecord(outcome.response?.["result"]) ?? {};
    const nextRevision = asNumber(result["revision"], revision + 1);
    await ctx.loadIssues();
    const view = await ctx.read("issue", { record_id: issueId, revision: nextRevision });
    ctx.state.selectedIssue = { record_id: issueId, revision: nextRevision };
    ctx.state.issueDetail = view;
    ctx.setNotice("问题操作已保存。", "success");
    ctx.render();
  }, { variant: "primary", icon: "shield" });

  return card(
    "",
    cardHead("处理问题", "写操作按核心合同选择普通写入或原生确认；关闭问题不会由界面代判。"),
    details(
      "展开处理表单",
      p("确认、开始、修复、回归、关闭和处置分别记录；旧尝试与旧证据保留为历史。", "muted subtle"),
      field("操作", actionSelect),
      ...Object.values(groups),
      submit,
      note,
    ),
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
