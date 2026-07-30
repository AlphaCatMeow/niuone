<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { formatVersionLabel } from '../utils/versionStatus.js'

const props = defineProps({
  about: {
    type: Object,
    default: () => ({}),
  },
})

const VERSION_REQUEST_TIMEOUT_MS = 15 * 1000
const checkState = ref('loading')
const latestVersion = ref('')
let requestController = null

const currentVersion = computed(() => formatVersionLabel(props.about.current_version))
const latestVersionLabel = computed(() => {
  if (checkState.value === 'loading') return '查询中…'
  if (checkState.value === 'error') return '查询失败'
  return latestVersion.value || '暂无发行版本'
})

async function loadLatestVersion(forceRefresh = false) {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), VERSION_REQUEST_TIMEOUT_MS)
  requestController = controller
  checkState.value = 'loading'
  try {
    const endpoint = forceRefresh ? '/api/version?refresh=1' : '/api/version'
    const response = await fetch(endpoint, {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const payload = await response.json()
    if (payload.check_ok !== true) throw new Error('version_check_failed')
    latestVersion.value = String(payload.latest_version || '').trim()
    checkState.value = 'ready'
  } catch (error) {
    if (error.name === 'AbortError') return
    checkState.value = 'error'
  } finally {
    window.clearTimeout(timeout)
    if (requestController === controller) requestController = null
  }
}

onMounted(() => loadLatestVersion(false))
onBeforeUnmount(() => requestController?.abort())
</script>

<template>
  <section class="about-project" aria-label="项目信息">
    <dl class="about-project-grid">
      <div class="about-project-item">
        <dt>作者</dt>
        <dd><a :href="about.author_url" target="_blank" rel="noopener noreferrer">{{ about.author || 'kunkundi' }}</a></dd>
      </div>
      <div class="about-project-item">
        <dt>代码仓库</dt>
        <dd><a :href="about.repository_url" target="_blank" rel="noopener noreferrer">{{ about.repository || 'kunkundi/niuone' }}</a></dd>
      </div>
      <div class="about-project-item">
        <dt>开源许可</dt>
        <dd><a :href="about.license_url" target="_blank" rel="noopener noreferrer">{{ about.license || 'Apache License 2.0' }}</a></dd>
      </div>
      <div class="about-project-item">
        <dt>当前版本</dt>
        <dd>{{ currentVersion }}</dd>
      </div>
      <div class="about-project-item">
        <dt>最新版本</dt>
        <dd class="about-version-control">
          <span :class="{'about-version-error': checkState === 'error'}">{{ latestVersionLabel }}</span>
          <button
            type="button"
            :disabled="checkState === 'loading'"
            @click="loadLatestVersion(true)"
          >{{ checkState === 'loading' ? '查询中…' : '检查更新' }}</button>
        </dd>
      </div>
    </dl>
  </section>
</template>
