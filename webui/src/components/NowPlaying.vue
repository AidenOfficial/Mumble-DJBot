<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { formatBytes, thumbnailUrl } from '../api'
import Controls from './Controls.vue'
import AddToPlaylist from './AddToPlaylist.vue'
import PrepPanel from './PrepPanel.vue'
import { formatTime, useStatus } from '../composables/useStatus'
import { modeLabel, t, typeLabel } from '../i18n'

const { status, error, clock, progress, syncedAt } = useStatus()

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

const titlePending = computed(() =>
  !!status.value?.prep && /^https?:\/\//.test(current.value?.title ?? ''))

const skipBlocks = computed(() => {
  const d = current.value?.duration || 0
  if (!d) return []
  return (current.value?.skip_segments ?? []).map(([start, end]) => ({
    start, end, left: (start / d) * 100, width: (Math.min(end, d) - start) / d * 100,
  }))
})

const sourceLabel = computed(() => typeLabel(current.value?.type ?? ''))
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
        <!-- 还在读取信息时标题就是链接本身:先显示占位,链接用小字 -->
        <template v-if="titlePending">
          <h1 class="max-w-full truncate text-xl font-semibold sm:text-2xl" :style="{ color: 'var(--c-text-muted)' }">
            {{ t('np.loadingTitle') }}
          </h1>
          <p class="max-w-full truncate text-xs" :style="{ color: 'var(--c-text-faint)' }">{{ current.title }}</p>
        </template>
        <h1 v-else class="max-w-full truncate text-xl font-semibold sm:text-2xl" :title="current.title">
          {{ current.title || t('common.untitled') }}
        </h1>
        <p class="flex items-center gap-2 text-sm" :style="{ color: 'var(--c-text-muted)' }">
          <span
            class="rounded-full px-2 py-0.5 text-xs font-medium"
            :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }"
          >{{ sourceLabel }}</span>
          <span v-if="current.artist && current.artist !== '??'">{{ current.artist }}</span>
          <AddToPlaylist :source="{ source: 'current' }" :label="t('np.save')" />
        </p>
      </template>
      <template v-else>
        <h1 class="text-xl font-semibold sm:text-2xl" :style="{ color: 'var(--c-text-muted)' }">
          {{ t('np.nothing') }}
        </h1>
        <p class="text-sm" :style="{ color: 'var(--c-text-faint)' }">
          {{ t('np.emptyHint') }}
        </p>
      </template>
    </div>

    <!-- 还没出声:显示在等什么、还要多久 -->
    <PrepPanel v-if="current && status?.prep" :prep="status.prep" :synced-at="syncedAt" />

    <!-- progress -->
    <div v-else-if="current" class="w-full max-w-xl">
      <div
        class="relative h-1.5 w-full overflow-hidden rounded-full"
        :style="{ background: 'var(--c-surface-2)' }"
        role="progressbar"
        :aria-valuenow="Math.round(progress * 100)"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <!-- 边下边播时已经下载到的位置(像视频网站的缓冲条) -->
        <div
          v-if="current.download"
          class="absolute inset-y-0 left-0 rounded-full"
          :style="{ width: `${current.download.progress * 100}%`, background: 'var(--c-accent-soft)', transition: 'width 800ms linear' }"
        />
        <div
          class="relative h-full rounded-full"
          :style="{ width: `${progress * 100}%`, background: 'var(--c-accent)', transition: 'width 200ms linear' }"
        />
      </div>
      <!-- SponsorBlock 会跳过的片段 -->
      <div v-if="skipBlocks.length" class="relative -mt-1.5 h-1.5 w-full" aria-hidden="true">
        <div
          v-for="(b, i) in skipBlocks"
          :key="i"
          class="absolute inset-y-0 rounded-full"
          :style="{ left: `${b.left}%`, width: `${b.width}%`, background: 'repeating-linear-gradient(135deg, var(--c-text-faint) 0 3px, transparent 3px 6px)', opacity: 0.7 }"
          :title="t('np.skipped', { from: formatTime(b.start), to: formatTime(b.end) })"
        />
      </div>
      <div class="mt-1.5 flex justify-between text-xs tabular-nums" :style="{ color: 'var(--c-text-muted)' }">
        <span>{{ formatTime(clock.playhead) }}</span>
        <span>{{ current.duration ? formatTime(current.duration) : '--:--' }}</span>
      </div>
      <p v-if="current.download" class="mt-1 text-center text-[11px] tabular-nums" :style="{ color: 'var(--c-text-faint)' }">
        {{ t('np.bgDownload', { pct: Math.round(current.download.progress * 100) }) }}<template
          v-if="current.download.speed"> · {{ formatBytes(current.download.speed) }}/s</template><template
          v-if="current.download.eta"> · {{ t('np.bgDone', { eta: formatTime(current.download.eta) }) }}</template>
      </p>
      <p v-if="skipBlocks.length" class="mt-1 text-center text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
        {{ t('np.skipping', { n: skipBlocks.length }) }}
      </p>
    </div>

    <!-- controls -->
    <Controls />

    <!-- queue summary -->
    <p v-if="status && !status.empty" class="text-sm" :style="{ color: 'var(--c-text-muted)' }">
      {{ t('np.summary', { n: status.queue_length, mode: modeLabel(status.mode) }) }}
      <span v-if="!status.play" :style="{ color: 'var(--c-accent)' }"> · {{ t('np.paused') }}</span>
    </p>

    <p v-if="error" class="rounded-md px-3 py-2 text-sm" :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-danger)' }">
      {{ t('np.unreachable', { error }) }}
    </p>
  </section>
</template>
