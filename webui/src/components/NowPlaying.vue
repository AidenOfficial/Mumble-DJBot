<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { formatBytes, postControls, thumbnailUrl } from '../api'
import Controls from './Controls.vue'
import AddToPlaylist from './AddToPlaylist.vue'
import FavButton from './FavButton.vue'
import PrepPanel from './PrepPanel.vue'
import { formatTime, useStatus } from '../composables/useStatus'
import { modeLabel, t, typeLabel } from '../i18n'

const { status, error, clock, syncedAt, applyStatus } = useStatus()

const current = computed(() => status.value?.current ?? null)

// Only swap the artwork when the song (or its artwork source) actually changes,
// so the <img> does not flicker on every poll. 来源从 '' 变成 album/video 时
// 地址跟着变,浏览器就会换上高清图。
const artSrc = ref<string | null>(null)
const artKind = ref('')
watch(
  () => [current.value?.id, current.value?.has_thumbnail, current.value?.artwork] as const,
  ([id, hasThumb, art]) => {
    artSrc.value = id && hasThumb ? thumbnailUrl(id, { version: art || 'pending' }) : null
    artKind.value = art || ''
  },
  { immediate: true },
)

// ---- 进度条拖动 ----
const duration = computed(() => current.value?.duration || 0)
const canSeek = computed(() => duration.value > 0)
const bar = ref<HTMLElement | null>(null)
const dragAt = ref<number | null>(null)   // 拖动 / 键盘调整中的目标位置(秒)
const hoverAt = ref<number | null>(null)
const seekingSince = ref(0)                // 刚跳转:ffmpeg 重启的那一下不闪准备面板
const shownPlayhead = computed(() => dragAt.value ?? clock.playhead)
const shownProgress = computed(() =>
  duration.value ? Math.min(1, Math.max(0, shownPlayhead.value / duration.value)) : 0)

function secondsAt(e: PointerEvent) {
  const r = bar.value!.getBoundingClientRect()
  return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * duration.value
}
function onPointerDown(e: PointerEvent) {
  if (!canSeek.value || e.button !== 0) return
  bar.value!.setPointerCapture(e.pointerId)
  dragAt.value = secondsAt(e)
  e.preventDefault()
}
function onPointerMove(e: PointerEvent) {
  if (!canSeek.value) return
  if (dragAt.value !== null && bar.value!.hasPointerCapture(e.pointerId)) dragAt.value = secondsAt(e)
  else hoverAt.value = secondsAt(e)
}
function onPointerUp(e: PointerEvent) {
  if (dragAt.value === null || !bar.value!.hasPointerCapture(e.pointerId)) return
  const target = dragAt.value
  seek(target)
}
function onPointerCancel() {
  dragAt.value = null
}

let keyTimer: ReturnType<typeof setTimeout> | undefined
function onKey(e: KeyboardEvent) {
  if (!canSeek.value) return
  const step = { ArrowLeft: -5, ArrowRight: 5, PageDown: -30, PageUp: 30 }[e.key]
  if (step === undefined && e.key !== 'Home') return
  e.preventDefault()
  const base = dragAt.value ?? clock.playhead
  dragAt.value = e.key === 'Home' ? 0 : Math.min(duration.value - 1, Math.max(0, base + (step ?? 0)))
  // 按住方向键时连续调整,停下来再真正跳转(每次跳转都要重启 ffmpeg)
  clearTimeout(keyTimer)
  keyTimer = setTimeout(() => dragAt.value !== null && seek(dragAt.value), 400)
}

async function seek(position: number) {
  seekingSince.value = performance.now()
  clock.playhead = position
  try {
    applyStatus(await postControls({ action: 'seek', position }))
  } catch {
    // 直播 / 已经切歌:下一次轮询会带回真实位置
  } finally {
    dragAt.value = null
  }
}

