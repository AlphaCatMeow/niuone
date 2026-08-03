<script setup>
import { computed, ref } from 'vue'
import {
  formatPracticeNumber,
  PRACTICE_TIDE_STATUS_LABELS,
  practiceCandidateIndustryLabel,
  practiceNiuoneLifecycleLabel,
  practiceCandidateTier,
} from '../../utils/practiceCandidateDisplay.js'

const props = defineProps({
  item: { type: Object, required: true },
  strategyMeta: { type: Object, required: true },
})

const strategyName = computed(() => String(props.item.best_strategy || ''))
const strategy = computed(() => props.strategyMeta[strategyName.value] || {
  label: strategyName.value || '综合',
  color: '#94a3b8',
})
const tideStrategy = computed(() => ['tide_leader', 'tide_rotation', 'tide_recovery'].includes(strategyName.value))
const niuoneStrategy = computed(() => ['niu_leader', 'niu_pullback', 'niu_emerging', 'niu_reversal_probe'].includes(strategyName.value))
const reversalStrategy = computed(() => strategyName.value === 'niu_reversal_probe')
const dynamicStrategy = computed(() => tideStrategy.value || niuoneStrategy.value)
const zettarancStrategy = computed(() => ['shaofu_b1', 'b2_confirm', 'b3_accelerate', 'super_b1'].includes(strategyName.value))
const uniqueFlags = (values) => [...new Set(
  (Array.isArray(values) ? values : [])
    .map((value) => String(value || '').trim())
    .filter(Boolean),
)]
const flagKey = (value) => String(value || '').replace(/[，。、；：:/\s]|或/g, '')
const hardBlockers = computed(() => uniqueFlags(props.item.hard_blockers))
const riskFlags = computed(() => {
  const blockers = new Set(hardBlockers.value.map(flagKey))
  return uniqueFlags(props.item.risk_flags).filter((flag) => !blockers.has(flagKey(flag)))
})
const tier = computed(() => practiceCandidateTier(props.item))
const tierLabel = computed(() => ({ high: '交易达标', mid: hardBlockers.value.length ? '未达标' : '等确认', low: '仅观察' })[tier.value])
const industryLabel = computed(() => practiceCandidateIndustryLabel(props.item))
const change = computed(() => Number(props.item.change_pct))
const changeText = computed(() => Number.isFinite(change.value)
  ? `${change.value > 0 ? '+' : ''}${change.value.toFixed(2)}%`
  : '--')
const changeClass = computed(() => change.value > 0 ? 'up' : change.value < 0 ? 'down' : 'flat')
const distance = computed(() => Number(props.item.distance_pct))
const distanceText = computed(() => Number.isFinite(distance.value)
  ? `${distance.value > 0 ? '+' : ''}${distance.value.toFixed(2)}%`
  : '--')
const score = computed(() => props.item.best_score ?? props.item.score ?? 0)
const threshold = computed(() => Number(props.item.entry_threshold ?? 8))
const scoreBasis = computed(() => String(props.item.score_basis || ''))
const tradeDiscipline = computed(() => [props.item.position_hint, props.item.time_stop].filter(Boolean).join(' · '))
const marketRegimeLabel = computed(() => ({
  defensive: '防守',
  offensive: '进攻',
  rotation: '轮动',
  recovery: '修复',
  balanced: '均衡',
}[props.item.market_regime] || props.item.market_regime || '--'))
const mainlineModeLabel = computed(() => ({
  single: '单主线',
  dual: '双主线',
  none: '未形成',
}[props.item.mainline_mode] || props.item.mainline_mode || '未形成'))
const mainlineStateLabel = computed(() => (
  PRACTICE_TIDE_STATUS_LABELS[props.item.mainline_state] || props.item.mainline_state || '--'
))
const lifecycleLabel = computed(() => practiceNiuoneLifecycleLabel(props.item))
const mainlineThemes = computed(() => [props.item.mainline_primary, props.item.mainline_secondary]
  .filter(Boolean)
  .join(' / ') || '--')
const detailsExpanded = ref(false)
const candidateDetailsId = computed(() => (
  `practice-candidate-details-${String(props.item.code || props.item.name || 'unknown')
    .replace(/[^a-zA-Z0-9_-]/g, '-')}-${strategyName.value || 'general'}`
))
function toggleCandidateDetails() {
  if (niuoneStrategy.value) {
    detailsExpanded.value = !detailsExpanded.value
  }
}
</script>

