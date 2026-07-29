<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PracticePositionCard from './PracticePositionCard.vue'
import PracticeSoldCard from './PracticeSoldCard.vue'

const props = defineProps({
  positions: { type: Array, default: () => [] },
  soldStocks: { type: Array, default: () => [] },
  totalEquity: { type: Number, default: 0 },
  strategyMeta: { type: Object, default: () => ({}) },
})

const params = new URLSearchParams(location.search)
const mode = ref(params.get('holdings') === 'sold' ? 'sold' : 'open')
const brief = ref(params.get('brief') === '1')
const showSold = computed(() => mode.value === 'sold')

function syncUrl() {
  const next = new URL(location.href)
  if (mode.value === 'sold') next.searchParams.set('holdings', 'sold')
  else next.searchParams.delete('holdings')
  if (brief.value && mode.value === 'open') next.searchParams.set('brief', '1')
  else next.searchParams.delete('brief')
  history.replaceState(null, '', `${next.pathname}${next.search}${next.hash}`)
}

function setMode(nextMode) {
  mode.value = nextMode === 'sold' ? 'sold' : 'open'
  syncUrl()
}

function setBrief(enabled) {
  brief.value = Boolean(enabled)
  syncUrl()
}

function restoreFromUrl() {
  const nextParams = new URLSearchParams(location.search)
  mode.value = nextParams.get('holdings') === 'sold' ? 'sold' : 'open'
  brief.value = nextParams.get('brief') === '1'
}

onMounted(() => window.addEventListener('popstate', restoreFromUrl))
onBeforeUnmount(() => window.removeEventListener('popstate', restoreFromUrl))
</script>

<template>
  <section class="practice-positions-section" aria-labelledby="practicePositionsTitle">
    <div class="practice-position-bar">
      <div class="practice-position-context">
        <div class="practice-position-heading-copy">
          <h4 id="practicePositionsTitle">模拟持仓</h4>
        </div>
        <div v-if="$slots['candidate-entry']" class="practice-position-source-flow">
          <slot name="candidate-entry" />
        </div>
      </div>
      <div class="practice-position-toolbar" :class="{ 'single-control': showSold }">
        <div class="practice-mode-control" aria-label="持仓视图">
          <button class="practice-mode-btn" :class="{ active: !showSold }" type="button" @click="setMode('open')">当前持仓{{ positions.length ? ` ${positions.length}` : '' }}</button>
          <button class="practice-mode-btn" :class="{ active: showSold }" type="button" @click="setMode('sold')">今日卖出{{ soldStocks.length ? ` ${soldStocks.length}` : '' }}</button>
        </div>
        <div v-if="!showSold" class="practice-mode-control" aria-label="持仓显示模式">
          <button class="practice-mode-btn" :class="{ active: !brief }" type="button" @click="setBrief(false)">完整</button>
          <button class="practice-mode-btn" :class="{ active: brief }" type="button" @click="setBrief(true)">简要</button>
        </div>
      </div>
    </div>
    <div v-if="showSold" class="position-card-list">
      <PracticeSoldCard v-for="sold in soldStocks" :key="`${sold.code}-${sold.last_sell_time || ''}`" :sold="sold" />
      <div v-if="!soldStocks.length" class="empty" style="padding:18px;font-size:13px">今日暂无卖出股票</div>
    </div>
    <div v-else :class="positions.length && brief ? 'position-brief-grid' : 'position-card-list'">
      <PracticePositionCard
        v-for="position in positions"
        :key="position.code"
        :position="position"
        :total-equity="totalEquity"
        :brief="brief"
        :strategy-meta="strategyMeta"
      />
      <div v-if="!positions.length" class="empty" style="padding:18px;font-size:13px">暂无持仓，等待模型决策建仓</div>
    </div>
  </section>
</template>

<style scoped>
.practice-positions-section {
  margin-top: 12px;
}

.practice-position-bar {
  align-items: center;
  display: flex;
  gap: 16px;
  justify-content: space-between;
  margin-bottom: 8px;
  min-width: 0;
  padding: 2px;
}

.practice-position-context {
  align-items: center;
  display: flex;
  gap: 12px;
  min-width: 0;
}

.practice-position-heading-copy {
  min-width: 0;
}

.practice-position-heading-copy h4 {
  color: var(--text);
  font-size: 14px;
  font-weight: 800;
  line-height: 1.35;
  margin: 0;
}

.practice-position-toolbar {
  align-items: center;
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
}

.practice-position-source-flow {
  align-items: center;
  display: inline-flex;
  flex: 0 0 auto;
}

@media (max-width: 720px) {
  .practice-position-bar {
    align-items: stretch;
    flex-direction: column;
    gap: 8px;
  }

  .practice-position-context {
    justify-content: space-between;
  }

  .practice-position-heading-copy h4 {
    font-size: 13px;
  }

  .practice-position-toolbar {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .practice-position-toolbar.single-control {
    grid-template-columns: minmax(0, 1fr);
  }

  .practice-position-toolbar .practice-mode-control {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: 100%;
  }

  .practice-position-toolbar .practice-mode-btn {
    min-width: 0;
  }
}
</style>
