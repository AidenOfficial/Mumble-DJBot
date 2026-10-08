<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { formatBytes, type PrepStatus } from '../api'

// 当前曲还没出声时显示:在等什么(阶段)、已经等了多久、还要多久开始播放
const props = defineProps<{ prep: PrepStatus; syncedAt: number }>()

// 两次轮询之间本地推算:已等待秒数往上走,预计剩余往下走
const now = ref(performance.now())
let raf = 0
function frame() {
  now.value = performance.now()
  raf = requestAnimationFrame(frame)
}
onMounted(() => (raf = requestAnimationFrame(frame)))
onBeforeUnmount(() => cancelAnimationFrame(raf))
const sinceSync = computed(() => Math.max(0, (now.value - props.syncedAt) / 1000))

const elapsed = computed(() => props.prep.elapsed + sinceSync.value)
const eta = computed(() => (props.prep.eta === null ? null : Math.max(0, props.prep.eta - sinceSync.value)))

const STEPS = [
  { key: 'info', label: 'Fetching info', hint: 'Asking the site for the audio stream' },
  { key: 'connect', label: 'Connecting', hint: 'Opening the download' },
  { key: 'buffer', label: 'Buffering', hint: 'Downloading enough audio to start' },
  { key: 'play', label: 'Playing', hint: '' },
] as const
const stepIndex = computed(() => {
  switch (props.prep.stage) {
    case 'pending':
    case 'fetching_info': return 0
    case 'starting': return 1
    case 'downloading': return 2
    default: return 3
  }
})

// 缓冲进度:边下边播时只要攒够 target_secs 就开播,否则要等整个文件
const bufferPct = computed(() => {
  const p = props.prep
  if (p.stage !== 'downloading') return 0
  if (p.streaming && p.target_secs > 0) return Math.min(100, (p.buffered_secs / p.target_secs) * 100)
  return Math.min(100, p.progress * 100)
})

function secs(n: number) {
  if (n < 10) return `${n.toFixed(1)}s`
  if (n < 90) return `${Math.round(n)}s`
  return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`
}

const etaText = computed(() => {
  if (props.prep.stage === 'launching' || props.prep.stage === 'ready') return 'Starting now…'
  if (eta.value === null) return 'Estimating…'
  if (eta.value < 0.5) return 'Any moment now…'
  return `Starts in ${props.prep.eta_estimated ? '~' : ''}${secs(eta.value)}`
})
</script>

<template>
  <div class="w-full max-w-xl rounded-2xl p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }"
       role="status" aria-live="polite">
    <div class="flex items-baseline justify-between gap-2">
      <p class="text-sm font-semibold" :style="{ color: 'var(--c-accent)' }">{{ etaText }}</p>
      <p class="text-xs tabular-nums" :style="{ color: 'var(--c-text-muted)' }">waiting {{ secs(elapsed) }}</p>
    </div>

    <!-- 阶段 -->
    <ol class="mt-3 grid grid-cols-4 gap-1.5">
      <li v-for="(step, i) in STEPS" :key="step.key" class="min-w-0">
        <div class="h-1.5 overflow-hidden rounded-full" :style="{ background: 'var(--c-surface-2)' }">
          <div
            class="h-full rounded-full"
            :class="i === stepIndex && i < 3 ? 'animate-pulse' : ''"
            :style="{
              width: i < stepIndex ? '100%' : i === stepIndex ? (i === 2 ? `${Math.max(bufferPct, 6)}%` : '100%') : '0%',
              background: i < stepIndex ? 'var(--c-accent)' : 'var(--c-accent)',
              opacity: i < stepIndex ? 0.55 : 1,
              transition: 'width 400ms ease',
            }"
          />
        </div>
        <p class="mt-1 truncate text-[11px]"
           :style="{ color: i === stepIndex ? 'var(--c-text)' : 'var(--c-text-faint)', fontWeight: i === stepIndex ? 600 : 400 }">
          {{ i < stepIndex ? '✓ ' : '' }}{{ step.label }}
        </p>
      </li>
    </ol>
    <p class="mt-1 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
      {{ STEPS[stepIndex]?.hint }}<template v-if="prep.stage_elapsed > 0 && stepIndex < 3"> · {{ secs(prep.stage_elapsed + sinceSync) }}</template>
    </p>

    <!-- 下载细节 -->
    <div v-if="prep.stage === 'downloading'" class="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs tabular-nums"
         :style="{ color: 'var(--c-text-muted)' }">
      <span v-if="prep.streaming">buffered {{ Math.floor(prep.buffered_secs) }}s / {{ Math.ceil(prep.target_secs) }}s needed</span>
      <span v-else>{{ Math.round(prep.progress * 100) }}% downloaded</span>
      <span v-if="prep.speed">{{ formatBytes(prep.speed) }}/s</span>
      <span v-if="prep.total">{{ formatBytes(prep.downloaded) }} / {{ formatBytes(prep.total) }}</span>
    </div>
    <p v-if="prep.streaming && stepIndex >= 1" class="mt-2 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
      Long video: playback starts as soon as {{ Math.ceil(prep.target_secs) }}s of audio is ready; the rest keeps downloading in the background.
    </p>
    <p v-else-if="!prep.streaming && prep.stage === 'downloading'" class="mt-2 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
      This one plays once the whole file is downloaded.
    </p>
  </div>
</template>
