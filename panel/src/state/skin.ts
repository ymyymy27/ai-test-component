/** 面板皮肤：只改变本机视觉，不影响任何业务数据或判定。 */
import { ref } from "vue";

export type Skin = "console" | "paper";

export const SKINS: { id: Skin; name: string; summary: string }[] = [
  { id: "console", name: "Console", summary: "石墨黑 / 铜色信号" },
  { id: "paper", name: "Paper", summary: "暖纸白 / 墨色文字" },
];

const STORAGE_KEY = "aitest.skin";

function readSkin(): Skin {
  try {
    return localStorage.getItem(STORAGE_KEY) === "paper" ? "paper" : "console";
  } catch {
    return "console";
  }
}

export const skin = ref<Skin>(readSkin());

export function applySkin(value: Skin): void {
  skin.value = value;
  if (typeof document !== "undefined") {
    document.documentElement.dataset.skin = value;
  }
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // 宿主禁用 localStorage 时只影响本次会话，不影响业务。
  }
}

export function setSkin(value: Skin): void {
  applySkin(value);
}

export function toggleSkin(): void {
  applySkin(skin.value === "console" ? "paper" : "console");
}

export function initSkin(): void {
  applySkin(skin.value);
}
