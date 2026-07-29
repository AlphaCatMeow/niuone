<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { usePracticeCandidatesData } from '../composables/usePracticeCandidatesData.js'
import {
  practiceCandidateScanDescription,
  practiceCandidateStrategyMeta,
  practiceCandidateTierCounts,
} from '../utils/practiceCandidateDisplay.js'
import PracticeCandidateCard from './practice/PracticeCandidateCard.vue'

const { state, activatePracticeCandidates, deactivatePracticeCandidates } = usePracticeCandidatesData()
const dialogOpen = ref(false)
const launcherButton = ref(null)
const closeButton = ref(null)
const strategyMeta = computed(() => practiceCandidateStrategyMeta(state.strategyMeta))
const tierCounts = computed(() => practiceCandidateTierCounts(state.items))
const candidateCount = computed(() => Number(state.count) || state.items.length)
const scanDescription = computed(() => practiceCandidateScanDescription(
  state.strategySuite,
  state.stockUniverseLabel,
))
const launcherStatus = computed(() => {
  if (state.strategyCacheStale) return '待重新扫描'
  if (state.running) return '计算中'
  if (state.loading && !state.loaded) return '加载中'
  if (state.error) return '更新异常'
  return `${candidateCount.value}只`
})
const statusText = computed(() => state.strategyCacheStale
  ? (state.statusMessage || '策略已切换，等待重新扫描候选股')
  : state.running
  ? `计算中${state.startedAt ? ` · 开始 ${state.startedAt.slice(11)}` : ''}`
  : `扫描时间：${state.generatedAt || '--'} · ${scanDescription.value} ${candidateCount.value}只`)
function openCandidatesDialog() {
  dialogOpen.value = true
  nextTick(() => closeButton.value?.focus())
}

function closeCandidatesDialog({ restoreFocus = true } = {}) {
  if (!dialogOpen.value) return
  dialogOpen.value = false
  if (restoreFocus) nextTick(() => launcherButton.value?.focus())
}

function handleKeydown(event) {
  if (dialogOpen.value && event.key === 'Escape') closeCandidatesDialog()
}

watch(dialogOpen, (open) => {
  document.body.classList.toggle('practice-candidates-dialog-open', open)
})

