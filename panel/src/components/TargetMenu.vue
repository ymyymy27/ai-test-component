<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import { sourceLabel } from "@/api/labels";
import AppButton from "@/components/AppButton.vue";
import Icon from "@/components/Icon.vue";
import {
  chooseCustomTarget,
  chooseTarget,
  session,
} from "@/state/session";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: [] }>();

const proposal = computed(() => session.proposal);
const panel = ref<HTMLElement | null>(null);
const customOpen = ref(false);
const customText = ref("");
const customInput = ref<HTMLInputElement | null>(null);

async function onOpen(): Promise<void> {
  customOpen.value = false;
  customText.value = "";
  await nextTick();
  panel.value?.querySelector<HTMLElement>("button")?.focus();
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      document.addEventListener("keydown", onKeydown);
      void onOpen();
    } else {
      document.removeEventListener("keydown", onKeydown);
    }
  },
);

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
  }
}

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown);
});

async function pick(index: number): Promise<void> {
  await chooseTarget(index);
  emit("close");
}

async function openCustom(): Promise<void> {
  customOpen.value = true;
  await nextTick();
  customInput.value?.focus();
}

async function submitCustom(): Promise<void> {
  if (!customText.value.trim()) {
    return;
  }
  await chooseCustomTarget(customText.value);
  emit("close");
}
</script>

<template>
  <div v-if="open" class="menu-scrim" @click.self="emit('close')">
    <section
      ref="panel"
      class="menu"
      role="dialog"
      aria-label="换一个测试目标"
      aria-modal="false"
    >
      <header class="menu-head">
        <h2>换一个目标</h2>
        <button type="button" class="close" aria-label="关闭" @click="emit('close')">
          <Icon name="close" :size="14" />
        </button>
      </header>

      <p v-if="proposal.suggestLoading" class="hint">正在生成目标候选…</p>

      <ul v-else class="options" role="list">
        <li v-for="(candidate, index) in proposal.candidates" :key="candidate.title">
          <button
            type="button"
            class="option"
            :class="{ current: proposal.index === index }"
            :aria-current="proposal.index === index ? 'true' : undefined"
            @click="pick(index)"
          >
            <span class="option-title">{{ candidate.title }}</span>
            <span class="option-meta">
              <span v-if="candidate.reason">{{ candidate.reason }}</span>
              <span v-if="sourceLabel(candidate.source)" class="quiet">
                来源：{{ sourceLabel(candidate.source) }}
              </span>
            </span>
            <span v-if="proposal.index === index" class="current-mark">当前</span>
          </button>
        </li>
        <li v-if="proposal.fallback">
          <button type="button" class="option fallback" @click="pick(-2)">
            <span class="option-title">{{ proposal.fallback }}</span>
            <span class="option-meta quiet">通用目标：先确认项目能启动并跑通主流程</span>
          </button>
        </li>
      </ul>

      <div class="custom">
        <button v-if="!customOpen" type="button" class="custom-link" @click="openCustom">
          <Icon name="plus" :size="13" />
          自己写一句
        </button>
        <form v-else class="custom-form" @submit.prevent="submitCustom">
          <input
            ref="customInput"
            v-model="customText"
            class="custom-input"
            type="text"
            placeholder="例如：下单成功后订单出现在列表里，库存只扣一次"
            aria-label="测试目标"
            autocomplete="off"
          />
          <div class="row">
            <AppButton html-type="submit" size="sm" variant="primary" :disabled="!customText.trim()">
              用这个目标
            </AppButton>
            <AppButton size="sm" variant="ghost" @click="customOpen = false; customText = ''">
              收起
            </AppButton>
          </div>
        </form>
      </div>

      <p class="hint custom-note">换目标只会重新提取验收标准，不会开始运行。</p>
    </section>
  </div>
</template>

<style scoped>
.menu-scrim {
  position: fixed;
  inset: 0;
  z-index: 30;
}

.menu {
  position: fixed;
  top: 56px;
  right: max(16px, calc((100vw - 760px) / 2 + 24px));
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: min(360px, calc(100vw - 32px));
  max-height: min(70vh, 520px);
  overflow-y: auto;
  padding: var(--space-4);
  background: var(--surface);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
  animation: drop 160ms cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes drop {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
}

.menu-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.menu-head h2 {
  font-size: var(--fs-14);
}

.close {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  color: var(--text-3);
}

.close:hover {
  color: var(--text);
  background: var(--surface-raised);
  border-color: var(--line);
}

.options {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  overflow: hidden;
}

.option {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 10px var(--space-3);
  background: var(--surface-sunken);
  border: 0;
  text-align: left;
  color: var(--text);
}

.option:hover {
  background: var(--surface-raised);
}

.option.current {
  background: var(--accent-fill);
}

.option-title {
  font-size: var(--fs-13);
  line-height: 1.45;
  padding-right: 42px;
  overflow-wrap: anywhere;
}

.option-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.current-mark {
  position: absolute;
  top: 10px;
  right: 10px;
  padding: 0 5px;
  border: 1px solid var(--accent-line);
  border-radius: var(--r-chip);
  color: var(--accent);
  font-size: var(--fs-12);
}

.fallback {
  border-top: 1px solid var(--line);
}

.custom {
  padding-top: var(--space-2);
  border-top: 1px solid var(--line);
}

.custom-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 0;
  background: none;
  border: 0;
  color: var(--text-3);
  font-size: var(--fs-12);
}

.custom-link:hover {
  color: var(--accent);
}

.custom-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.custom-input {
  width: 100%;
  min-height: 32px;
  padding: 6px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
}

.custom-input::placeholder {
  color: var(--text-disabled);
}

.custom-note {
  padding-top: var(--space-1);
}
</style>
