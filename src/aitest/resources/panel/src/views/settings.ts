/** Settings: model profiles, core connection, local workspace facts. */

import type { AppContext } from "../context";
import { asStrings } from "../labels";
import {
  append,
  banner,
  button,
  card,
  cardHead,
  chip,
  codeBlock,
  div,
  facts,
  loading,
  pageHead,
  p,
  select,
  textInput,
} from "../ui";

export function renderSettings(ctx: AppContext): HTMLElement {
  const state = ctx.state;
  const model = state.model;
  const page = div("page");
  page.appendChild(
    pageHead(
      "设置",
      "模型、连接与本地数据",
      "模型配置只保存 HTTPS 地址与安全凭据引用；密钥正文不进入面板。",
      button("更多诊断", () => ctx.navigate("diagnostics"), { icon: "sliders" }),
    ),
  );

  const modelCard = card(
    "",
    cardHead("模型 API", "用于生成计划提案；关闭模型不影响人工计划、执行和报告。"),
  );
  if (!state.hostAvailable) {
    modelCard.appendChild(banner("未连接编辑器宿主，不能修改模型配置。", "warning"));
  } else if (!model.loaded) {
    modelCard.appendChild(
      button("读取模型配置", () => void ctx.modelLoad(), { icon: "refresh", disabled: model.busy }),
    );
    if (model.busy) modelCard.appendChild(loading("正在读取模型配置…"));
  } else {
    modelCard.appendChild(
      div(
        "form-grid",
        field("API 地址", textInput(model.endpoint, value => { model.endpoint = value; }, { placeholder: "https://api.deepseek.com" })),
        field("模型名称", textInput(model.model, value => { model.model = value; }, { placeholder: "deepseek-flash" })),
        field("请求超时（秒）", textInput(model.timeout, value => { model.timeout = value; }, { placeholder: "10–120" })),
      ),
    );
    if (model.profiles.length > 0) {
      const active = model.activeProfileId;
      modelCard.appendChild(
        field(
          "已保存的模型配置",
          select(
            model.profiles.map(profile => ({ value: profile.profile_id, label: profile.name })),
            active,
            value => void ctx.modelSelect(value),
            model.busy,
          ),
          "切换配置后需要受控重启核心才会用于新建测试。",
        ),
      );
    }
    const credential = div(
      "inline",
      chip(model.reference ? "安全凭据已配置" : "尚未配置安全凭据", model.reference ? "success" : "warning"),
      button("管理安全凭据", () => void ctx.modelCredential(), { icon: "shield", disabled: model.busy }),
    );
    append(
      modelCard,
      credential,
      p("密钥正文只保存在 Windows 安全存储；面板只显示引用。", "muted subtle"),
      div(
        "hero-actions",
        button("保存默认配置", () => void ctx.modelSave(), { variant: "primary", icon: "check", disabled: model.busy }),
        button("重新读取", () => void ctx.modelLoad(), { icon: "refresh", disabled: model.busy }),
      ),
      model.note ? banner(model.note, model.note.includes("已保存") ? "success" : "info") : null,
      p("保存配置不会自动检查模型连通性；实际模型请求的结果会在计划准备中显示。", "muted subtle"),
    );
  }

  const doctor = state.doctor;
  const connection = card(
    "",
    cardHead(
      "核心连接",
      doctor?.status === "READY" ? "已连接本地核心。" : "尚未通过核心自检。",
      chip(doctor?.status === "READY" ? "已连接" : "未连接", doctor?.status === "READY" ? "success" : "warning"),
    ),
    facts([
      ["状态", doctor ? String(doctor["status"] ?? "未知") : "尚未连接"],
      ["协议", String(doctor?.["protocol"] ?? "—")],
      ["项目", String(state.project?.project_id ?? "尚未发布")],
      ["工作空间", state.workspaceId || "尚未取得"],
      ["可派发动作", `${asStrings(doctor?.supported_actions).length} 项`],
    ]),
    div(
      "hero-actions",
      button("连接 / 刷新核心", () => void ctx.ensureCore(), { icon: "zap", disabled: state.busy !== null }),
      button("重启核心", () => void ctx.restartCore(), { icon: "refresh", disabled: state.busy !== null }),
      button("打开插件设置", () => ctx.openHostSettings(), { icon: "settings" }),
      button("空间诊断", () => void storageUsage(ctx), { icon: "chart" }),
    ),
    p("重启会先请求核心优雅停机；有活动执行时会被核心拒绝，不会被打断。", "muted subtle"),
  );

  const workspace = card(
    "flat",
    cardHead("本地数据边界", "业务记录、证据和报告保存在独立用户数据工作空间。"),
    div(
      "hero-steps",
      infoLine("面板只读核心 DTO，不直接读写业务文件。"),
      infoLine("退出重开后按原运行继续；关闭编辑器不等于取消成功。"),
      infoLine("备份、迁移和制品导出在诊断页的高级入口执行。"),
    ),
  );

  page.append(modelCard, connection, workspace);
  return page;
}

function field(labelText: string, control: HTMLElement, help?: string): HTMLElement {
  const label = document.createElement("label");
  label.className = "field";
  const title = document.createElement("span");
  title.className = "field-label";
  title.textContent = labelText;
  label.append(title, control);
  if (help) {
    const hint = document.createElement("span");
    hint.className = "field-help";
    hint.textContent = help;
    label.appendChild(hint);
  }
  return label;
}

function infoLine(text: string): HTMLElement {
  return div("hero-step", div("hero-step-num", "·"), div("grow", text));
}

async function storageUsage(ctx: AppContext): Promise<void> {
  const result = await ctx.readOnly("storage_usage", {});
  if (!result) return;
  ctx.showDialog("空间诊断", codeBlock(JSON.stringify(result, null, 2)));
}
