/** Guided first-run flow. Each step states what will be saved and what stays human-owned. */

import type { AppContext } from "../context";
import { asNumber, asRecord, asRecords, asText, label, toneForState } from "../labels";
import { icon } from "../icons";
import { DEFAULT_AI_GOAL, WIZARD_STEPS } from "../wizard";
import type { WizardCheck } from "../types";
import {
  banner,
  button,
  card,
  cardHead,
  checkbox,
  chip,
  details,
  div,
  facts,
  field,
  h2,
  loading,
  pageHead,
  p,
  progress,
  stepper,
  textArea,
  textInput,
  select,
} from "../ui";

export function renderWizard(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  const state = wizard.state;
  const page = div("page");
  const head = pageHead(
    "准备测试",
    wizardTitle(state.stage),
    "按顺序准备项目、环境、计划与运行。发布和授权会在面板内确认。",
    state.stage !== "choose"
      ? button("重新开始", () => wizard.reset(), { variant: "ghost", icon: "refresh", disabled: state.busy })
      : null,
  );
  page.appendChild(head);
  page.appendChild(stepper([...WIZARD_STEPS], Math.min(wizard.stageIndex(), WIZARD_STEPS.length - 1), wizard.completedSteps()));
  if (state.stage === "choose") page.appendChild(chooseStep(ctx));
  else if (state.stage === "configure") page.appendChild(configureStep(ctx));
  else if (state.stage === "restart") page.appendChild(restartStep(ctx));
  else if (state.stage === "project") page.appendChild(projectStep(ctx));
  else if (state.stage === "goals") page.appendChild(goalsStep(ctx));
  else if (state.stage === "model") page.appendChild(modelStep(ctx));
  else if (state.stage === "manual") page.appendChild(manualStep(ctx));
  else if (state.stage === "review") page.appendChild(reviewStep(ctx));
  else if (state.stage === "commit") page.appendChild(commitStep(ctx));
  else if (state.stage === "prepare" || state.stage === "register") page.appendChild(prepareStep(ctx));
  else if (state.stage === "done") page.appendChild(doneStep(ctx));
  if (state.error) page.appendChild(banner(state.error, "danger"));
  else if (state.note) page.appendChild(banner(state.note, state.busy ? "info" : "neutral"));
  return page;
}

function wizardTitle(stage: string): string {
  switch (stage) {
    case "choose":
      return "选择要测试的项目";
    case "configure":
      return "准备项目与环境";
    case "restart":
      return "重启核心以应用配置";
    case "project":
      return "保存项目并分析源码";
    case "goals":
      return "选择一个验证目标";
    case "model":
      return "生成检查计划";
    case "manual":
      return "手工填写检查计划";
    case "review":
      return "核对并发布计划";
    case "prepare":
      return "准备运行";
    case "register":
      return "登记运行";
    case "done":
      return "准备完成";
    default:
      return "准备测试";
  }
}

function chooseStep(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  return card(
    "hero",
    div("eyebrow", "第 1 步"),
    h2("选择一个本地项目与测试入口"),
    p("项目目录和测试数据目录会分开；测试入口必须是项目目录内的 Python 文件。", "muted"),
    div(
      "hero-actions",
      button("选择项目目录", () => void wizard.chooseProject(), {
        variant: "primary",
        icon: "folder",
        disabled: wizard.state.busy,
      }),
    ),
    details(
      "当前向导支持什么？",
      p("仅支持标准库 Python 入口；测试入口需要覆盖加载、正常、边界和异常输入，并输出可独立比较的结果。"),
      p("模型辅助只读取项目名称、测试目标与项目理解摘要；默认不发送源码。", "muted"),
    ),
  );
}

