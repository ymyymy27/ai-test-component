import { beforeEach, describe, expect, it } from "vitest";

import { setSkin, skin, toggleSkin } from "@/state/skin";

describe("皮肤切换", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.dataset.skin = "";
  });

  it("切换 Paper 后写到 html 与 localStorage", () => {
    setSkin("paper");
    expect(skin.value).toBe("paper");
    expect(document.documentElement.dataset.skin).toBe("paper");
    expect(localStorage.getItem("aitest.skin")).toBe("paper");
  });

  it("再次切换回到 Console", () => {
    setSkin("paper");
    toggleSkin();
    expect(skin.value).toBe("console");
    expect(document.documentElement.dataset.skin).toBe("console");
    expect(localStorage.getItem("aitest.skin")).toBe("console");
  });
});