onMounted(() => {
  activatePracticeCandidates()
  window.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  deactivatePracticeCandidates()
  document.body.classList.remove('practice-candidates-dialog-open')
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <section class="practice-candidates-launcher" aria-label="模拟交易候选股">
    <button
      ref="launcherButton"
      type="button"
      class="practice-candidates-launcher-button"
      aria-haspopup="dialog"
      aria-controls="practiceCandidatesDialog"
      :aria-expanded="dialogOpen"
      title="查看模拟交易买入候选股"
      @click="openCandidatesDialog"
    >
      <span>候选池</span>
      <span class="practice-candidates-launcher-status" :class="{ running: state.running, error: state.error }">
        {{ launcherStatus }}
      </span>
      <span class="practice-candidates-launcher-chevron" aria-hidden="true">›</span>
    </button>
  </section>

  <Teleport to="body">
    <div
      v-if="dialogOpen"
      class="practice-candidates-backdrop"
      role="presentation"
      @click.self="closeCandidatesDialog()"
    >
      <section
        id="practiceCandidatesDialog"
        class="practice-candidates-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="practiceCandidatesDialogTitle"
      >
        <header class="practice-candidates-dialog-head">
          <div>
            <h2 id="practiceCandidatesDialogTitle">模拟交易候选股</h2>
            <p>{{ statusText }}</p>
          </div>
          <button
            ref="closeButton"
            type="button"
            class="practice-candidates-dialog-close"
            title="关闭"
            aria-label="关闭模拟交易候选股"
            @click="closeCandidatesDialog()"
          >×</button>
        </header>

        <div class="practice-candidates-dialog-body">
          <div v-if="state.running" class="empty practice-candidates-state-notice">
            多战法正在计算中，完成后页面会自动刷新；当前下方仍显示上一版缓存结果。
          </div>
          <div v-if="state.strategyCacheStale" class="empty practice-candidates-state-notice">
            {{ state.statusMessage || '策略已切换，等待重新扫描候选股。旧策略候选已隐藏。' }}
          </div>
          <div v-if="state.loading && !state.loaded" class="loading">候选股加载中...</div>
          <div v-else-if="state.error && !state.items.length" class="empty practice-candidates-error">⚠️ {{ state.error }}</div>
          <template v-else-if="state.items.length">
            <div v-if="state.error" class="industry-flow-notice warning">候选股自动更新暂时失败，继续展示缓存结果：{{ state.error }}</div>
            <div class="practice-candidates-tier-summary">
              <span class="high">试仓 {{ tierCounts.high }}只</span>
              <span class="mid">等确认 {{ tierCounts.mid }}只</span>
              <span class="low">仅观察 {{ tierCounts.low }}只</span>
            </div>
            <div class="practice-candidates-list">
              <PracticeCandidateCard
                v-for="item in state.items"
                :key="`${item.code || item.name}-${item.best_strategy || item.score || ''}`"
                :item="item"
                :strategy-meta="strategyMeta"
              />
            </div>
          </template>
          <div v-else-if="!state.strategyCacheStale" class="empty">暂无多战法结果，请等待扫描完成…</div>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
:global(body.practice-candidates-dialog-open) {
  overflow: hidden;
}

.practice-candidates-launcher {
  display: flex;
  justify-content: flex-start;
}

.practice-candidates-launcher-button {
  align-items: center;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--text);
  display: inline-flex;
  font-size: 12px;
  font-weight: 700;
  gap: 6px;
  justify-content: center;
  min-height: 30px;
  padding: 5px 8px;
  transition: background .16s ease, border-color .16s ease, color .16s ease;
}

.practice-candidates-launcher-button:hover {
  background: var(--panel2);
  border-color: var(--accent-border);
  color: var(--accent-text);
}

.practice-candidates-launcher-button:focus-visible,
.practice-candidates-dialog-close:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.practice-candidates-launcher-status {
  background: transparent;
  border: 0;
  border-left: 1px solid var(--line);
  border-radius: 0;
  color: var(--muted);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  padding: 1px 0 1px 7px;
  white-space: nowrap;
}

.practice-candidates-launcher-chevron {
  color: var(--muted);
  font-size: 14px;
  line-height: 1;
}

.practice-candidates-launcher-status.running {
  color: var(--accent-text);
}

.practice-candidates-launcher-status.error {
  border-color: var(--red-border);
  color: var(--red-text);
}

.practice-candidates-backdrop {
  --candidate-overlay: rgba(15, 23, 42, .38);
  --candidate-dialog-surface: #f7f8fa;
  --candidate-dialog-head: #ffffff;
  --candidate-dialog-border: #d9dfe7;
  --candidate-dialog-shadow: 0 24px 64px rgba(15, 23, 42, .20);
  --candidate-card-surface: #ffffff;
  --candidate-card-border: #cfd8e3;
  --candidate-card-expanded-border: #9eafc4;
  --candidate-card-divider: #d8dee7;
  --candidate-card-subtle: #f5f7fa;
  --candidate-card-shadow: 0 2px 5px rgba(16, 24, 40, .08);
  --candidate-card-expanded-shadow: 0 10px 28px rgba(15, 23, 42, .14);
  --candidate-niuone-bg: #f4eff9;
  --candidate-niuone-border: #d7c9e7;
  --candidate-niuone-text: #74548d;
  background: var(--candidate-overlay);
  backdrop-filter: blur(8px);
  display: grid;
  inset: 0;
  padding: 18px;
  place-items: center;
  position: fixed;
  z-index: 88;
}

.practice-candidates-dialog {
  background: var(--candidate-dialog-surface);
  border: 1px solid var(--candidate-dialog-border);
  border-radius: 16px;
  box-shadow: var(--candidate-dialog-shadow);
  color: var(--text);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  max-height: min(88vh, 900px);
  max-height: min(88dvh, 900px);
  min-height: 0;
  overflow: hidden;
  width: min(1120px, calc(100vw - 32px));
}

.practice-candidates-dialog-head {
  align-items: center;
  background: var(--candidate-dialog-head);
  border-bottom: 1px solid var(--candidate-dialog-border);
  display: flex;
  gap: 14px;
  justify-content: space-between;
  padding: 14px 16px;
  position: static;
}

.practice-candidates-dialog-head > div {
  min-width: 0;
}

.practice-candidates-dialog-head h2 {
  color: var(--text);
  font-size: 17px;
  font-weight: 800;
  line-height: 1.35;
  margin: 0;
}

.practice-candidates-dialog-head p {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.45;
  margin: 3px 0 0;
  overflow-wrap: anywhere;
}

.practice-candidates-dialog-close {
  align-items: center;
  background: var(--candidate-card-subtle);
  border: 1px solid var(--candidate-card-border);
  border-radius: 9px;
  color: var(--muted);
  display: inline-flex;
  flex: 0 0 auto;
  font-size: 20px;
  height: 32px;
  justify-content: center;
  line-height: 1;
  padding: 0;
  width: 32px;
}

.practice-candidates-dialog-close:hover {
  border-color: var(--accent-border);
  color: var(--text);
}

.practice-candidates-dialog-body {
  background: var(--candidate-dialog-surface);
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 16px;
}

.practice-candidates-state-notice {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--accent-text);
}

