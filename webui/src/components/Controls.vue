<script setup lang="ts">
import { ref, watch } from 'vue'
import { useStatus } from '../composables/useStatus'
import { modeLabel, t } from '../i18n'

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

const MODES = ['one-shot', 'repeat', 'single', 'random', 'autoplay']
</script>

<template>
  <div class="flex w-full max-w-xl flex-col items-center gap-5">
    <!-- transport -->
    <div class="flex items-center gap-4">
      <button
        class="flex h-14 w-14 cursor-pointer items-center justify-center rounded-full border-0"
        :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)', boxShadow: 'var(--shadow-1)' }"
        :title="status?.play ? t('ctl.pause') : t('ctl.play')"
        @click="togglePlay"
      >
        <Icon :icon="status?.play ? 'pause' : 'play'" class="text-xl" :class="{ 'translate-x-px': !status?.play }" />
      </button>
      <button
        class="flex h-11 w-11 cursor-pointer items-center justify-center rounded-full border-0"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
        :title="t('ctl.skip')"
        @click="control({ action: 'skip' })"
      >
        <Icon icon="forward-step" class="text-lg" />
      </button>
    </div>

    <!-- volume -->
    <div class="flex w-full max-w-xs items-center gap-3">
      <Icon icon="volume-low" class="shrink-0 text-sm" :style="{ color: 'var(--c-text-muted)' }" />
      <input
        v-model.number="volume"
        type="range"
        min="0"
        max="1"
        step="0.01"
        class="volume-slider w-full"
        :aria-label="t('ctl.volume')"
        @input="onVolumeInput"
      />
      <span class="w-8 text-right text-xs tabular-nums" :style="{ color: 'var(--c-text-muted)' }">
        {{ Math.round(volume * 100) }}
      </span>
      <span
        v-if="status?.ducking"
        class="shrink-0 rounded-full px-2 py-0.5 text-xs whitespace-nowrap"
        :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }"
        :title="t('ctl.duckHint')"
      >{{ t('ctl.duck') }}</span>
    </div>

    <!-- mode -->
    <div
      class="flex max-w-full gap-1 overflow-x-auto rounded-full p-1"
      :style="{ background: 'var(--c-surface-2)' }"
      role="radiogroup"
      :aria-label="t('ctl.mode')"
    >
      <button
        v-for="m in MODES"
        :key="m"
        class="shrink-0 cursor-pointer rounded-full border-0 px-2 py-1 text-xs font-medium whitespace-nowrap capitalize sm:px-3"
        :style="status?.mode === m
          ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' }
          : { background: 'transparent', color: 'var(--c-text-muted)' }"
        role="radio"
        :aria-checked="status?.mode === m"
        @click="control({ action: 'mode', mode: m })"
      >{{ modeLabel(m) }}</button>
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