function configureStep(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  const state = wizard.state;
  const busy = state.busy;
  const project = card(
    "",
    cardHead("准备这次测试", "先确认项目与数据边界；验证目标在下一步由 AI 候选给出。"),
    facts([
      ["源码目录", state.session?.source ?? "—"],
      ["测试入口", state.session?.entry ?? "—"],
      ["独立环境", state.workspace || "准备后由核心返回"],
    ]),
    fieldInput("项目名称", state.name, value => { state.name = value; }),
  );
  const goal = fieldArea("你希望验证什么", state.goal, value => { state.goal = value; }, "描述应该发生什么，以及哪些情况需要特别检查。");
  if (state.useModel) {
    // 模型路径不在这里再问一遍目标：下一步会直接给出可选择的候选，
    // 这里只保留可选的额外关注点，避免“先写目标、再选目标”的重复。
    project.append(
      details(
        "补充说明（可选）",
        p("AI 会先分析项目并给出目标候选；这里可以写额外关注点，留空即可。", "muted subtle"),
        goal,
      ),
    );
  } else {
    project.append(goal);
  }
  project.append(
    fieldArea("测试数据如何隔离、测试后如何复位", state.dataPolicy, value => { state.dataPolicy = value; }, "例如：只在内存中计算，不连接业务系统，结束后无需清理。"),
    button(
      state.confirmed ? "已确认：这是本地测试入口" : "确认这是本地测试入口",
      () => {
        state.confirmed = !state.confirmed;
        ctx.render();
      },
      { variant: state.confirmed ? "primary" : "secondary", icon: state.confirmed ? "check" : "shield", disabled: busy },
    ),
    p("确认只表示这是本地测试入口，不会修改真实业务数据；确认后仍可取消。", "muted subtle"),
  );

  const model = card(
    "",
    cardHead("AI 辅助", "AI 先分析项目并给出验证目标候选；生成计划前由你核对。"),
    checkbox("用 AI 生成验证目标与计划（推荐）", state.useModel, value => {
      state.useModel = value;
      state.runTier = value ? "on_demand" : "full";
      if (value && !state.goal.trim()) state.goal = DEFAULT_AI_GOAL;
      ctx.render();
    }, busy),
  );
  if (state.useModel) {
    model.append(
      div(
        "inline",
        chip(state.reference ? "模型已配置" : "模型未配置", state.reference ? "success" : "warning"),
        button("填写 API Key（一次）", () => void wizard.saveCredential(), { icon: "shield", disabled: busy }),
      ),
      p("默认使用 DeepSeek Flash；只需填一次，密钥由宿主安全保存，不出现在面板、记录或日志。", "muted subtle"),
      checkbox("允许模型读取入口源码，用于生成可核验的预期结果", state.sourceSnippets, value => {
        state.sourceSnippets = value;
        ctx.render();
      }, busy),
      details(
        "高级：模型地址与超时",
        div(
          "form-grid",
          fieldInput("API 地址", state.endpoint, value => { state.endpoint = value; }),
          fieldInput("模型名称", state.model, value => { state.model = value; }),
          fieldInput("请求超时（秒）", state.modelTimeout, value => { state.modelTimeout = value; }, "10–120 秒"),
        ),
      ),
    );
  }

  const actions = div(
    "hero-actions",
    button("准备环境并分析项目", () => void wizard.prepareEnvironment(), {
      variant: "primary",
      icon: "arrow-right",
      disabled: busy,
    }),
    button("重新选择项目", () => void wizard.chooseProject(), { icon: "folder", disabled: busy }),
  );
  return div("stack", project, model, envCard(), actions);
}

function envCard(): HTMLElement {
  return details(
    "运行环境",
    p("会在独立 Python 3.11+ 环境中登记解释器、固定源码快照并分析项目理解；不会修改被测项目。", "muted subtle"),
  );
}

function restartStep(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  const state = wizard.state;
  return card(
    "hero",
    div("eyebrow", "应用配置"),
    h2("重启核心以加载环境与模型配置"),
    p("carriers 与模型配置只在核心启动时读取。面板会请求核心优雅停机；如果有活动执行，核心会拒绝重启，已登记的运行不会被中断。", "muted"),
    state.busy ? loading("正在受控重启核心…") : null,
    div(
      "hero-actions",
      button("重试受控重启", () => void wizard.restartAndContinue(), {
        variant: "primary",
        icon: "refresh",
        disabled: state.busy,
      }),
      button("重新开始", () => wizard.reset(), { icon: "refresh", disabled: state.busy }),
    ),
  );
}

