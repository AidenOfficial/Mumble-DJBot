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

function applyStatus(s: BotStatus) {
  status.value = s
  lastSync = performance.now()
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
  if (s && s.play && !s.empty) {
    const elapsed = (performance.now() - lastSync) / 1000
    const duration = s.current?.duration || 0
    const pos = s.playhead + elapsed
    clock.playhead = duration > 0 ? Math.min(pos, duration) : pos
  }
  requestAnimationFrame(tick)
}

function ensureStarted() {
  if (started) return
  started = true
  poll()
  setInterval(poll, POLL_MS)
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
  return { status, error, clock, progress, refresh: poll, control, applyStatus }
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
