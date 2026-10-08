<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { formatBytes, type PrepStatus } from '../api'
import { t } from '../i18n'

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
  { key: 'info', label: 'prep.info', hint: 'prep.infoHint' },
  { key: 'connect', label: 'prep.connect', hint: 'prep.connectHint' },
  { key: 'buffer', label: 'prep.buffer', hint: 'prep.bufferHint' },
  { key: 'play', label: 'prep.play', hint: null },
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
  if (n < 10) return t('prep.secs', { n: n.toFixed(1) })
  if (n < 90) return t('prep.secs', { n: Math.round(n) })
  return t('prep.minSecs', { m: Math.floor(n / 60), s: Math.round(n % 60) })
}

const etaText = computed(() => {
  if (props.prep.stage === 'launching' || props.prep.stage === 'ready') return t('prep.startingNow')
  if (eta.value === null) return t('prep.estimating')
  if (eta.value < 0.5) return t('prep.anyMoment')
  return t('prep.startsIn', { eta: `${props.prep.eta_estimated ? '~' : ''}${secs(eta.value)}` })
})
</script>

<template>
  <div class="w-full max-w-xl rounded-2xl p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }"
       role="status" aria-live="polite">
    <div class="flex items-baseline justify-between gap-2">
      <p class="text-sm font-semibold" :style="{ color: 'var(--c-accent)' }">{{ etaText }}</p>
      <p class="text-xs tabular-nums" :style="{ color: 'var(--c-text-muted)' }">{{ t('prep.waiting', { t: secs(elapsed) }) }}</p>
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
          {{ i < stepIndex ? '✓ ' : '' }}{{ t(step.label) }}
        </p>
      </li>
    </ol>
    <p class="mt-1 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
      {{ STEPS[stepIndex]?.hint ? t(STEPS[stepIndex]!.hint!) : '' }}<template v-if="prep.stage_elapsed > 0 && stepIndex < 3"> · {{ secs(prep.stage_elapsed + sinceSync) }}</template>
    </p>

    <!-- 下载细节 -->
    <div v-if="prep.stage === 'downloading'" class="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs tabular-nums"
         :style="{ color: 'var(--c-text-muted)' }">
      <span v-if="prep.streaming">{{ t('prep.buffered', { have: Math.floor(prep.buffered_secs), need: Math.ceil(prep.target_secs) }) }}</span>
      <span v-else>{{ t('prep.downloaded', { pct: Math.round(prep.progress * 100) }) }}</span>
      <span v-if="prep.speed">{{ formatBytes(prep.speed) }}/s</span>
      <span v-if="prep.total">{{ formatBytes(prep.downloaded) }} / {{ formatBytes(prep.total) }}</span>
    </div>
    <p v-if="prep.streaming && stepIndex >= 1" class="mt-2 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
      {{ t('prep.streamingHint', { n: Math.ceil(prep.target_secs) }) }}
    </p>
    <p v-else-if="!prep.streaming && prep.stage === 'downloading'" class="mt-2 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
      {{ t('prep.wholeFileHint') }}
    </p>
  </div>
</template>
