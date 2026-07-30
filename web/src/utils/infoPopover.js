const INFO_POPOVER_HOVER_QUERY = '(hover: hover) and (pointer: fine)'

export function infoPopoverUsesHover() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(INFO_POPOVER_HOVER_QUERY).matches
}

export function allowInfoPopoverClick(event) {
  if (!infoPopoverUsesHover() || event?.detail === 0) return true
  event?.currentTarget?.blur?.()
  return false
}