.practice-candidates-error {
  color: var(--red-text);
}

.practice-candidates-tier-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.practice-candidates-tier-summary > span {
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 12px;
  padding: 4px 9px;
}

.practice-candidates-tier-summary .high {
  background: var(--green-soft);
  border-color: var(--green-border);
  color: var(--green-text);
}

.practice-candidates-tier-summary .mid {
  background: var(--yellow-soft);
  border-color: var(--yellow-border);
  color: var(--yellow-text);
}

.practice-candidates-tier-summary .low {
  background: var(--candidate-card-subtle);
  color: var(--muted);
}

.practice-candidates-list {
  display: grid;
  gap: 14px;
}

:global(html[data-theme="dark"] .practice-candidates-backdrop) {
  --candidate-overlay: rgba(2, 6, 23, .72);
  --candidate-dialog-surface: #0c1016;
  --candidate-dialog-head: #151a23;
  --candidate-dialog-border: #303947;
  --candidate-dialog-shadow: 0 28px 82px rgba(0, 0, 0, .54);
  --candidate-card-surface: #12171f;
  --candidate-card-border: #3a4657;
  --candidate-card-expanded-border: #5b6a80;
  --candidate-card-divider: #384454;
  --candidate-card-subtle: #181e28;
  --candidate-card-shadow: 0 4px 14px rgba(0, 0, 0, .18), inset 0 1px 0 rgba(255, 255, 255, .03);
  --candidate-card-expanded-shadow: 0 12px 30px rgba(0, 0, 0, .38), inset 0 1px 0 rgba(255, 255, 255, .04);
  --candidate-niuone-bg: #1d1923;
  --candidate-niuone-border: #40364a;
  --candidate-niuone-text: #a99bb5;
  backdrop-filter: blur(10px);
}

@media (max-width: 560px) {
  .practice-candidates-backdrop {
    padding: 12px clamp(16px, 5vw, 24px) max(16px, env(safe-area-inset-bottom));
    place-items: end center;
  }

  .practice-candidates-dialog {
    border-radius: 16px 16px 12px 12px;
    max-height: 92vh;
    max-height: 92dvh;
    width: min(100%, 440px);
  }

  .practice-candidates-dialog-head {
    padding: 9px 10px;
  }

  .practice-candidates-dialog-head h2 {
    font-size: 14px;
  }

  .practice-candidates-dialog-head p {
    font-size: 10px;
    line-height: 1.35;
    margin-top: 2px;
  }

  .practice-candidates-dialog-close {
    border-radius: 8px;
    font-size: 18px;
    height: 28px;
    width: 28px;
  }

  .practice-candidates-dialog-body {
    padding: 8px;
  }

  .practice-candidates-tier-summary {
    gap: 6px;
    margin-bottom: 10px;
  }

  .practice-candidates-tier-summary > span {
    font-size: 11px;
    padding: 3px 7px;
  }

  .practice-candidates-list {
    gap: 10px;
  }
}
</style>
