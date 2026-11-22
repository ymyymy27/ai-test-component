<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { LINK_STATE_LABEL } from "@/domain/labels";
import type { LinkData, LinkNode } from "@/domain/types";
import type { LinkAssertInput } from "@/repositories/ProjectRepository";
import Icon from "./Icon.vue";
import StatusMark from "./StatusMark.vue";

const props = defineProps<{
  link: LinkData | null;
  busy: boolean;
}>();

const emit = defineEmits<{
  assert: [input: LinkAssertInput];
}>();

const error = ref("");
const nodeId = ref("");
const expected = ref("");
const actual = ref("");
const operator = ref<"equals" | "contains" | "not_equals">("equals");

const selectedNode = computed<LinkNode | null>(
  () => props.link?.nodes.find((node) => node.id === nodeId.value) ?? null,
);

watch(
  () => props.link,
  (value, previous) => {
    if (value && value !== previous) {
      error.value = "";
      nodeId.value = "";
      expected.value = "";
      actual.value = "";
    }
  },
);

function selectNode(value: string): void {
  nodeId.value = value;
  const node = props.link?.nodes.find((entry) => entry.id === value) ?? null;
  // 预期必须由人按验收要求填写；用当前状态预填会产生同义反复的通过断言。
  expected.value = "";
  actual.value = node?.state ?? "";
}

function onNodeChange(event: Event): void {
  const target = event.target;
  if (target instanceof HTMLSelectElement) {
    selectNode(target.value);
  }
}

function assertNode(): void {
  if (!nodeId.value) {
    return;
  }
  if (!expected.value.trim()) {
    error.value = "先填写预期值；不要直接使用当前状态，否则断言必然通过。";
    return;
  }
  error.value = "";
  emit("assert", {
    nodeId: nodeId.value,
    expected: expected.value,
    actual: actual.value,
    operator: operator.value,
  });
}
</script>

<template>
  <div class="stack">
    <div v-if="link" class="chain">
      <div v-for="node in link.nodes" :key="node.id" class="check-row">
        <StatusMark :result="node.state === 'gap' ? 'blocked' : node.state" />
        <span class="check-name">{{ node.name }}</span>
        <span class="check-method">{{ node.detail }}</span>
        <span
          class="badge"
          :class="node.state === 'failed' || node.state === 'gap' ? 'is-warn' : 'is-neutral'"
        >
          {{ LINK_STATE_LABEL[node.state] }}
        </span>
      </div>
      <p v-if="link.location.known" class="doc-note">
        首个观测异常定位：<span class="mono"
          >{{ link.location.path }}:{{ link.location.line }}</span
        >
      </p>
      <p v-else class="doc-note">{{ link.location.reason || "没有可定位的异常节点。" }}</p>
      <p v-if="link.gaps.length" class="doc-note">
        缺口：{{ link.gaps.map((gap) => gap.reason).join("；") }}
      </p>
    </div>
    <p v-else class="doc-note">
      本次运行没有自动生成调用链。链路只提供定位与证据，不改变结论。
    </p>

    <details v-if="link" class="link-tools">
      <summary>对节点补充断言</summary>
      <div class="form-grid">
        <div class="field">
          <label for="link-node">节点</label>
          <select id="link-node" :value="nodeId" @change="onNodeChange">
            <option value="">选择节点</option>
            <option v-for="node in link.nodes" :key="node.id" :value="node.id">
              {{ node.name }}
            </option>
          </select>
        </div>
        <div class="field">
          <label for="link-operator">比较方式</label>
          <select id="link-operator" v-model="operator">
            <option value="equals">等于</option>
            <option value="contains">包含</option>
            <option value="not_equals">不等于</option>
          </select>
        </div>
      </div>
      <div class="form-grid">
        <div class="field">
          <label for="link-expected">预期</label>
          <input id="link-expected" v-model="expected" type="text" />
        </div>
        <div class="field">
          <label for="link-actual">实际</label>
          <input id="link-actual" v-model="actual" type="text" />
        </div>
      </div>
      <p v-if="selectedNode" class="field-hint">
        当前节点状态：{{ LINK_STATE_LABEL[selectedNode.state] }}。预期请按验收要求手写，不要直接复制当前状态。
      </p>
      <p v-if="error" class="field-error" role="alert">{{ error }}</p>
      <button
        class="btn btn-sm"
        type="button"
        :disabled="busy || !nodeId || !expected.trim()"
        @click="assertNode"
      >
        写入断言并重新生成报告
      </button>
    </details>
    <p v-if="busy" class="field-hint"><Icon name="refresh" :size="12" /> 正在更新链路…</p>
  </div>
</template>
