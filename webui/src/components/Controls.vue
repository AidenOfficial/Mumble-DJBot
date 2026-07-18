<script setup lang="ts">
import { ref, watch } from 'vue'
import { useStatus } from '../composables/useStatus'

const { status, control } = useStatus()

const volume = ref(0.5)
let volumeDragging = false
watch(
  () => status.value?.volume,
  (v) => {
    if (v !== undefined && !volumeDragging) volume.value = v
  },
  { immediate: true },
)

let volumeTimer: ReturnType<typeof setTimeout> | undefined
function onVolumeInput() {
  volumeDragging = true
  clearTimeout(volumeTimer)
  volumeTimer = setTimeout(() => {
    control({ action: 'volume', volume: volume.value })
    volumeDragging = false
  }, 150)
}

function togglePlay() {
  if (!status.value) return
  control({ action: status.value.play ? 'pause' : 'resume' })
}

const MODES = ['one-shot', 'repeat', 'random', 'autoplay']
</script>

<template>
  <div class="flex w-full max-w-xl flex-col items-center gap-5">
    <!-- transport -->
    <div class="flex items-center gap-4">
      <button
        class="flex h-14 w-14 cursor-pointer items-center justify-center rounded-full border-0"
        :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)', boxShadow: 'var(--shadow-1)' }"
        :title="status?.play ? 'Pause' : 'Play'"
        @click="togglePlay"
      >
        <svg v-if="status?.play" viewBox="0 0 24 24" class="h-6 w-6" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z" /></svg>
        <svg v-else viewBox="0 0 24 24" class="h-6 w-6" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
      </button>
      <button
        class="flex h-11 w-11 cursor-pointer items-center justify-center rounded-full border-0"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
        title="Skip"
        @click="control({ action: 'skip' })"
      >
        <svg viewBox="0 0 24 24" class="h-5 w-5" fill="currentColor"><path d="M6 6l8.5 6L6 18V6zM16 6h2v12h-2z" /></svg>
      </button>
    </div>

    <!-- volume -->
    <div class="flex w-full max-w-xs items-center gap-3">
      <svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0" fill="currentColor" :style="{ color: 'var(--c-text-muted)' }">
        <path d="M3 10v4h4l5 5V5L7 10H3z" />
      </svg>
      <input
        v-model.number="volume"
        type="range"
        min="0"
        max="1"
        step="0.01"
        class="volume-slider w-full"
        aria-label="Volume"
        @input="onVolumeInput"
      />
      <span class="w-8 text-right text-xs tabular-nums" :style="{ color: 'var(--c-text-muted)' }">
        {{ Math.round(volume * 100) }}
      </span>
      <span
        v-if="status?.ducking"
        class="rounded-full px-2 py-0.5 text-xs"
        :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }"
        title="Volume lowered while someone is talking"
      >duck</span>
    </div>

    <!-- mode -->
    <div
      class="flex gap-1 rounded-full p-1"
      :style="{ background: 'var(--c-surface-2)' }"
      role="radiogroup"
      aria-label="Playback mode"
    >
      <button
        v-for="m in MODES"
        :key="m"
        class="cursor-pointer rounded-full border-0 px-3 py-1 text-xs font-medium capitalize"
        :style="status?.mode === m
          ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' }
          : { background: 'transparent', color: 'var(--c-text-muted)' }"
        role="radio"
        :aria-checked="status?.mode === m"
        @click="control({ action: 'mode', mode: m })"
      >{{ m }}</button>
    </div>
  </div>
</template>

<style scoped>
.volume-slider {
  appearance: none;
  height: 4px;
  border-radius: 2px;
  background: var(--c-surface-2);
  outline: none;
}
.volume-slider::-webkit-slider-thumb {
  appearance: none;
  height: 14px;
  width: 14px;
  border-radius: 50%;
  background: var(--c-accent);
  cursor: pointer;
  border: none;
}
.volume-slider::-moz-range-thumb {
  height: 14px;
  width: 14px;
  border-radius: 50%;
  background: var(--c-accent);
  cursor: pointer;
  border: none;
}
</style>
