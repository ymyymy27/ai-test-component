/** DOM primitives. Everything here is presentation-only and accepts core facts verbatim. */

import { icon, type IconName } from "./icons";
import type { Tone } from "./types";

export type Child = Node | string | null | undefined | false;

export function append(parent: Node, ...children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
}

export function div(className = "", ...children: Child[]): HTMLDivElement {
  const node = document.createElement("div");
  if (className) node.className = className;
  append(node, ...children);
  return node;
}

export function section(className = "", ...children: Child[]): HTMLElement {
  const node = document.createElement("section");
  if (className) node.className = className;
  append(node, ...children);
  return node;
}

export function span(value: string, className = ""): HTMLSpanElement {
  const node = document.createElement("span");
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

export function p(value: string, className = ""): HTMLParagraphElement {
  const node = document.createElement("p");
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

export function h2(value: string, className = ""): HTMLHeadingElement {
  const node = document.createElement("h2");
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

export function h3(value: string, className = ""): HTMLHeadingElement {
  const node = document.createElement("h3");
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

export function h4(value: string, className = ""): HTMLHeadingElement {
  const node = document.createElement("h4");
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

export function card(className = "", ...children: Child[]): HTMLDivElement {
  return div(`card${className ? ` ${className}` : ""}`, ...children);
}

export function cardHead(title: string, sub?: string, ...tail: Child[]): HTMLElement {
  const copy = div("grow", div("card-title", title), sub ? div("card-sub", sub) : null);
  const head = div("card-head", copy);
  append(head, ...tail);
  return head;
}

export interface ButtonOptions {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  icon?: IconName;
  disabled?: boolean;
  title?: string;
  className?: string;
  testId?: string;
  type?: "button" | "submit";
}

export function button(text: string, onClick: () => void, options: ButtonOptions = {}): HTMLButtonElement {
  const node = document.createElement("button");
  node.type = options.type ?? "button";
  const variant = options.variant ?? "secondary";
  node.className = `btn${variant === "primary" ? " btn-primary" : variant === "ghost" ? " btn-ghost" : variant === "danger" ? " btn-danger" : ""}${options.className ? ` ${options.className}` : ""}`;
  if (options.icon) node.appendChild(icon(options.icon));
  if (text) node.appendChild(document.createTextNode(text));
  node.disabled = options.disabled === true;
  node.onclick = onClick;
  if (options.title) node.title = options.title;
  if (options.testId) node.dataset.testid = options.testId;
  return node;
}

export function iconButton(name: IconName, label: string, onClick: () => void, disabled = false): HTMLButtonElement {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "icon-btn";
  node.setAttribute("aria-label", label);
  node.title = label;
  node.disabled = disabled;
  node.onclick = onClick;
  node.appendChild(icon(name, 17));
  return node;
}

export function chip(text: string, tone: Tone = "neutral"): HTMLSpanElement {
  const node = span(text, "chip");
  node.dataset.tone = tone;
  return node;
}

export function stepper(labels: string[], active: number, done: ReadonlySet<number> = new Set()): HTMLElement {
  const nav = document.createElement("nav");
  nav.className = "stepper";
  nav.setAttribute("aria-label", "流程进度");
  labels.forEach((text, index) => {
    const state = index < active || done.has(index) ? "done" : index === active ? "active" : "";
    const step = div(`step${state ? ` ${state}` : ""}`);
    if (state === "active") step.setAttribute("aria-current", "step");
    const dot = span(state === "done" ? "✓" : String(index + 1), "step-dot");
    step.append(dot, span(text, "step-label"));
    nav.appendChild(step);
  });
  return nav;
}

export function progress(done: number, total: number, slim = false): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = `progress${slim ? " slim" : ""}`;
  const bar = document.createElement("div");
  const ratio = total > 0 ? Math.max(0, Math.min(1, done / total)) : 0;
  bar.style.width = `${Math.round(ratio * 100)}%`;
  wrap.appendChild(bar);
  wrap.setAttribute("role", "progressbar");
  wrap.setAttribute("aria-valuemin", "0");
  wrap.setAttribute("aria-valuemax", String(Math.max(total, 0)));
  wrap.setAttribute("aria-valuenow", String(Math.max(0, Math.min(done, total))));
  return wrap;
}

export function facts(rows: Array<[string, Child]>): HTMLElement {
  const dl = document.createElement("dl");
  dl.className = "facts";
  for (const [key, value] of rows) {
    dl.appendChild(document.createElement("dt")).textContent = key;
    const dd = document.createElement("dd");
    append(dd, value);
    dl.appendChild(dd);
  }
  return dl;
}

export function field(labelText: string, control: HTMLElement, help?: string): HTMLLabelElement {
  const label = document.createElement("label");
  label.className = "field";
  label.appendChild(span(labelText, "field-label"));
  label.appendChild(control);
  if (help) label.appendChild(span(help, "field-help"));
  if ((control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement || control instanceof HTMLSelectElement)
      && !control.getAttribute("aria-label")) {
    control.setAttribute("aria-label", labelText);
  }
  return label;
}

export interface InputOptions {
  placeholder?: string;
  disabled?: boolean;
  type?: string;
  ariaLabel?: string;
  testId?: string;
}

export function textInput(value: string, onInput: (value: string) => void, options: InputOptions = {}): HTMLInputElement {
  const input = document.createElement("input");
  input.className = "input";
  input.type = options.type ?? "text";
  input.value = value;
  if (options.placeholder) input.placeholder = options.placeholder;
  if (options.disabled) input.disabled = true;
  if (options.ariaLabel) input.setAttribute("aria-label", options.ariaLabel);
  if (options.testId) input.dataset.testid = options.testId;
  input.oninput = () => onInput(input.value);
  return input;
}

export function textArea(value: string, onInput: (value: string) => void, options: InputOptions & { rows?: number } = {}): HTMLTextAreaElement {
  const area = document.createElement("textarea");
  area.className = "textarea";
  area.value = value;
  if (options.placeholder) area.placeholder = options.placeholder;
  if (options.disabled) area.disabled = true;
  if (options.rows) area.rows = options.rows;
  if (options.ariaLabel) area.setAttribute("aria-label", options.ariaLabel);
  if (options.testId) area.dataset.testid = options.testId;
  area.oninput = () => onInput(area.value);
  return area;
}

export function select(
  options: ReadonlyArray<{ value: string; label: string }>,
  value: string,
  onChange: (value: string) => void,
  disabled = false,
): HTMLSelectElement {
  const node = document.createElement("select");
  node.className = "select";
  node.disabled = disabled;
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

export function checkbox(labelText: string, checked: boolean, onChange: (checked: boolean) => void, disabled = false): HTMLLabelElement {
  const label = document.createElement("label");
  label.className = "check";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  input.disabled = disabled;
  input.onchange = () => onChange(input.checked);
  label.append(input, document.createTextNode(labelText));
  return label;
}

export function details(summary: string, ...children: Child[]): HTMLDetailsElement {
  const node = document.createElement("details");
  node.className = "group";
  const head = document.createElement("summary");
  head.textContent = summary;
  const body = document.createElement("div");
  append(body, ...children);
  node.append(head, body);
  return node;
}

export function banner(
  message: string,
  tone: Tone = "info",
  actionLabel?: string,
  onAction?: () => void,
): HTMLElement {
  const icons: Record<Tone, IconName> = {
    neutral: "info",
    info: "info",
    success: "check-circle",
    warning: "alert",
    danger: "alert",
  };
  const node = div("banner", icon(icons[tone], 15), div("grow", message));
  node.dataset.tone = tone;
  node.setAttribute("role", tone === "danger" ? "alert" : "status");
  if (actionLabel && onAction) {
    node.appendChild(button(actionLabel, onAction, { variant: "ghost", className: "btn-sm" }));
  }
  return node;
}

export function empty(
  iconName: IconName,
  title: string,
  body: string,
  action?: HTMLButtonElement,
): HTMLElement {
  const node = div(
    "empty",
    div("empty-icon", icon(iconName, 22)),
    div("empty-title", title),
    div("empty-body", body),
  );
  if (action) node.appendChild(action);
  return node;
}

export function loading(message: string): HTMLElement {
  return div("loading-line", div("spinner"), span(message));
}

export function stat(value: string | number, labelText: string): HTMLElement {
  return div("stat", div("stat-value", String(value)), div("stat-label", labelText));
}

export function metricRow(labelText: string, value: number, total: number): HTMLElement {
  const row = div("metric-row");
  const bar = div("metric-bar", progress(value, total, true));
  row.append(
    span(labelText, "metric-label"),
    bar,
    span(`${value} / ${total > 0 ? total : "—"}`, "metric-value"),
  );
  return row;
}

export function codeBlock(value: string): HTMLElement {
  const node = document.createElement("pre");
  node.className = "code-block";
  node.textContent = value;
  return node;
}

export function iconBadge(name: IconName): HTMLElement {
  return div("row-icon", icon(name, 15));
}

export function statusChip(text: string, tone: Tone): HTMLSpanElement {
  return chip(text, tone);
}

export function pageHead(eyebrowText: string, title: string, sub?: string, ...actions: Child[]): HTMLElement {
  const head = div(
    "page-head",
    div("page-head-copy", div("eyebrow", eyebrowText), h2(title, "page-title"), sub ? p(sub, "page-sub") : null),
  );
  if (actions.length) {
    const row = div("inline");
    append(row, ...actions);
    head.appendChild(row);
  }
  return head;
}

export function cardSection(title: string, ...children: Child[]): HTMLElement {
  return section("", div("section-title", title), ...children);
}
