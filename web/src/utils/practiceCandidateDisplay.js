export const PRACTICE_STRATEGY_META = {
  trend_pullback: { label: '趋势回踩', color: '#60a5fa' },
  breakout: { label: '突破确认', color: '#ec4899' },
  shaofu_b1: { label: '少妇B1', color: '#f97316' },
  b2_confirm: { label: 'B2确认', color: '#22c55e' },
  b3_accelerate: { label: 'B3中继', color: '#a78bfa' },
  super_b1: { label: '超级B1', color: '#fb7185' },
  tide_leader: { label: '主线领航', color: '#06b6d4' },
  tide_rotation: { label: '轮动初升', color: '#14b8a6' },
  tide_recovery: { label: '冰点修复', color: '#22d3ee' },
  niu_leader: { label: '牛牛战法 · 领涨', color: '#8b5cf6' },
  niu_pullback: { label: '牛牛战法 · 转强', color: '#a78bfa' },
  niu_emerging: { label: '牛牛战法 · 启动', color: '#c084fc' },
  niu_reversal_probe: { label: '牛牛战法 · 试仓', color: '#f59e0b' },
}

export const PRACTICE_STOCK_BOARD_LABELS = {
  main_board: '主板',
  chi_next: '创业板',
  star_market: '科创板',
  st: 'ST',
}

export const PRACTICE_TIDE_STATUS_LABELS = {
  leading: '领先',
  improving: '改善',
  weakening: '转弱',
  lagging: '落后',
  candidate: '候选',
  emerging: '启动',
  intraday_mainline: '日内强势观察',
  reversal_probe: '日线V型试仓',
  mainline: '主线',
  diverging: '分歧',
  fading: '退潮',
  inactive: '失效',
}

export const PRACTICE_NIUONE_LIFECYCLE_LABELS = {
  brewing: '主线酝酿',
  markup: '主线主升',
  climax: '主线高潮',
  divergence: '主线分歧',
  fade: '主线退幕',
}

export function practiceNiuoneLifecycleLabel(item = {}) {
  const explicit = String(item.niuone_lifecycle_label || '').trim()
  if (explicit) return explicit
  const stage = String(item.niuone_lifecycle_stage || '').trim()
  if (PRACTICE_NIUONE_LIFECYCLE_LABELS[stage]) {
    return PRACTICE_NIUONE_LIFECYCLE_LABELS[stage]
  }
  const state = String(item.mainline_state || item.sector_status || '').trim()
  if (state === 'candidate') return PRACTICE_NIUONE_LIFECYCLE_LABELS.brewing
  if (state === 'emerging') {
    return item.mainline_cross_day_persistent
      ? PRACTICE_NIUONE_LIFECYCLE_LABELS.markup
      : PRACTICE_NIUONE_LIFECYCLE_LABELS.brewing
  }
  if (state === 'mainline') {
    return item.mainline_confirmed && Number(item.mainline_score || 0) >= 78
      ? PRACTICE_NIUONE_LIFECYCLE_LABELS.climax
      : PRACTICE_NIUONE_LIFECYCLE_LABELS.markup
  }
  if (state === 'diverging') return PRACTICE_NIUONE_LIFECYCLE_LABELS.divergence
  if (state === 'fading') return PRACTICE_NIUONE_LIFECYCLE_LABELS.fade
  return '--'
}

export function formatPracticeNumber(value, digits = 2) {
  const number = Number(value)
  return Number.isFinite(number)
    ? Number(number.toFixed(digits)).toLocaleString('en')
    : '--'
}

export function practiceCandidateTier(item) {
  const score = Number(item?.best_score ?? item?.score ?? 0)
  const threshold = Number(item?.entry_threshold ?? 8)
  const hardBlockers = Array.isArray(item?.hard_blockers) ? item.hard_blockers : []
  if (item?.actionable && !hardBlockers.length && score >= threshold) return 'high'
  return score >= threshold - 1.5 ? 'mid' : 'low'
}

export function practiceCandidateTierCounts(items) {
  const counts = { high: 0, mid: 0, low: 0 }
  for (const item of Array.isArray(items) ? items : []) counts[practiceCandidateTier(item)] += 1
  return counts
}

export function practiceCandidateScanDescription(strategySuite, stockUniverseLabel) {
  const universeLabel = String(stockUniverseLabel || '').trim() || '配置范围'
  return String(strategySuite || '').trim() === 'niuone'
    ? `全市场非ST主线识别 · ${universeLabel}入选`
    : `高流动性扫描 · ${universeLabel}入选`
}

export function practiceCandidateStrategyMeta(payloadMeta = {}) {
  const merged = { ...PRACTICE_STRATEGY_META, ...(payloadMeta || {}) }
  for (const strategyId of ['niu_leader', 'niu_pullback', 'niu_emerging', 'niu_reversal_probe']) {
    merged[strategyId] = {
      ...(merged[strategyId] || {}),
      label: PRACTICE_STRATEGY_META[strategyId].label,
    }
  }
  return merged
}

export function practiceCandidateIndustryLabel(item = {}) {
  const label = item.industry || item.sector || item.board_label || item.board || ''
  return PRACTICE_STOCK_BOARD_LABELS[label] || label
}