function projectStep(ctx: AppContext): HTMLElement {  const wizard = ctx.wizard;
  const state = wizard.state;
  const progressState = state.commandProgress;
  const resuming = state.session?.existing === true;
  const labels = resuming ? ["分析源码"] : ["保存项目", "绑定目录", "登记环境", "分析源码"];
  const done = progressState ? progressState.done : 0;
  const list = div("stack-sm");
  labels.forEach((text, index) => {
    const isDone = progressState !== null && index < done;
    const isCurrent = progressState !== null && index === done && state.busy;
    list.appendChild(
      div(
        "row",
        div("row-icon", isDone ? icon("check", 15) : isCurrent ? div("spinner") : spanNumber(index + 1)),
        div("row-main", div("row-title", text), div("row-sub", isDone ? "已保存" : isCurrent ? "处理中…" : "等待")),
        isDone ? chip("完成", "success") : isCurrent ? chip("进行中", "info") : null,
      ),
    );
  });
  return card(
    "",
    cardHead(
      resuming ? "正在核对已有项目并分析源码" : "正在保存项目并分析源码",
      resuming
        ? "续跑不会重复写入项目、绑定或环境；只重新核对源码快照与项目理解。"
        : "分析结果会作为计划提案的输入；失败会在发布前暴露。",
    ),
    list,
    progressState ? progress(progressState.done, progressState.total) : loading("正在生成受控命令…"),
    state.error
      ? div("hero-actions", button("重试本阶段", () => void wizard.runStage("project"), { variant: "primary", icon: "refresh" }))
      : null,
  );
}

function spanNumber(value: number): HTMLElement {
  return div("row-icon", String(value));
}

function goalsStep(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  const state = wizard.state;
  const snapshot = asRecord(state.receipts["snapshot"]);
  const understanding = snapshot ? asRecord(snapshot["understanding"]) : undefined;
  const understandingBody = understanding ? asRecord(understanding["body"]) : undefined;
  const rawGoals = snapshot?.["goal_candidates"]
    ?? understandingBody?.["goal_candidates"]
    ?? understanding?.["goal_candidates"];
  const goals = Array.isArray(rawGoals) ? asRecords(rawGoals) : [];
  const list = div("stack-sm");
  for (const [index, goal] of goals.entries()) {
    const statement = String(goal["statement"] ?? "");
    const why = String(goal["why"] ?? "");
    const risk = String(goal["risk"] ?? "");
    const selected = state.goal === statement;
    const item = button(
      `${index + 1}. ${statement || "未命名目标"}${selected ? " ✓" : ""}`,
      () => {
        state.goal = statement;
        state.error = "";
        ctx.render();
      },
      { variant: selected ? "primary" : "secondary", className: "row" },
    );
    list.appendChild(
      div(
        "stack-sm",
        item,
        why ? p(`为什么推荐：${why}`, "muted subtle") : null,
        risk ? p(`失败影响：${risk}`, "muted subtle") : null,
      ),
    );
  }
  return div(
    "stack",
    card(
      "hero",
      div("eyebrow", "第 3 步"),
      h2("先选一个业务目标"),
      p("AI 已根据项目理解和风险给出候选。选一个目标后再生成计划；不需要自己写测试步骤。", "muted"),
      goals.length ? list : p("这次分析没有返回目标候选，可以直接生成计划。", "muted subtle"),
    ),
    div(
      "hero-actions",
      button("用选中的目标生成计划", () => void wizard.runStage("model"), {
        variant: "primary",
        icon: "check",
        disabled: state.busy || (goals.length > 0 && !state.goal),
      }),
      button("跳过，使用原来的目标", () => void wizard.runStage("model"), { icon: "arrow-right", disabled: state.busy }),
    ),
    details(
      "没有模型或想手工填写？",
      p("手工四项检查是高级回退，不是默认流程；它要求你自己填写每项独立预期。", "muted subtle"),
      button("改用手工四项检查", () => void wizard.startManual(), { icon: "file", disabled: state.busy }),
    ),
  );
}

