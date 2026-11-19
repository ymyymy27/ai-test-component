import type { Scheduler } from "./scheduler";
import { realScheduler } from "./scheduler";

export class RunSimulator {
  private readonly scheduler: Scheduler;
  private readonly intervalMs: number;
  private handle: ReturnType<typeof setTimeout> | null = null;
  private steps: Array<() => void> = [];
  private index = 0;
  private onComplete: (() => void) | null = null;

  constructor(scheduler: Scheduler = realScheduler, intervalMs = 520) {
    this.scheduler = scheduler;
    this.intervalMs = intervalMs;
  }

  get active(): boolean {
    return this.handle !== null;
  }

  start(steps: Array<() => void>, onComplete: () => void): void {
    this.stop();
    this.steps = [...steps];
    this.index = 0;
    this.onComplete = onComplete;
    this.scheduleNext();
  }

  stop(): void {
    if (this.handle !== null) {
      this.scheduler.clear(this.handle);
      this.handle = null;
    }
    this.steps = [];
    this.index = 0;
    this.onComplete = null;
  }

  private scheduleNext(): void {
    if (this.index >= this.steps.length) {
      const callback = this.onComplete;
      this.handle = null;
      this.onComplete = null;
      callback?.();
      return;
    }
    this.handle = this.scheduler.set(() => {
      const step = this.steps[this.index];
      this.index += 1;
      step?.();
      if (this.index >= this.steps.length) {
        const callback = this.onComplete;
        this.handle = null;
        this.onComplete = null;
        callback?.();
        return;
      }
      this.scheduleNext();
    }, this.intervalMs);
  }
}
