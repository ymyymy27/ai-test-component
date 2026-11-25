<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";

export type TaskStepKey = "scope" | "plan" | "run" | "result" | "report";

const props = withDefaults(
  defineProps<{
    projectId: string;
    taskId: string;
    current: TaskStepKey;
    hasPlan?: boolean;
    hasRun?: boolean;
    running?: boolean;
    reportId?: string;
  }>(),
  {
    hasPlan: false,
    hasRun: false,
    running: false,
    reportId: "",
  },
);

const router = useRouter();

const currentIndex = computed(() => {
  if (props.current === "scope") return 0;
  if (props.current === "plan") return 1;
  if (props.current === "run") return 2;
  return 3;
});

interface StepItem {
  key: "scope" | "plan" | "run" | "result";
  label: string;
  target: string;
  clickable: boolean;
  done: boolean;
  current: boolean;
}

const steps = computed<StepItem[]>(() => {
  const base = `/projects/${props.projectId}/tasks/${props.taskId}`;
  const locked = props.running;
  const active = currentIndex.value;
  const defs: Array<{
    key: StepItem["key"];
    label: string;
    target: string;
    reachable: boolean;
  }> = [
    {
      key: "scope",
      label: "选范围",
      target: `${base}/scope`,
      reachable: active !== 0,
    },
    {
      key: "plan",
      label: "确认计划",
      target: `${base}/plan`,
      reachable: props.hasPlan && active !== 1,
    },
    {
      key: "run",
      label: "执行测试",
      target: `${base}/run`,
      reachable: false,
    },
    {
      key: "result",
      label: "结果与报告",
      target: props.reportId ? `/projects/${props.projectId}/reports/${props.reportId}` : `${base}/result`,
      reachable: props.hasRun && active !== 3,
    },
  ];
  return defs.map((step, index) => {
    const isCurrent = index === active;
    const isDone =
      !isCurrent &&
      (index < active ||
        (step.key === "plan" && props.hasPlan) ||
        (step.key === "run" && props.hasRun) ||
        (step.key === "result" && props.hasRun));
    return {
      key: step.key,
      label: step.label,
      target: step.target,
      clickable: !locked && step.reachable,
      done: isDone,
      current: isCurrent,
    };
  });
});

function navigate(step: StepItem): void {
  if (!step.clickable) {
    return;
  }
  void router.push(step.target);
}
</script>

<template>
  <nav class="task-stepper" aria-label="任务流程">
    <ol class="task-stepper-list">
      <li v-for="(step, index) in steps" :key="step.key" class="task-stepper-item">
        <button
          type="button"
          class="task-stepper-btn"
          :class="{ 'is-current': step.current, 'is-done': step.done }"
          :disabled="!step.clickable"
          :aria-current="step.current ? 'step' : undefined"
          @click="navigate(step)"
        >
          <span class="task-stepper-index" aria-hidden="true">{{ step.done ? "✓" : index + 1 }}</span>
          <span>{{ step.label }}</span>
        </button>
      </li>
    </ol>
  </nav>
</template>