function modelStep(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  const state = wizard.state;
  return card(
    "hero",
    div("eyebrow", "第 3 步"),
    h2("生成一份可核对的计划提案"),
    p("模型读取项目名称、测试目标和项目理解摘要，返回结构化用例；不会执行程序。", "muted"),
    facts([
      ["接收方", state.endpoint],
      ["模型", state.model],
      ["材料范围", "项目名称、测试目标、项目理解摘要；默认不含源码"],
    ]),
    div(
      "form-grid",
      field(
        "验证档位",
        select(
          [
            { value: "on_demand", label: "按需自测（推荐，不宣称完整覆盖）" },
            { value: "quick", label: "快速自检（不升级为完整通过）" },
            { value: "full", label: "完整验证（必须覆盖全部业务功能）" },
          ],
          state.runTier,
          value => {
            state.runTier = value as "quick" | "on_demand" | "full";
            ctx.render();
          },
          state.busy,
        ),
      ),
      field(
        "执行方式",
        select(
          [
            { value: "planned", label: "按计划一次准备" },
            { value: "stepwise", label: "逐步确认执行" },
          ],
          state.driver,
          value => {
            state.driver = value as "planned" | "stepwise";
            ctx.render();
          },
          state.busy,
        ),
      ),
    ),
    p("完整验证要求覆盖项目理解中的全部业务功能；只选一个目标时请用按需自测。", "muted subtle"),
    p("如果这里提示模型未配置，请按受控流程重启核心（或重新打开 Trae）后回到本步继续。环境与模型配置只在核心启动时加载。", "muted subtle"),
    div(
      "hero-actions",
      button("生成 AI 计划", () => void wizard.runStage("model"), {
        variant: "primary",
        icon: "check",
        disabled: state.busy,
      }),
      button("改用手工四项检查", () => void wizard.startManual(), { icon: "file", disabled: state.busy }),
    ),
  );
}

function manualStep(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  const state = wizard.state;
  const list = div("stack-sm");
  state.checks.forEach((check, index) => {
    const fold = details(
      `${index + 1}. ${check.objective}`,
      checkFields(check),
    );
    if (index === 0) fold.open = true;
    list.appendChild(fold);
  });
  return div(
    "stack",
    card(
      "",
      cardHead("四项独立检查", "每项使用同一测试入口；输入条件是说明，独立预期不能从运行结果倒填。"),
      p("示例：输入“2 加 3”，结果字段 normal，预期 5，类型数字。入口需输出包含该字段的 JSON。", "muted subtle"),
      list,
    ),
    div(
      "hero-actions",
      button("确认计划并准备测试", () => void wizard.runStage("plan"), {
        variant: "primary",
        icon: "check",
        disabled: state.busy,
      }),
      state.proposal
        ? button("改用 AI 计划", () => wizard.backToReview(), { icon: "arrow-right", disabled: state.busy })
        : null,
    ),
  );
}

function checkFields(check: WizardCheck): HTMLElement {
  return div(
    "form-grid",
    fieldInput("输入条件", check.inputs, value => { check.inputs = value; }),
    fieldInput("结果字段名称", check.key, value => { check.key = value; }),
    fieldInput("独立预期结果", check.value, value => { check.value = value; }),
    fieldSelect("预期类型", [
      { value: "text", label: "文字" },
      { value: "number", label: "数字" },
      { value: "boolean", label: "是 / 否" },
    ], check.type, value => {
      check.type = value === "number" ? "number" : value === "boolean" ? "boolean" : "text";
    }),
  );
}

