export function formatVersionLabel(value) {
  const version = String(value || '').trim()
  if (!version) return '--'
  if (['dev', 'local'].includes(version.toLowerCase())) return '开发版'
  return version
}

export function availableVersionFromPayload(payload) {
  if (payload?.check_ok !== true || payload?.update_available !== true) return ''
  return String(payload.latest_version || '').trim()
}

export function shouldShowVersionReminder(version, ignoredVersion, manual = false) {
  const available = String(version || '').trim()
  if (!available) return false
  return manual || available !== String(ignoredVersion || '').trim()
}