<template>
  <article
    class="practice-candidate-card"
    :class="{
      'niuone-candidate-card': niuoneStrategy,
      'details-expanded': detailsExpanded,
    }"
  >
    <div
      class="candidate-summary"
      :class="{ 'has-industry': industryLabel }"
      :role="niuoneStrategy ? 'button' : undefined"
      :tabindex="niuoneStrategy ? 0 : undefined"
      :aria-expanded="niuoneStrategy ? detailsExpanded : undefined"
      :aria-controls="niuoneStrategy ? candidateDetailsId : undefined"
      :aria-label="niuoneStrategy
        ? `${item.code || ''} ${item.name || ''}，${detailsExpanded ? '收起' : '查看'}详情`
        : undefined"
      @click="toggleCandidateDetails"
      @keydown.enter.prevent="toggleCandidateDetails"
      @keydown.space.prevent="toggleCandidateDetails"
    >
      <div class="candidate-primary">
        <div class="candidate-identity">
          <span class="candidate-stock-name">{{ item.code }} {{ item.name }}</span>
          <span
            class="candidate-strategy-badge"
            :style="{
              '--candidate-strategy-bg': `${strategy.color}22`,
              '--candidate-strategy-border': `${strategy.color}44`,
              '--candidate-strategy-text': strategy.color,
            }"
          >{{ strategy.label }}</span>
        </div>
      </div>
      <div v-if="industryLabel" class="candidate-industry">
        <span class="candidate-industry-badge">{{ industryLabel }}</span>
      </div>
      <span class="candidate-tier" :class="tier">{{ tierLabel }}</span>
    </div>
    <div :id="niuoneStrategy ? candidateDetailsId : undefined" class="candidate-details">
      <div class="candidate-metric-grid">
        <div class="candidate-metric">
          <div style="color:var(--muted);font-size:11px">价格 / 涨跌</div>
          <div style="color:var(--text);font-size:14px;font-weight:600">
            {{ formatPracticeNumber(item.price) }} <span class="index-change" :class="changeClass" style="font-size:13px">{{ changeText }}</span>
          </div>
        </div>
        <div class="candidate-metric">
          <div style="color:var(--muted);font-size:11px">{{ strategy.label }}评分</div>
          <div style="color:var(--text);font-size:14px;font-weight:600">{{ score }}/{{ item.score_total || 10 }} · 基准≥{{ threshold }}</div>
        </div>
        <div class="candidate-metric">
          <div style="color:var(--muted);font-size:11px">{{ dynamicStrategy ? 'EMA20 / 距EMA20' : 'BBI / 距BBI' }}</div>
          <div style="color:var(--text);font-size:14px;font-weight:600">{{ formatPracticeNumber(dynamicStrategy ? item.ema20 : item.bbi) }} / {{ distanceText }}</div>
        </div>
        <div class="candidate-metric">
          <div style="color:var(--muted);font-size:11px">成交额</div>
          <div style="color:var(--text);font-size:14px;font-weight:600">{{ item.amount_yi != null ? `${item.amount_yi}亿` : '--' }}</div>
        </div>
      </div>
      <div v-if="niuoneStrategy" class="niuone-details">
        <section class="niuone-detail-section">
          <h4>主线与龙头</h4>
          <div class="niuone-fact-grid">
            <div class="niuone-fact">
              <span>市场环境</span>
              <strong>{{ marketRegimeLabel }} · {{ formatPracticeNumber(item.market_score) }}</strong>
            </div>
            <div class="niuone-fact">
              <span>生命周期</span>
              <strong>{{ lifecycleLabel }}</strong>
            </div>
            <div class="niuone-fact">
              <span>主线状态</span>
              <strong>{{ mainlineStateLabel }} · {{ formatPracticeNumber(item.mainline_score) }}</strong>
            </div>
            <div class="niuone-fact">
              <span>核心题材</span>
              <strong>{{ mainlineThemes }}</strong>
            </div>
            <div class="niuone-fact">
              <span>主线结构</span>
              <strong>{{ mainlineModeLabel }}</strong>
            </div>
            <div class="niuone-fact">
              <span>跨日确认 / 延续核心</span>
              <strong>{{ item.mainline_cross_day_confirmed ? '已完成' : '待完成' }} · {{ item.mainline_core_overlap_count ?? 0 }}只</strong>
            </div>
            <div class="niuone-fact">
              <span>强势股 / 有效强势股</span>
              <strong>{{ item.strong_stock_count ?? '--' }}只 · {{ formatPracticeNumber(item.effective_strong_count) }}只</strong>
            </div>
            <div class="niuone-fact">
              <span>龙头梯队</span>
              <strong>#{{ item.stock_leader_rank ?? '--' }} · 强度 {{ formatPracticeNumber(item.stock_strong_score) }}</strong>
            </div>
            <div v-if="reversalStrategy" class="niuone-fact">
              <span>日线V型区间</span>
              <strong>{{ item.daily_v_left_days ?? '--' }}日回落 · {{ item.daily_v_right_days ?? '--' }}日修复</strong>
            </div>
            <div v-if="reversalStrategy" class="niuone-fact">
              <span>左侧跌幅 / 右侧反弹</span>
              <strong>{{ formatPracticeNumber(item.daily_v_decline_pct) }}% · {{ formatPracticeNumber(item.daily_v_rebound_pct) }}%</strong>
            </div>
            <div v-if="reversalStrategy" class="niuone-fact">
              <span>阶段低点 / 跌幅收复</span>
              <strong>{{ item.daily_v_trough_date || '--' }} · {{ formatPracticeNumber(Number(item.daily_v_recovery_ratio) * 100) }}%</strong>
            </div>
            <div class="niuone-fact">
              <span>主线内排名 / 龙头集中度</span>
              <strong>#{{ formatPracticeNumber(item.stock_sector_rank) }} · {{ formatPracticeNumber(Number(item.leader_concentration) * 100) }}%</strong>
            </div>
          </div>
          <p v-if="reversalStrategy || item.mainline_intraday_state === 'intraday_mainline'" class="niuone-observation-note">
            {{ reversalStrategy
              ? '牛牛试仓依据日线区间V型结构，右侧修复确认后轻仓参与；日内观察数据不作为该策略的必要条件。'
              : '日内强势仅作为题材研究观察，不会直接触发牛牛试仓。' }}
          </p>
        </section>

        <section class="niuone-detail-section">
          <h4>风控与执行</h4>
          <div class="niuone-fact-grid risk-grid">
            <div class="niuone-fact">
              <span>结构止损</span>
              <strong>{{ formatPracticeNumber(item.stop_price) }} · {{ formatPracticeNumber(item.stop_distance_pct) }}%</strong>
            </div>
            <div class="niuone-fact">
              <span>有效损失</span>
              <strong>{{ formatPracticeNumber(item.effective_loss_distance_pct) }}%</strong>
            </div>
            <div class="niuone-fact">
              <span>单笔风险预算</span>
              <strong>{{ formatPracticeNumber(item.per_trade_risk_budget_pct) }}%</strong>
            </div>
            <div class="niuone-fact">
              <span>动态仓位上限</span>
              <strong>{{ formatPracticeNumber(item.max_position_pct_by_risk) }}%</strong>
            </div>
          </div>
          <dl class="niuone-rules">
            <div v-if="scoreBasis"><dt>评分依据</dt><dd>{{ scoreBasis }}</dd></div>
            <div v-if="item.position_hint"><dt>仓位规则</dt><dd>{{ item.position_hint }}</dd></div>
            <div v-if="item.time_stop"><dt>退出规则</dt><dd>{{ item.time_stop }}</dd></div>
          </dl>
        </section>

        <section v-if="hardBlockers.length || riskFlags.length" class="niuone-detail-section niuone-conditions">
          <h4>未通过条件 <span>{{ hardBlockers.length + riskFlags.length }}</span></h4>
          <ul v-if="hardBlockers.length" class="niuone-condition-list blockers">
            <li v-for="flag in hardBlockers" :key="`hard-${flag}`">{{ flag }}</li>
          </ul>
          <ul v-if="riskFlags.length" class="niuone-condition-list risks">
            <li v-for="flag in riskFlags" :key="`risk-${flag}`">{{ flag }}</li>
          </ul>
        </section>
      </div>

      <div v-else style="display:flex;flex-wrap:wrap;gap:6px;color:var(--muted);font-size:12px">
        <template v-if="tideStrategy">
          <span>市场 {{ item.market_regime || '--' }} {{ formatPracticeNumber(item.market_score) }}</span>
          <span>行业潮位 {{ PRACTICE_TIDE_STATUS_LABELS[item.sector_status] || item.sector_status || '--' }} / {{ formatPracticeNumber(item.sector_score) }}</span>
          <span>板块内排名 {{ formatPracticeNumber(item.stock_sector_rank) }}</span>
          <span>结构止损 {{ formatPracticeNumber(item.stop_price) }} ({{ formatPracticeNumber(item.stop_distance_pct) }}%)</span>
          <span>跳空缓冲 {{ formatPracticeNumber(item.gap_buffer_pct) }}%</span>
          <span>有效损失 {{ formatPracticeNumber(item.effective_loss_distance_pct) }}%</span>
          <span>单笔预算 {{ formatPracticeNumber(item.per_trade_risk_budget_pct) }}%</span>
          <span>动态仓位上限 {{ formatPracticeNumber(item.max_position_pct_by_risk) }}%</span>
        </template>
        <template v-else>
          <span>BBI上行 {{ item.bbi_upward ? '✅' : '❌' }}</span>
          <span>站上BBI {{ item.above_bbi ? '✅' : '❌' }}</span>
          <span v-if="item.min_j_10d != null">J最低 {{ Number(item.min_j_10d).toFixed(1) }} {{ item.j_recovering ? '📈回升' : item.j_oversold ? '📉续降' : '--' }}</span>
          <span v-if="zettarancStrategy && item.industry_flow_matched" style="color:var(--green-text)">
            行业主力净流入第{{ item.industry_flow_rank }}名 · 评分+{{ Number(item.industry_flow_adjustment || 0).toFixed(2) }}
          </span>
        </template>
        <span v-if="scoreBasis">{{ scoreBasis }}</span>
        <span v-if="tradeDiscipline">{{ tradeDiscipline }}</span>
        <span v-for="flag in hardBlockers" :key="`hard-${flag}`" style="color:#fbbf24;font-size:11px;margin-left:6px">硬过滤:{{ flag }}</span>
        <span v-for="flag in riskFlags" :key="`risk-${flag}`" style="color:#f87171;font-size:11px;margin-left:6px">⚠️{{ flag }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.practice-candidate-card {
  background: var(--candidate-card-surface, var(--panel));
  border: 1px solid var(--candidate-card-border, var(--line));
  border-radius: 10px;
  box-shadow: var(--candidate-card-shadow, none);
  padding: 16px;
  transition: border-color .18s ease, box-shadow .18s ease;
}

.candidate-summary {
  align-items: flex-start;
  column-gap: 12px;
  display: grid;
  grid-template-areas: 'primary tier';
  grid-template-columns: minmax(0, 1fr) auto;
  margin-bottom: 10px;
  row-gap: 8px;
}

.candidate-summary.has-industry {
  grid-template-areas:
    'primary tier'
    'industry tier';
}

.niuone-candidate-card .candidate-summary {
  cursor: pointer;
}

.niuone-candidate-card .candidate-summary:focus-visible {
  outline: 2px solid var(--accent-border);
  outline-offset: 4px;
}

.niuone-candidate-card:not(.details-expanded) .candidate-summary {
  margin-bottom: 0;
}

.niuone-candidate-card:not(.details-expanded) .candidate-details {
  display: none;
}

.niuone-candidate-card.details-expanded {
  border-color: var(--candidate-card-expanded-border, var(--accent-border));
  box-shadow: var(--candidate-card-expanded-shadow, var(--candidate-card-shadow, none));
}

.niuone-candidate-card.details-expanded .candidate-summary {
  margin-bottom: 0;
  padding-bottom: 12px;
}

.niuone-candidate-card.details-expanded .candidate-details {
  border-top: 1px solid var(--candidate-card-divider, var(--candidate-card-border, var(--line)));
  padding-top: 12px;
}

.candidate-primary {
  grid-area: primary;
  min-width: 0;
}

.candidate-identity {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.candidate-stock-name {
  color: var(--text);
  font-size: 17px;
  font-weight: 780;
}

.candidate-strategy-badge {
  align-items: center;
  background: var(--candidate-strategy-bg);
  border: 1px solid;
  border-color: var(--candidate-strategy-border);
  border-radius: 999px;
  color: var(--candidate-strategy-text);
  display: inline-flex;
  font-size: 12px;
  padding: 2px 8px;
  white-space: nowrap;
}

.niuone-candidate-card .candidate-strategy-badge {
  background: var(--candidate-niuone-bg, var(--candidate-strategy-bg));
  border-color: var(--candidate-niuone-border, var(--candidate-strategy-border));
  color: var(--candidate-niuone-text, var(--candidate-strategy-text));
}

.candidate-industry {
  grid-area: industry;
  justify-self: start;
  min-width: 0;
}

.candidate-industry-badge {
  align-items: center;
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  border-radius: 6px;
  color: var(--accent-text);
  display: inline-flex;
  font-size: 12px;
  max-width: 100%;
  padding: 2px 8px;
  white-space: nowrap;
}

.candidate-tier {
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 6px;
  display: inline-flex;
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 600;
  grid-area: tier;
  justify-self: end;
  line-height: 1;
  padding: 6px 9px;
  white-space: nowrap;
}

.candidate-tier.high {
  background: var(--green-soft);
  border-color: var(--green-border);
  color: var(--green-text);
}

.candidate-tier.mid {
  background: var(--yellow-soft);
  border-color: var(--yellow-border);
  color: var(--yellow-text);
}

.candidate-tier.low {
  background: var(--candidate-card-subtle, var(--panel2));
  color: var(--muted);
}

.candidate-metric-grid {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-bottom: 10px;
}

.candidate-metric {
  background: var(--candidate-card-subtle, var(--panel2));
  border: 1px solid var(--candidate-card-border, var(--line));
  border-radius: 7px;
  min-width: 0;
  padding: 8px 10px;
}

.niuone-details {
  color: var(--text);
  font-size: 12px;
}

.niuone-detail-section {
  border-top: 1px solid var(--candidate-card-border, var(--line));
  padding-top: 12px;
}

.niuone-detail-section + .niuone-detail-section {
  margin-top: 13px;
}

.niuone-detail-section h4 {
  align-items: center;
  color: var(--muted);
  display: flex;
  font-size: 12px;
  font-weight: 650;
  gap: 6px;
  letter-spacing: .02em;
  margin: 0 0 10px;
}

.niuone-detail-section h4 span {
  color: var(--text);
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}

.niuone-fact-grid {
  display: grid;
  gap: 10px 22px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.niuone-fact {
  min-width: 0;
}

.niuone-fact > span {
  color: var(--muted);
  display: block;
  font-size: 11px;
  margin-bottom: 3px;
}

.niuone-fact > strong {
  color: var(--text);
  display: block;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  font-weight: 620;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.niuone-observation-note {
  border-left: 2px solid var(--accent-border);
  color: var(--muted);
  margin: 10px 0 0;
  padding: 3px 0 3px 9px;
}

.niuone-rules {
  background: var(--candidate-card-subtle, var(--panel2));
  border-radius: 7px;
  margin: 11px 0 0;
  padding: 8px 11px;
}

.niuone-rules > div {
  display: grid;
  gap: 10px;
  grid-template-columns: 58px minmax(0, 1fr);
  line-height: 1.55;
}

.niuone-rules > div + div {
  border-top: 1px solid var(--candidate-card-border, var(--line));
  margin-top: 6px;
  padding-top: 6px;
}

.niuone-rules dt {
  color: var(--muted);
}

.niuone-rules dd {
  color: var(--text);
  margin: 0;
}

.niuone-condition-list {
  display: grid;
  gap: 6px 18px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  list-style: none;
  margin: 0;
  padding: 0;
}

.niuone-condition-list + .niuone-condition-list {
  margin-top: 7px;
}

.niuone-condition-list li {
  line-height: 1.45;
  padding-left: 13px;
  position: relative;
}

.niuone-condition-list li::before {
  border-radius: 50%;
  content: '';
  height: 5px;
  left: 1px;
  position: absolute;
  top: .48em;
  width: 5px;
}

.niuone-condition-list.blockers li {
  color: var(--yellow-text);
}

.niuone-condition-list.blockers li::before {
  background: var(--yellow-text);
}

.niuone-condition-list.risks li {
  color: #f87171;
}

.niuone-condition-list.risks li::before {
  background: #f87171;
}

@media (max-width: 900px) {
  .candidate-metric-grid,
  .niuone-fact-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .practice-candidate-card {
    border-radius: 9px;
    padding: 12px;
  }

  .niuone-candidate-card .candidate-summary {
    column-gap: 8px;
    row-gap: 6px;
  }

  .niuone-candidate-card .candidate-summary.has-industry {
    grid-template-areas:
      'primary industry'
      'primary tier';
  }

  .niuone-candidate-card .candidate-identity {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }

  .niuone-candidate-card .candidate-stock-name {
    font-size: 15px;
    line-height: 1.35;
  }

  .niuone-candidate-card .candidate-strategy-badge,
  .niuone-candidate-card .candidate-industry-badge {
    font-size: 11px;
    padding: 1px 6px;
  }

  .niuone-candidate-card .candidate-tier {
    font-size: 10px;
    padding: 4px 7px;
  }

  .niuone-candidate-card .candidate-industry,
  .niuone-candidate-card .candidate-tier {
    justify-self: end;
  }

  .niuone-candidate-card.details-expanded .candidate-summary {
    padding-bottom: 10px;
  }

  .niuone-candidate-card.details-expanded .candidate-details {
    padding-top: 10px;
  }

  .niuone-fact-grid {
    gap: 10px 14px;
  }

  .niuone-condition-list {
    grid-template-columns: 1fr;
  }

  .niuone-rules > div {
    gap: 3px;
    grid-template-columns: 1fr;
  }
}
</style>