function reviewStep(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  const state = wizard.state;
  const preview = state.preview ?? {};
  const coverage = asRecord(preview["coverage"]) ?? {};
  const cases = asRecords(preview["cases"]);
  const coverageRows = asRecords(preview["coverage_preview"]);
  const unknown = asNumber(preview["unknown_count"]);
  const gaps = asNumber(preview["capability_gap_count"]);
  const stats = div(
    "stats",
    statValue(cases.length, "计划用例"),
    statValue(`${asNumber(coverage["covered_items"])}/${asNumber(coverage["template_items"])}`, "已覆盖模板项"),
    statValue(asNumber(coverage["required_items"]), "必测项"),
    statValue(unknown, "未知项"),
    statValue(gaps, "能力缺口"),
  );
  const coverageList = div("stack-sm");
  for (const row of coverageRows.slice(0, 8)) {
    const functionId = typeof row["function_id"] === "string" ? row["function_id"] : "功能";
    const overall = row["overall_status"];
    const dimensions = asRecord(row["dimensions"]) ?? {};
    const dimensionText = Object.entries(dimensions).map(([key, value]) => `${key} ${label(value)}`).join(" · ");
    coverageList.appendChild(
      div(
        "row",
        div("row-main", div("row-title", functionId), div("row-sub", dimensionText || "未声明维度")),
        chip(label(overall), toneForState(overall)),
      ),
    );
  }
  if (coverageRows.length > 8) coverageList.appendChild(p(`仅显示前 8 项，共 ${coverageRows.length} 项。`, "muted subtle"));

  const caseList = div("stack-sm");
  for (const item of cases.slice(0, 10)) {
    const objective = typeof item["objective"] === "string" ? item["objective"] : "未命名用例";
    const meta = [item["case_id"], item["layer"], label(item["importance"]), label(item["verification_method"])]
      .filter(value => typeof value === "string" && value)
      .join(" · ");
    caseList.appendChild(
      div(
        "row",
        div("row-main", div("row-title", objective), div("row-sub", meta)),
        chip(label(asRecord(item["assertion_basis"])?.["state"]), toneForState(asRecord(item["assertion_basis"])?.["state"])),
      ),
    );
  }
  if (cases.length > 10) caseList.appendChild(p(`仅显示前 10 条，共 ${cases.length} 条；完整内容在提案 JSON 中。`, "muted subtle"));

  const proposalJson = state.proposal ? JSON.stringify(state.proposal, null, 2) : "{}";
  const advanced = details(
    "高级：查看或编辑提案 JSON",
    p("修改后需要重新校验；客户端只做语法提示，发布前由核心重新校验。", "muted subtle"),
    textArea(proposalJson, value => {
      try {
        const parsed: unknown = JSON.parse(value);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          state.proposal = parsed as Record<string, unknown>;
          state.preview = null;
          state.catalogConfirmed = false;
          state.error = "";
          state.note = "提案已修改，请重新校验。";
        } else {
          state.error = "提案必须是 JSON 对象。";
          state.note = "";
        }
      } catch {
        // Keep the last valid proposal; show the syntax issue without freezing the form.
        state.error = "提案 JSON 语法无效，尚未应用修改。";
        state.note = "";
      }
    }, { rows: 14, ariaLabel: "计划提案 JSON" }),
  );

  const publishCard = card(
    "",
    cardHead("发布前确认", "确认业务功能目录与计划摘要后，一次确认冻结计划与执行许可。"),
    button(
      state.catalogConfirmed ? "已确认：业务功能目录完整" : "确认业务功能目录完整",
      () => {
        state.catalogConfirmed = !state.catalogConfirmed;
        ctx.render();
      },
      { variant: state.catalogConfirmed ? "primary" : "secondary", icon: state.catalogConfirmed ? "check" : "shield", disabled: state.busy },
    ),
    advanced,
    state.useModel && !state.expectedFactsText.trim()
      ? banner("未填写验证预期：这些 AI 用例发布后只能保持未验证。可在“验证预期”中按用例填写机器可读预期。", "warning")
      : null,
    details(
      "验证预期（可选，JSON）",
      p("按用例填写机器可读预期，例如 {\"case-1\": {\"sum\": 5}}；未填写的用例只能保持未验证。", "muted subtle"),
      textArea(state.expectedFactsText, value => {
        state.expectedFactsText = value;
        state.preview = null;
        state.catalogConfirmed = false;
      }, { rows: 6, ariaLabel: "验证预期 JSON" }),
    ),
    div(
      "hero-actions",
      button("重新校验", () => void wizard.review(), { icon: "refresh", disabled: state.busy }),
      button("确认并开始", () => void wizard.publish(), {
        variant: "primary",
        icon: "shield",
        disabled: state.busy || !state.catalogConfirmed || !state.preview,
      }),
    ),
    p("点击“确认并开始”后，核心会生成确认材料，面板展示并提交确认；面板不会代签，也不会自动继续。", "muted subtle"),
    state.issues.length ? banner(state.issues.join("；"), "danger") : null,
  );

  return div(
    "stack",
    card(
      "hero",
      div("eyebrow", "第 4 步"),
      h2("核对计划、覆盖与依据"),
      p("发布后计划不可变；一次确认会同时冻结计划、覆盖契约和执行许可。", "muted"),
      state.useModel
        ? banner("AI 单目标计划按需自测：只验证选中的目标，不宣称完整覆盖全部业务功能。", "info")
        : null,
      stats,
    ),
    publishCard,
    coverageRows.length ? card("", cardHead("功能 × 维度覆盖", "来自已保存的项目理解，未覆盖项会阻止完整验证。"), coverageList) : null,
    card("", cardHead("计划用例", "逐条核对目标、层级和核验方式。"), caseList),
  );
}

