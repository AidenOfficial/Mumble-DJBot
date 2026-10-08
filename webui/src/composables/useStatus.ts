import { computed, reactive, ref } from 'vue'
import { fetchStatus, postControls, type BotStatus, type ControlAction } from '../api'

const POLL_MS = 3000

// Module-level singleton: every component shares the same status stream and
// the same smoothly advancing playhead clock.
const status = ref<BotStatus | null>(null)
const error = ref<string | null>(null)
const clock = reactive({ playhead: 0 })

let started = false
let lastSync = 0 // performance.now() at the time of the last poll
const syncedAt = ref(0) // 同上,给需要本地推算的组件(准备面板)用

function applyStatus(s: BotStatus) {
  status.value = s
  lastSync = performance.now()
  syncedAt.value = lastSync
  clock.playhead = s.playhead
  error.value = null
}

async function poll() {
  try {
    applyStatus(await fetchStatus())
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

function tick() {
  const s = status.value
  if (s && s.play && !s.empty && !s.prep) {  // 还在准备(没出声)时进度不走
    const elapsed = (performance.now() - lastSync) / 1000
    const duration = s.current?.duration || 0
    const pos = s.playhead + elapsed
    clock.playhead = duration > 0 ? Math.min(pos, duration) : pos
  }
  requestAnimationFrame(tick)
}

// 等待开播 / 后台下载时每秒刷新,平时 3 秒一次
function nextDelay() {
  const s = status.value
  return s?.prep || s?.current?.download ? 1000 : POLL_MS
}

async function pollLoop() {
  await poll()
  setTimeout(pollLoop, nextDelay())
}

function ensureStarted() {
  if (started) return
  started = true
  pollLoop()
  requestAnimationFrame(tick)
}

/** Fire a control action; the response is a fresh status, applied at once. */
async function control(body: ControlAction) {
  try {
    applyStatus(await postControls(body))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

export function useStatus() {
  ensureStarted()
  const progress = computed(() => {
    const duration = status.value?.current?.duration || 0
    if (!duration) return 0
    return Math.min(1, clock.playhead / duration)
  })
  return { status, error, clock, progress, syncedAt, refresh: poll, control, applyStatus }
}

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  const ss = String(sec).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}