const showPrep = computed(() => {
  if (!current.value || !status.value?.prep) return false
  // 跳转后几秒内的"准备中"只是 ffmpeg 在重新打开文件,别把进度条换成准备面板
  return !seekingSince.value || performance.now() - seekingSince.value > 4000
})
watch(() => status.value?.prep, (prep) => {
  if (!prep) seekingSince.value = 0
})
watch(() => current.value?.id, () => {
  dragAt.value = null
  seekingSince.value = 0
})

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
      <template v-if="artSrc">
        <!-- 视频封面是 16:9:整张放进方框,后面垫一层同图的模糊放大版 -->
        <img
          v-if="artKind === 'video'"
          :src="artSrc"
          alt=""
          aria-hidden="true"
          class="absolute inset-0 h-full w-full scale-125 object-cover opacity-80 blur-2xl"
        />
        <img
          :src="artSrc"
          alt=""
          class="relative h-full w-full"
          :class="artKind === 'video' ? 'object-contain' : 'object-cover'"
        />
      </template>
      <div
        v-else
        class="flex h-full w-full items-center justify-center"
        :style="{ background: 'linear-gradient(135deg, var(--c-accent-soft), var(--c-surface-2))' }"
      >
        <Icon icon="music" class="text-7xl opacity-40" aria-hidden="true" />
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
          <span v-if="current.album" class="hidden max-w-48 truncate sm:inline" :title="current.album">· {{ current.album }}</span>
          <FavButton :item-id="current.id" :source="{ source: 'library', item_id: current.id }" :label="t('np.save')" />
          <AddToPlaylist :source="{ source: 'current' }" :label="t('atp.header')" />
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
    <PrepPanel v-if="showPrep && status?.prep" :prep="status.prep" :synced-at="syncedAt" />

    <!-- progress:点击或拖动跳转,键盘 ←/→ 5 秒、PgUp/PgDn 30 秒 -->
    <div v-else-if="current" class="w-full max-w-xl">
      <div
        ref="bar"
        class="group relative flex h-5 w-full touch-none items-center outline-none"
        :class="canSeek ? 'cursor-pointer' : ''"
        :role="canSeek ? 'slider' : 'progressbar'"
        :tabindex="canSeek ? 0 : -1"
        :aria-label="t('np.seek')"
        :aria-valuenow="Math.round(shownPlayhead)"
        aria-valuemin="0"
        :aria-valuemax="Math.round(duration)"
        :aria-valuetext="formatTime(shownPlayhead)"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerCancel"
        @pointerleave="hoverAt = null"
        @keydown="onKey"
      >
        <div
          class="relative h-1.5 w-full overflow-hidden rounded-full transition-[height] duration-150 group-focus-visible:h-2"
          :class="canSeek ? 'group-hover:h-2' : ''"
          :style="{ background: 'var(--c-surface-2)' }"
        >
          <!-- 边下边播时已经下载到的位置(像视频网站的缓冲条) -->
          <div
            v-if="current.download"
            class="absolute inset-y-0 left-0 rounded-full"
            :style="{ width: `${current.download.progress * 100}%`, background: 'var(--c-accent-soft)', transition: 'width 800ms linear' }"
          />
          <div
            class="relative h-full rounded-full"
            :style="{ width: `${shownProgress * 100}%`, background: 'var(--c-accent)', transition: dragAt === null ? 'width 200ms linear' : 'none' }"
          />
          <!-- SponsorBlock 会跳过的片段 -->
          <div
            v-for="(b, i) in skipBlocks"
            :key="i"
            class="absolute inset-y-0"
            :style="{ left: `${b.left}%`, width: `${b.width}%`, background: 'repeating-linear-gradient(135deg, var(--c-text-faint) 0 3px, transparent 3px 6px)', opacity: 0.7 }"
            :title="t('np.skipped', { from: formatTime(b.start), to: formatTime(b.end) })"
          />
        </div>
        <!-- 拖动手柄:悬停 / 拖动 / 键盘聚焦时出现 -->
        <div
          v-if="canSeek"
          class="pointer-events-none absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
          :class="dragAt !== null ? 'opacity-100' : ''"
          :style="{ left: `${shownProgress * 100}%`, background: 'var(--c-accent)', boxShadow: 'var(--shadow-2)' }"
        />
        <!-- 悬停 / 拖动时显示目标时间 -->
        <div
          v-if="canSeek && (dragAt ?? hoverAt) !== null"
          class="pointer-events-none absolute -top-6 -translate-x-1/2 rounded px-1.5 py-0.5 text-[11px] tabular-nums"
          :style="{ left: `${((dragAt ?? hoverAt)! / duration) * 100}%`, background: 'var(--c-surface-2)', color: 'var(--c-text)', boxShadow: 'var(--shadow-2)' }"
        >{{ formatTime((dragAt ?? hoverAt)!) }}</div>
      </div>
      <div class="mt-1.5 flex justify-between text-xs tabular-nums" :style="{ color: 'var(--c-text-muted)' }">
        <span>{{ formatTime(shownPlayhead) }}</span>
        <span>{{ current.duration ? formatTime(current.duration) : '--:--' }}</span>
      </div>
      <p v-if="current.download" class="mt-1 text-center text-[11px] tabular-nums" :style="{ color: 'var(--c-text-faint)' }">
        <Icon icon="download" class="mr-1" />{{ t('np.bgDownload', { pct: Math.round(current.download.progress * 100) }) }}<template
          v-if="current.download.speed"> · {{ formatBytes(current.download.speed) }}/s</template><template
          v-if="current.download.eta"> · {{ t('np.bgDone', { eta: formatTime(current.download.eta) }) }}</template>
      </p>
      <p v-if="skipBlocks.length" class="mt-1 text-center text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
        <Icon icon="forward-step" class="mr-1" />{{ t('np.skipping', { n: skipBlocks.length }) }}
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
