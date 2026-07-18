<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { thumbnailUrl } from '../api'
import Controls from './Controls.vue'
import { formatTime, useStatus } from '../composables/useStatus'

const { status, error, clock, progress } = useStatus()

const current = computed(() => status.value?.current ?? null)

// Only swap the artwork when the song actually changes, so the <img> does
// not flicker on every poll.
const artSrc = ref<string | null>(null)
watch(
  () => [current.value?.id, current.value?.has_thumbnail] as const,
  ([id, hasThumb]) => {
    artSrc.value = id && hasThumb ? thumbnailUrl(id) : null
  },
  { immediate: true },
)

const sourceLabel = computed(() => {
  const t = current.value?.type ?? ''
  const labels: Record<string, string> = {
    url: 'Stream',
    file: 'Library',
    radio: 'Radio',
    playlist: 'Playlist',
  }
  return labels[t] ?? t
})
</script>

<template>
  <section class="mx-auto flex w-full max-w-3xl flex-col items-center gap-6 px-4 py-8 sm:py-14">
    <!-- artwork -->
    <div
      class="relative aspect-square w-56 overflow-hidden sm:w-72"
      :style="{ borderRadius: 'var(--radius-l)', boxShadow: 'var(--shadow-2)' }"
    >
      <img
        v-if="artSrc"
        :src="artSrc"
        alt=""
        class="h-full w-full object-cover"
      />
      <div
        v-else
        class="flex h-full w-full items-center justify-center"
        :style="{ background: 'linear-gradient(135deg, var(--c-accent-soft), var(--c-surface-2))' }"
      >
        <svg viewBox="0 0 24 24" class="h-20 w-20 opacity-40" fill="currentColor" aria-hidden="true">
          <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" />
        </svg>
      </div>
    </div>

    <!-- title / source -->
    <div class="flex w-full flex-col items-center gap-1 text-center">
      <template v-if="current">
        <h1 class="max-w-full truncate text-xl font-semibold sm:text-2xl" :title="current.title">
          {{ current.title || 'Untitled' }}
        </h1>
        <p class="flex items-center gap-2 text-sm" :style="{ color: 'var(--c-text-muted)' }">
          <span
            class="rounded-full px-2 py-0.5 text-xs font-medium"
            :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }"
          >{{ sourceLabel }}</span>
          <span v-if="current.artist && current.artist !== '??'">{{ current.artist }}</span>
        </p>
      </template>
      <template v-else>
        <h1 class="text-xl font-semibold sm:text-2xl" :style="{ color: 'var(--c-text-muted)' }">
          Nothing playing
        </h1>
        <p class="text-sm" :style="{ color: 'var(--c-text-faint)' }">
          The queue is empty - add something from Mumble or the search page.
        </p>
      </template>
    </div>

    <!-- progress -->
    <div v-if="current" class="w-full max-w-xl">
      <div
        class="h-1.5 w-full overflow-hidden rounded-full"
        :style="{ background: 'var(--c-surface-2)' }"
        role="progressbar"
        :aria-valuenow="Math.round(progress * 100)"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <div
          class="h-full rounded-full"
          :style="{ width: `${progress * 100}%`, background: 'var(--c-accent)', transition: 'width 200ms linear' }"
        />
      </div>
      <div class="mt-1.5 flex justify-between text-xs tabular-nums" :style="{ color: 'var(--c-text-muted)' }">
        <span>{{ formatTime(clock.playhead) }}</span>
        <span>{{ current.duration ? formatTime(current.duration) : '--:--' }}</span>
      </div>
    </div>

    <!-- controls -->
    <Controls />

    <!-- queue summary -->
    <p v-if="status && !status.empty" class="text-sm" :style="{ color: 'var(--c-text-muted)' }">
      {{ status.queue_length }} in queue · {{ status.mode }} mode
      <span v-if="!status.play" :style="{ color: 'var(--c-accent)' }"> · paused</span>
    </p>

    <p v-if="error" class="rounded-md px-3 py-2 text-sm" :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-danger)' }">
      Can't reach the bot: {{ error }}
    </p>
  </section>
</template>