function commitStep(ctx: AppContext): HTMLElement {
  const state = ctx.wizard.state;
  const progressState = state.commandProgress;
  return card(
    "",
    cardHead("发布计划", "确认后，计划、覆盖契约与执行许可一起冻结。"),
    progressState ? progress(progressState.done, progressState.total) : loading("正在保存用例与验收范围…"),
    progressState ? p(`正在执行：${progressState.label}（${progressState.done}/${progressState.total}）`, "muted subtle") : null,
    p("面板不会代签确认；窗口关闭或超时只表示结果未核实，按原意图核对。", "muted subtle"),
    state.error
      ? div("hero-actions", button("重试发布", () => void wizardRetryCommit(ctx), { variant: "primary", icon: "refresh" }))
      : null,
  );
}

function wizardRetryCommit(ctx: AppContext): void {
  void ctx.wizard.runStage("commit");
}

function prepareStep(ctx: AppContext): HTMLElement {  const wizard = ctx.wizard;
  const state = wizard.state;
  const progressState = state.commandProgress;
  return card(
    "",
    cardHead("准备并登记运行", "固定源码、环境和确认引用；登记本身不执行任何步骤。"),
    progressState ? progress(progressState.done, progressState.total) : loading("正在生成受控命令…"),
    progressState ? p(`正在执行：${progressState.label}（${progressState.done}/${progressState.total}）`, "muted subtle") : null,
    state.error
      ? div("hero-actions", button("重试本阶段", () => void wizard.runStage(state.stage === "register" ? "register" : "prepare"), { variant: "primary", icon: "refresh" }))
      : null,
    facts([
      ["计划", asText(state.receipts["publish"]?.["plan_id"], "—")],
      ["准备记录", asText(state.receipts["prepare"]?.["prepared_run_id"], "—")],
      ["运行", asText(state.receipts["register"]?.["run_id"], "—")],
    ]),
  );
}

function doneStep(ctx: AppContext): HTMLElement {
  const wizard = ctx.wizard;
  const state = wizard.state;
  const register = state.receipts["register"] ?? {};
  return card(
    "hero",
    div("eyebrow", "准备完成"),
    h2("运行已登记，可以开始执行"),
    p("下一步在工作台逐项查看操作、确认授权、执行并核验。执行请求返回不等于业务通过。", "muted"),
    facts([
      ["计划", asText(state.receipts["publish"]?.["plan_id"], "—")],
      ["运行", asText(register["run_id"], "—")],
      ["步骤", Array.isArray(register["steps"]) ? `${register["steps"].length} 步` : "读取运行后可见"],
    ]),
    div(
      "hero-actions",
      button("进入测试工作台", () => wizard.complete(), { variant: "primary", icon: "terminal" }),
      button("回到当前检查", () => ctx.navigate("home"), { icon: "arrow-right" }),
    ),
  );
}

function fieldInput(labelText: string, value: string, onChange: (value: string) => void, help?: string): HTMLElement {
  return div("", fieldNode(labelText, textInput(value, onChange), help));
}

function fieldArea(labelText: string, value: string, onChange: (value: string) => void, help?: string): HTMLElement {
  return div("span-2", fieldNode(labelText, textArea(value, onChange, { rows: 4 }), help));
}

function fieldSelect(
  labelText: string,
  options: ReadonlyArray<{ value: string; label: string }>,
  value: string,
  onChange: (value: string) => void,
): HTMLElement {
  return div("", fieldNode(labelText, select(options, value, onChange)));
}

function fieldNode(labelText: string, control: HTMLElement, help?: string): HTMLElement {
  const wrapper = document.createElement("label");
  wrapper.className = "field";
  const title = document.createElement("span");
  title.className = "field-label";
  title.textContent = labelText;
  wrapper.append(title, control);
  if (help) {
    const hint = document.createElement("span");
    hint.className = "field-help";
    hint.textContent = help;
    wrapper.appendChild(hint);
  }
  return wrapper;
}

function statValue(value: string | number, labelText: string): HTMLElement {
  return div("stat", div("stat-value", String(value)), div("stat-label", labelText));
}
