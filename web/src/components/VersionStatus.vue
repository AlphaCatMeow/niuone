<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useDashboardTabs } from '../composables/useDashboardTabs.js'
import {
  availableVersionFromPayload,
  formatVersionLabel,
  shouldShowVersionReminder,
} from '../utils/versionStatus.js'

const VERSION_REQUEST_TIMEOUT_MS = 15 * 1000
const DOCKER_HUB_URL = 'https://hub.docker.com/r/kunkundi/niuone'
const IGNORED_UPDATE_STORAGE_KEY = 'niuone:ignored-update-version'

const { autoVersionCheckEnabled, currentVersion, initializeDashboardTabs } = useDashboardTabs()
const state = ref('idle')
const title = ref('点击检查新版本')
const availableVersion = ref('')
const pendingVersion = ref('')
const value = computed(() => formatVersionLabel(currentVersion.value))
let requestController = null

function closeUpdateDialog() {
  availableVersion.value = ''
}

function ignoredUpdateVersion() {
  try {
    return String(window.localStorage.getItem(IGNORED_UPDATE_STORAGE_KEY) || '').trim()
  } catch (error) {
    console.warn('Ignored update preference is unavailable', error)
    return ''
  }
}

function ignoreAvailableVersion() {
  const version = availableVersion.value
  if (!version) return
  try {
    window.localStorage.setItem(IGNORED_UPDATE_STORAGE_KEY, version)
  } catch (error) {
    console.warn('Ignored update preference could not be saved', error)
  }
  closeUpdateDialog()
  state.value = 'idle'
  title.value = `当前版本 ${value.value}；点击检查新版本`
}

function showAvailableVersion(version) {
  if (document.body.classList.contains('compliance-dialog-open')) {
    pendingVersion.value = version
    return
  }
  availableVersion.value = version
}

function handleComplianceClosed() {
  if (!pendingVersion.value) return
  availableVersion.value = pendingVersion.value
  pendingVersion.value = ''
}

function handleKeydown(event) {
  if (event.key === 'Escape') closeUpdateDialog()
}

async function checkForUpdates(manual = false) {
  if (state.value === 'checking') return
  closeUpdateDialog()
  pendingVersion.value = ''
  state.value = 'checking'
  title.value = '正在检查新版本'
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), VERSION_REQUEST_TIMEOUT_MS)
  requestController = controller
  try {
    const response = await fetch('/api/version', {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const payload = await response.json()
    currentVersion.value = String(payload.current_version || currentVersion.value).trim()
    const currentLabel = formatVersionLabel(currentVersion.value)
    const updateVersion = availableVersionFromPayload(payload)
    if (payload.check_ok !== true) {
      state.value = 'error'
      title.value = `当前版本 ${currentLabel}；版本检查失败，请稍后重试`
    } else if (updateVersion) {
      if (!shouldShowVersionReminder(updateVersion, ignoredUpdateVersion(), manual)) {
        state.value = 'idle'
        title.value = `当前版本 ${currentLabel}；点击检查新版本`
        return
      }
      state.value = 'update'
      title.value = `发现新版本 ${updateVersion}`
      showAvailableVersion(updateVersion)
    } else if (payload.update_available === false) {
      state.value = 'current'
      title.value = `版本检查完成；当前版本 ${currentLabel}`
    } else {
      state.value = 'idle'
      title.value = `当前版本 ${currentLabel}；无法判断是否存在新版本`
    }
  } catch (error) {
    state.value = 'error'
    title.value = error.name === 'AbortError'
      ? '版本检查超时，请稍后重试'
      : '版本检查失败，请稍后重试'
    if (error.name !== 'AbortError') console.error('Version check failed', error)
  } finally {
    window.clearTimeout(timeout)
    if (requestController === controller) requestController = null
  }
}

onMounted(async () => {
  document.addEventListener('keydown', handleKeydown)
  window.addEventListener('niuone:compliance-closed', handleComplianceClosed)
  await initializeDashboardTabs()
  if (autoVersionCheckEnabled.value) await checkForUpdates(false)
})
onBeforeUnmount(() => {
  requestController?.abort()
  document.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('niuone:compliance-closed', handleComplianceClosed)
})
</script>

<template>
  <button
    id="versionStatus"
    type="button"
    class="version-status"
    :data-state="state"
    :title="title"
    :aria-label="title"
    aria-haspopup="dialog"
    :aria-expanded="Boolean(availableVersion)"
    :disabled="state === 'checking'"
    @click="checkForUpdates(true)"
  >
    <b id="versionValue">{{ value }}</b>
  </button>
  <div
    v-if="availableVersion"
    class="version-update-backdrop"
    role="presentation"
    @click.self="closeUpdateDialog"
  >
    <section
      class="version-update-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="versionUpdateTitle"
      aria-describedby="versionUpdateDescription"
    >
      <h2 id="versionUpdateTitle">发现新版本</h2>
      <p id="versionUpdateDescription">
        当前版本 {{ value }}，可升级到 {{ availableVersion }}。
      </p>
      <div class="version-update-actions">
        <button type="button" class="version-update-later" @click="closeUpdateDialog">稍后</button>
        <button type="button" class="version-update-ignore" @click="ignoreAvailableVersion">此版本不再提醒</button>
        <a :href="DOCKER_HUB_URL" target="_blank" rel="noopener noreferrer" @click="closeUpdateDialog">查看版本</a>
      </div>
    </section>
  </div>
</template>
