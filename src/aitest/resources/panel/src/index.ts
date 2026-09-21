/** Shared phase-one navigation. Only displays core DTOs; no business state machine. */
type Page = "current" | "project" | "workbench" | "review" | "settings";
const pages: ReadonlyArray<readonly [Page, string, string]> = [
  ["current", "当前检查", "尚未连接本地核心，没有运行记录。"],
  ["project", "项目与计划", "项目、任务与交付、规则和计划将在此编辑。"],
  ["workbench", "工作台", "执行事实、人工步骤、证据与待核实状态将在此展示。"],
  ["review", "报告与问题", "报告修订、本地复核、问题与回归将在此展示。"],
  ["settings", "设置", "环境、模型出站策略、凭据引用及存储维护将在此配置。"],
];

class AITestPanel extends HTMLElement {
  private page: Page = "current";
  private readonly root = this.attachShadow({ mode: "open" });

  connectedCallback(): void { this.render(); }

  private render(): void {
    this.root.replaceChildren();
    const style = document.createElement("style");
    style.textContent = `
      :host { display:block; color:var(--vscode-foreground,#222); font:14px/1.6 system-ui; }
      main { max-width:960px; margin:auto; padding:16px; }
      nav { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0; }
      button { padding:8px 12px; color:inherit; background:transparent;
        border:1px solid currentColor; border-radius:4px; cursor:pointer; }
      button[aria-current=page] { font-weight:700; border-width:2px; }
      button:focus-visible { outline:3px solid var(--vscode-focusBorder,#2678d8); }
      section { border-top:1px solid currentColor; padding-top:16px; }
      .status { opacity:.8; } h1 { font-size:20px; } h2 { font-size:17px; }
    `;
    const main = document.createElement("main");
    const heading = document.createElement("h1");
    heading.textContent = "AI 辅助测试";
    const status = document.createElement("p");
    status.className = "status";
    status.textContent = "一期工程骨架 · 本地核心尚不可用 · 未进行产品验收";
    const nav = document.createElement("nav");
    nav.setAttribute("aria-label", "主导航");
    for (const [id, label] of pages) {
      const button = document.createElement("button");
      button.textContent = label;
      button.dataset.page = id;
      if (this.page === id) button.setAttribute("aria-current", "page");
      button.onclick = () => {
        this.page = id;
        this.render();
        this.root.querySelector<HTMLButtonElement>(`button[data-page="${id}"]`)?.focus();
      };
      nav.append(button);
    }
    const section = document.createElement("section");
    section.setAttribute("aria-live", "polite");
    const selected = pages.find(([id]) => id === this.page)!;
    const title = document.createElement("h2"); title.textContent = selected[1];
    const description = document.createElement("p"); description.textContent = selected[2];
    section.append(title, description);
    main.append(heading, status, nav, section);
    this.root.append(style, main);
  }
}

customElements.define("ai-test-panel", AITestPanel);
