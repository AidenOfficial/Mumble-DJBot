import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { fetchStatus, type BotStatus } from '../api'

const POLL_MS = 3000

/**
 * Polls /api/status and exposes a smoothly advancing playhead: between
 * polls the position is extrapolated from the last server value with
 * requestAnimationFrame, then snapped on the next poll.
 */
export function useStatus() {
  const status = ref<BotStatus | null>(null)
  const error = ref<string | null>(null)
  const clock = reactive({ playhead: 0 })

  let pollTimer: ReturnType<typeof setInterval> | undefined
  let raf = 0
  let lastSync = 0 // performance.now() at the time of the last poll

  const tick = () => {
    const s = status.value
    if (s && s.play && !s.empty) {
      const elapsed = (performance.now() - lastSync) / 1000
      const duration = s.current?.duration || 0
      const pos = s.playhead + elapsed
      clock.playhead = duration > 0 ? Math.min(pos, duration) : pos
    }
    raf = requestAnimationFrame(tick)
  }

  const poll = async () => {
    try {
      const s = await fetchStatus()
      status.value = s
      lastSync = performance.now()
      clock.playhead = s.playhead
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  onMounted(() => {
    poll()
    pollTimer = setInterval(poll, POLL_MS)
    raf = requestAnimationFrame(tick)
  })

  onBeforeUnmount(() => {
    if (pollTimer) clearInterval(pollTimer)
    cancelAnimationFrame(raf)
  })

  const progress = computed(() => {
    const duration = status.value?.current?.duration || 0
    if (!duration) return 0
    return Math.min(1, clock.playhead / duration)
  })

  return { status, error, clock, progress, refresh: poll }
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
