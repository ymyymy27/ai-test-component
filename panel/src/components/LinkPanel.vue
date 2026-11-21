<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { LINK_STATE_LABEL } from "@/domain/labels";
import type { LinkData, LinkNode } from "@/domain/types";
import type { LinkAssertInput } from "@/repositories/ProjectRepository";
import { useUiStore } from "@/stores/ui";
import Icon from "./Icon.vue";
import StatusMark from "./StatusMark.vue";

const ui = useUiStore();

const props = defineProps<{
  link: LinkData | null;
  busy: boolean;
  hostMode: boolean;
}>();

const emit = defineEmits<{
  load: [link: LinkData];
  hostImport: [];
  assert: [input: LinkAssertInput];
}>();

const raw = ref("");
const error = ref("");
const nodeId = ref("");
const expected = ref("");
const actual = ref("");
const operator = ref<"equals" | "contains" | "not_equals">("equals");

const selectedNode = computed<LinkNode | null>(
  () => props.link?.nodes.find((node) => node.id === nodeId.value) ?? null,
);

function parseLink(text: string): LinkData {
  const parsed = JSON.parse(text) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("链路 JSON 必须是对象。");
  }
  const source = parsed as Record<string, unknown>;
  if (!Array.isArray(source.nodes)) {
    throw new Error("链路 JSON 缺少 nodes 数组。");
  }
  const nodes: LinkNode[] = source.nodes.map((item, index) => {
    if (!item || typeof item !== "object") {
      throw new Error(`第 ${index + 1} 个节点不是对象。`);
    }
    const node = item as Record<string, unknown>;
    const state = String(node.state ?? node.status ?? "not_run");
    return {
      id: String(node.id ?? node.node_id ?? `node_${index + 1}`),
      name: String(node.name ?? node.node_id ?? `节点 ${index + 1}`),
      state: ["passed", "failed", "blocked", "gap", "not_run"].includes(state)
        ? (state as LinkNode["state"])
        : "not_run",
      detail: String(node.detail ?? node.message ?? ""),
      evidenceRef: String(node.evidence_ref ?? node.evidenceRef ?? ""),
    };
  });
  const rawLocation = source.location;
  let known = false;
  let path = "";
  let line = 0;
  let reason = "没有可定位的异常节点。";
  if (rawLocation && typeof rawLocation === "object") {
    const location = rawLocation as Record<string, unknown>;
    const nested = location.location;
    if (nested && typeof nested === "object") {
      const inner = nested as Record<string, unknown>;
      known = Boolean(inner.known);
      path = String(inner.path ?? "");
      line = Number(inner.line ?? 0);
      reason = String(inner.reason ?? "");
    } else {
      known = Boolean(location.known);
      path = String(location.path ?? "");
      line = Number(location.line ?? 0);
      reason = String(location.reason ?? "");
    }
  }
  return {
    nodes,
    assertions: Array.isArray(source.assertions)
      ? source.assertions
          .filter(
            (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
          )
          .map((item) => ({
            nodeId: String(item.node_id ?? item.nodeId ?? ""),
            expected: String(item.expected ?? ""),
            actual: String(item.actual ?? ""),
            result: item.result === "passed" ? "passed" : "failed",
            operator: String(item.operator ?? "equals"),
          }))
      : [],
    gaps: Array.isArray(source.gaps)
      ? source.gaps
          .filter(
            (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
          )
          .map((item) => ({
            nodeId: String(item.node_id ?? item.nodeId ?? ""),
            reason: String(item.reason ?? ""),
          }))
      : [],
    location: { known, path, line, reason },
    unknown: String(source.unknown ?? ""),
  };
}

async function submitLink(): Promise<void> {
  error.value = "";
  if (!raw.value.trim()) {
    error.value = "先粘贴或填写链路 JSON。";
    return;
  }
  let parsed: LinkData;
  try {
    parsed = parseLink(raw.value);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "链路 JSON 不合法。";
    return;
  }
  if (props.link && (props.link.nodes.length > 0 || props.link.assertions.length > 0)) {
    const answer = await ui.askConfirm({
      title: "替换已导入的链路？",
      body: "导入会整体替换当前节点、缺口与断言；已有关联断言不会保留。",
      confirmLabel: "替换链路",
      tone: "danger",
    });
    if (answer === null) {
      return;
    }
  }
  // 节点集合会变化，旧 nodeId 可能不存在；清空选择避免对旧节点补断言。
  nodeId.value = "";
  expected.value = "";
  actual.value = "";
  emit("load", parsed);
  // 保存成功时 link 会更新；失败时保留粘贴内容，避免重新输入。
}

watch(
  () => props.link,
  (value, previous) => {
    if (value && value !== previous) {
      raw.value = "";
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
    <p v-else class="doc-note">这次运行还没有导入链路。链路只提供定位与证据，不改变结论。</p>

    <div v-if="hostMode" class="stack-sm">
      <p class="doc-note">
        宿主模式从项目配置中的日志和查询导入链路；面板不能自行生成链路事实。
      </p>
      <button class="btn btn-sm" type="button" :disabled="busy" @click="emit('hostImport')">
        从项目配置导入链路
      </button>
    </div>
    <details v-else class="link-tools">
      <summary>导入链路 JSON</summary>
      <div class="field">
        <label for="link-json">链路 JSON</label>
        <textarea
          id="link-json"
          v-model="raw"
          class="code-input"
          rows="6"
          placeholder='{"nodes":[{"id":"request","name":"请求到达","state":"passed","detail":"..."}],"gaps":[],"location":{"known":false,"reason":"..."}}'
        />
        <p v-if="error" class="field-error" role="alert">{{ error }}</p>
      </div>
      <button class="btn btn-sm" type="button" :disabled="busy" @click="submitLink">
        导入并重新生成报告
      </button>
    </details>

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
