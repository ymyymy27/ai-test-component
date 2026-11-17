<script setup lang="ts">
import { computed } from "vue";

import AppButton from "@/components/AppButton.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import {
  applicableCount,
  chooseCustomTarget,
  chooseTarget,
  loadProposal,
  selectedCount,
  session,
  startTest,
  toggleProposalItem,
} from "@/state/session";

const proposal = computed(() => session.proposal);

const scopeNote = computed(() => {
  const total = applicableCount();
  const chosen = selectedCount();
  if (total === 0) {
    return "没有可纳入的验收项。";
  }
  if (chosen === total) {
    return `全部 ${total} 条适用验收项都会进入本次范围。`;
  }
  return `已排除 ${total - chosen} 条；这些不进入本次结论。`;
});

const envText = computed(() => {
  switch (session.env) {
    case "ok":
      return "本机 · 环境正常";
    case "warn":
      return "本机 · 部分能力缺失，AI 可能不可用";
    case "error":
      return "本机 · 环境异常，点顶部状态条处理";
    default:
      return "本机 · 环境检查中";
  }
});

function retryExtract(): void {
  const index = session.proposal.index;
  if (index >= 0 || index === -2) {
    void chooseTarget(index);
    return;
  }
  void chooseCustomTarget(session.proposal.goal);
}
</script>

<template>
  <section class="proposal" data-testid="proposal-panel">
    <SkeletonBlock
      v-if="proposal.suggestLoading || (proposal.itemsLoading && proposal.items.length === 0)"
      :lines="4"
      :label="proposal.suggestLoading ? '正在生成目标候选' : '正在从目标里提取验收标准'"
    />

    <template v-else>
      <ErrorNotice
        v-if="proposal.suggestError"
        :error="proposal.suggestError"
        retry-label="重新获取"
        @retry="loadProposal"
      />

      <section class="scope-block" data-testid="proposal-scope">
        <div class="scope-head">
          <span class="scope-label"><span class="code">RANGE</span> / 范围</span>
          <span class="scope-value numeric" data-testid="proposal-scope-value">
            {{ selectedCount() }}<span class="slash">/</span>{{ applicableCount() }}
            <span class="quiet">条适用</span>
          </span>
        </div>
        <div
          class="scope-ticks"
          role="progressbar"
          :aria-valuenow="selectedCount()"
          :aria-valuemin="0"
          :aria-valuemax="applicableCount()"
          aria-label="本次范围覆盖"
        >
          <span
            v-for="item in proposal.items"
            :key="item.id"
            class="scope-tick"
            :class="{
              on: item.enabled,
              off: item.observable && !item.enabled,
              unavailable: !item.observable,
            }"
          ></span>
        </div>
        <p class="hint scope-note">{{ scopeNote }}</p>
      </section>

      <section class="criteria-block">
        <div class="console-head">
          <span class="console-label">CHECKS / 验收标准</span>
          <span class="console-count numeric">{{ String(applicableCount()).padStart(2, "0") }}</span>
          <span class="console-note">默认全选，点掉即排除</span>
        </div>

        <ErrorNotice
          v-if="proposal.itemsError"
          :error="proposal.itemsError"
          retry-label="重新提取"
          :busy="proposal.itemsLoading"
          @retry="retryExtract"
        />

        <ul v-if="proposal.items.length" class="criteria" data-testid="proposal-criteria">
          <li
            v-for="(item, index) in proposal.items"
            :key="item.id"
            class="criteria-row"
            :class="{ off: !item.observable, excluded: item.observable && !item.enabled }"
          >
            <span class="criteria-index numeric">{{ String(index + 1).padStart(2, "0") }}</span>
            <button
              v-if="item.observable"
              type="button"
              class="check"
              role="switch"
              :aria-checked="item.enabled"
              :aria-label="`纳入：${item.text}`"
              @click="toggleProposalItem(item)"
            >
              <span class="check-box" :class="{ on: item.enabled }" aria-hidden="true">
                <Icon v-if="item.enabled" name="check" :size="10" />
              </span>
            </button>
            <span v-else class="switch-placeholder" aria-hidden="true">—</span>
            <span class="criteria-text">{{ item.text }}</span>
            <span class="criteria-note">
              {{ item.observable ? item.source : "不可观察，暂不纳入" }}
            </span>
          </li>
        </ul>

        <p v-else-if="!proposal.itemsLoading" class="hint empty-note">
          没有提取出可观察的验收标准。打开“调整”补一条，或换一个目标。
        </p>
      </section>

      <dl class="facts">
        <div class="fact">
          <dt><span class="code">ENV</span> 环境</dt>
          <dd>{{ envText }}</dd>
        </div>
        <div class="fact">
          <dt><span class="code">WRITES</span> 副作用</dt>
          <dd>可能写入测试数据；默认不自动清理。</dd>
        </div>
        <div class="fact">
          <dt><span class="code">EST</span> 预计耗时</dt>
          <dd>未提供估算；按用例数量与项目启动时间而定。</dd>
        </div>
      </dl>

      <ErrorNotice
        v-if="proposal.startError"
        :error="proposal.startError"
        :show-retry="false"
        @retry="() => {}"
      />

      <footer class="sheet-foot">
        <p class="hint foot-note">
          点一下即确认本次范围并开始；运行在后台继续，关闭面板不会中断。
        </p>
        <AppButton
          variant="primary"
          :disabled="applicableCount() === 0"
          :loading="proposal.starting"
          data-testid="start-test"
          @click="startTest"
        >
          <Icon name="play" :size="13" />
          开始测试
          <span class="run-code">RUN</span>
        </AppButton>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.proposal {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.criteria-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.criteria-block .section-title {
  margin-bottom: 0;
}

.criteria-block h2 {
  font-size: var(--fs-14);
}

.criteria {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid var(--line);
}

.console-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--line-strong);
}

