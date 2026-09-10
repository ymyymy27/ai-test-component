class AITestPanel extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `<section class="aitest-panel"><h2>AI 辅助测试</h2><p>组件骨架已加载。</p></section>`;
  }
}

if (!customElements.get("ai-test-panel")) {
  customElements.define("ai-test-panel", AITestPanel);
}

