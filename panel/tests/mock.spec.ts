import { describe, expect, it } from "vitest";

import type { DoctorFacts } from "@/api/types";
import { installMockHost } from "@/mock/host";

describe("演示宿主", () => {
  it("版本号与当前构建一致（0.5.0）", async () => {
    installMockHost();
    const result = await window.aitestHost!.call("doctor", {});

    expect(result.ok).toBe(true);
    if (!result.ok) {
      return;
    }
    const facts = result.data as DoctorFacts;
    expect(facts.package.version).toBe("0.5.0");
  });
});