.console-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--text-3);
}

.console-count {
  font-family: var(--font-mono);
  font-size: var(--fs-16);
  color: var(--accent);
}

.console-note {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-3);
}

.criteria-row {
  display: grid;
  grid-template-columns: 22px 20px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-2);
  padding: 7px 0;
  border-bottom: 1px solid var(--line);
}

.criteria-index {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-disabled);
}

.criteria-row:last-child {
  border-bottom: 0;
}

.criteria-text {
  font-size: var(--fs-13);
  line-height: 1.5;
  word-break: break-word;
  transition: color 140ms ease;
}

.criteria-row.excluded .criteria-text {
  color: var(--text-3);
}

.criteria-row.off .criteria-text {
  color: var(--text-3);
}

.criteria-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  max-width: 18ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.check {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  background: none;
  border: 0;
}

.check-box {
  display: grid;
  place-items: center;
  width: 14px;
  height: 14px;
  border: 1px solid var(--line-strong);
  border-radius: 1px;
  background: var(--surface-sunken);
  color: var(--bg);
}

.check-box.on {
  background: var(--accent);
  border-color: var(--accent);
}

.check:hover .check-box {
  border-color: var(--accent);
}

.check:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}

.switch-placeholder {
  color: var(--text-disabled);
  text-align: center;
}

.code {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  color: var(--text-3);
}

.empty-note {
  padding: var(--space-3) 0;
}

.scope-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--space-3) 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.scope-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.scope-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.scope-value {
  font-size: var(--fs-14);
  color: var(--text);
}

.slash {
  margin: 0 2px;
  color: var(--text-3);
}

.scope-ticks {
  display: flex;
  gap: 2px;
  height: 8px;
}

.scope-tick {
  flex: 1 1 0;
  min-width: 2px;
  border-radius: 1px;
  background: var(--line);
}

.scope-tick.off {
  background: var(--line-strong);
}

.scope-tick.unavailable {
  background: var(--surface-sunken);
  border: 1px solid var(--line);
}

.scope-tick.on {
  background: var(--accent-line);
}

.scope-note {
  margin-top: 2px;
}

.facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
  margin: 0;
  padding: var(--space-3) 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.fact {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.fact dt {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.fact dd {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.55;
}

.sheet-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding-top: var(--space-3);
  border-top: 1px solid var(--line);
}

.run-code {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.14em;
  opacity: 0.65;
}

.foot-note {
  flex: 1 1 220px;
  max-width: 44ch;
}

@media (max-width: 520px) {
  .criteria-row {
    grid-template-columns: 18px 20px minmax(0, 1fr);
    row-gap: 2px;
  }

  .criteria-note {
    grid-column: 3;
    max-width: 100%;
  }

  .facts {
    grid-template-columns: 1fr;
    gap: var(--space-3);
  }

  .sheet-foot :deep(.btn) {
    width: 100%;
  }
}
</style>
