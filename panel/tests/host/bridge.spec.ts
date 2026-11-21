import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HostError, callHost, type HostProgressEvent } from "@/host/bridge";

interface CallRecord {
  command: string;
  payload: object;
  options?: {
    confirm?: boolean;
    onProgress?: (event: HostProgressEvent) => void;
  };
}

function installHost(
  handler: (record: CallRecord) => Promise<{ ok: true; data: unknown } | { ok: false; error: { code: string; message: string } }> = () =>
    new Promise(() => undefined),
) {
  const calls: CallRecord[] = [];
  const host = {
    call: vi.fn((command: string, payload: object, options?: CallRecord["options"]) => {
      const record = { command, payload, options };
      calls.push(record);
      return handler(record);
    }),
    cancel: vi.fn(),
    getState: () => undefined,
    setState: () => undefined,
  };
  (window as unknown as { aitestHost: unknown }).aitestHost = host;
  return { host, calls };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  delete (window as unknown as { aitestHost?: unknown }).aitestHost;
});

describe("host call timeouts", () => {
  it("keeps long commands running past the panel default", async () => {
    const { host, calls } = installHost();
    const pending = callHost("run.repair", { run_id: "run_1" }, { confirm: true });
    const settled = vi.fn();
    pending.catch(settled);
    await vi.advanceTimersByTimeAsync(400_000);
    expect(calls).toHaveLength(1);
    expect(settled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(230_000);
    await expect(pending).rejects.toMatchObject({ code: "PANEL_TIMEOUT" });
    expect(host.cancel).toHaveBeenCalledWith(
      "run.repair",
      { run_id: "run_1" },
      { confirm: true },
    );
  });

  it("uses the shorter default for ordinary commands", async () => {
    installHost();
    const pending = callHost("doctor", { probe: "short" });
    const rejected = vi.fn();
    pending.catch(rejected);
    await vi.advanceTimersByTimeAsync(299_000);
    expect(rejected).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_000);
    await expect(pending).rejects.toMatchObject({ code: "PANEL_TIMEOUT" });
  });

  it("progress events reset the timeout for long-running downloads", async () => {
    const { calls } = installHost();
    const pending = callHost("doctor", { probe: "progress" }, { onProgress: () => undefined });
    const rejected = vi.fn();
    pending.catch(rejected);
    await vi.advanceTimersByTimeAsync(250_000);
    calls[0]?.options?.onProgress?.({ type: "progress", phase: "runtime", stage: "download" });
    await vi.advanceTimersByTimeAsync(250_000);
    expect(rejected).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(60_000);
    await expect(pending).rejects.toMatchObject({ code: "PANEL_TIMEOUT" });
  });

  it("maps host errors without leaking the raw payload", async () => {
    installHost(async () => ({
      ok: false,
      error: { code: "CONFIRMATION_REQUIRED", message: "缺少确认" },
    }));
    await expect(callHost("run.start", { plan_id: "plan_1" })).rejects.toMatchObject({
      code: "CONFIRMATION_REQUIRED",
      message: "缺少确认",
    });
    await expect(callHost("run.start", { plan_id: "plan_1" })).rejects.toBeInstanceOf(HostError);
  });
});

describe("in-flight deduplication", () => {
  it("reuses the same promise for identical calls", async () => {
    type HostOk = { ok: true; data: unknown };
    let releaseCall: (value: HostOk) => void = () => undefined;
    const { calls } = installHost(
      () =>
        new Promise<HostOk>((resolve) => {
          releaseCall = resolve;
        }),
    );
    const first = callHost("doctor", { probe: "dedupe" });
    const second = callHost("doctor", { probe: "dedupe" });
    expect(calls).toHaveLength(1);
    releaseCall({ ok: true, data: { value: 1 } });
    await expect(first).resolves.toEqual({ value: 1 });
    await expect(second).resolves.toEqual({ value: 1 });
  });

  it("does not merge different confirmation scope", async () => {
    const { calls } = installHost(() => new Promise(() => undefined));
    callHost("run.repair", { run_id: "run_1" }, { confirm: true }).catch(() => undefined);
    callHost("run.repair", { run_id: "run_1" }, { confirm: false }).catch(() => undefined);
    expect(calls).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(700_000);
  });
});
