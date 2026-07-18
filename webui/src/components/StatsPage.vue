<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { formatTime } from '../composables/useStatus'

interface Stats {
  total_plays: number
  total_seconds: number
  unique_tracks: number
  by_type: { type: string; count: number }[]
  top_tracks: { item_id: string; title: string; count: number }[]
  top_users: { user: string; count: number }[]
  hours: number[]
  weekdays: number[]
  most_skipped: { item_id: string; title: string; count: number }[]
  busiest_day: { date: string; count: number } | null
  last_30d: { date: string; count: number }[]
}

const stats = ref<Stats | null>(null)
const error = ref(false)

onMounted(async () => {
  try {
    const rv = await fetch('../api/stats')
    if (!rv.ok) throw new Error(String(rv.status))
    stats.value = await rv.json()
  } catch {
    error.value = true
  }
})

// Categorical palette for source identity - validated (dataviz six checks)
// for both surfaces; light mode relies on the visible labels for relief.
const TYPE_COLORS: Record<string, { light: string; dark: string; label: string }> = {
  url: { light: '#6d5ef2', dark: '#8577ff', label: 'Stream' },
  file: { light: '#1baf7a', dark: '#199e70', label: 'Library' },
  radio: { light: '#eda100', dark: '#c98500', label: 'Radio' },
  playlist: { light: '#e87ba4', dark: '#d55181', label: 'Playlist' },
}
const isDark = () =>
  document.documentElement.getAttribute('data-theme') === 'dark' ||
  (!document.documentElement.getAttribute('data-theme') &&
    window.matchMedia('(prefers-color-scheme: dark)').matches)

function typeColor(t: string): string {
  const entry = TYPE_COLORS[t]
  if (!entry) return 'var(--c-text-faint)'
  return isDark() ? entry.dark : entry.light
}
function typeLabel(t: string): string {
  return TYPE_COLORS[t]?.label ?? (t || 'other')
}

const totalHours = computed(() =>
  stats.value ? (stats.value.total_seconds / 3600).toFixed(1) : '0')

const maxHour = computed(() => Math.max(1, ...(stats.value?.hours ?? [1])))
const maxTrack = computed(() => Math.max(1, ...(stats.value?.top_tracks.map(t => t.count) ?? [1])))
const maxUser = computed(() => Math.max(1, ...(stats.value?.top_users.map(u => u.count) ?? [1])))
const typeTotal = computed(() =>
  Math.max(1, (stats.value?.by_type ?? []).reduce((a, b) => a + b.count, 0)))

const HOUR_LABELS = [0, 6, 12, 18]
</script>

<template>
  <section class="mx-auto w-full max-w-3xl px-4 py-8">
    <p v-if="error" class="text-center text-sm" :style="{ color: 'var(--c-text-muted)' }">
      Statistics are unavailable.
    </p>
    <p v-else-if="!stats" class="text-center text-sm" :style="{ color: 'var(--c-text-faint)' }">
      Loading...
    </p>

    <template v-else-if="stats.total_plays === 0">
      <p class="mt-10 text-center text-sm" :style="{ color: 'var(--c-text-muted)' }">
        No plays recorded yet - statistics appear once the bot starts playing.
      </p>
    </template>

    <template v-else>
      <!-- stat tiles -->
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div
          v-for="tile in ([
            { label: 'Total plays', value: String(stats.total_plays), sub: undefined },
            { label: 'Hours played', value: totalHours, sub: undefined },
            { label: 'Unique tracks', value: String(stats.unique_tracks), sub: undefined },
            { label: 'Busiest day', value: stats.busiest_day ? String(stats.busiest_day.count) : '-',
              sub: stats.busiest_day?.date },
          ] as { label: string; value: string; sub?: string }[])"
          :key="tile.label"
          class="rounded-xl p-4"
          :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }"
        >
          <p class="text-2xl font-semibold tabular-nums">{{ tile.value }}</p>
          <p class="mt-0.5 text-xs" :style="{ color: 'var(--c-text-muted)' }">
            {{ tile.label }}<span v-if="tile.sub"> · {{ tile.sub }}</span>
          </p>
        </div>
      </div>

      <!-- source share -->
      <div class="mt-6 rounded-xl p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <h2 class="mb-3 text-sm font-semibold">Where the music comes from</h2>
        <div class="flex h-3 w-full gap-[2px] overflow-hidden rounded-full">
          <div
            v-for="row in stats.by_type"
            :key="row.type"
            :style="{ width: `${(row.count / typeTotal) * 100}%`, background: typeColor(row.type) }"
            :title="`${typeLabel(row.type)}: ${row.count}`"
          />
        </div>
        <div class="mt-3 flex flex-wrap gap-x-4 gap-y-1">
          <span v-for="row in stats.by_type" :key="row.type" class="flex items-center gap-1.5 text-xs">
            <span class="h-2.5 w-2.5 rounded-sm" :style="{ background: typeColor(row.type) }" />
            <span>{{ typeLabel(row.type) }}</span>
            <span class="tabular-nums" :style="{ color: 'var(--c-text-muted)' }">{{ row.count }}</span>
          </span>
        </div>
      </div>

      <!-- hour histogram -->
      <div class="mt-4 rounded-xl p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <h2 class="mb-3 text-sm font-semibold">When the channel listens</h2>
        <div class="flex h-24 items-end gap-[2px]">
          <div
            v-for="(count, h) in stats.hours"
            :key="h"
            class="min-w-0 flex-1 rounded-t"
            :style="{
              height: `${(count / maxHour) * 100}%`,
              background: 'var(--c-accent)',
              opacity: count ? 1 : 0.15,
              minHeight: '2px',
            }"
            :title="`${String(h).padStart(2, '0')}:00 - ${count} play${count === 1 ? '' : 's'}`"
          />
        </div>
        <div class="mt-1 flex justify-between text-[10px] tabular-nums" :style="{ color: 'var(--c-text-faint)' }">
          <span v-for="h in HOUR_LABELS" :key="h">{{ String(h).padStart(2, '0') }}:00</span>
          <span>23:00</span>
        </div>
      </div>

      <!-- top tracks / users -->
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <div class="rounded-xl p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
          <h2 class="mb-3 text-sm font-semibold">
            Most played
            <span v-if="stats.top_tracks[0]" class="ml-1 rounded-full px-1.5 py-0.5 text-[10px]" :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }">镇站之宝</span>
          </h2>
          <ol class="flex flex-col gap-2">
            <li v-for="t in stats.top_tracks.slice(0, 8)" :key="t.item_id" class="text-xs">
              <div class="mb-0.5 flex justify-between gap-2">
                <span class="truncate" :title="t.title">{{ t.title || t.item_id }}</span>
                <span class="tabular-nums" :style="{ color: 'var(--c-text-muted)' }">{{ t.count }}</span>
              </div>
              <div class="h-1.5 rounded-full" :style="{ background: 'var(--c-surface-2)' }">
                <div class="h-full rounded-full" :style="{ width: `${(t.count / maxTrack) * 100}%`, background: 'var(--c-accent)' }" />
              </div>
            </li>
          </ol>
        </div>

        <div class="flex flex-col gap-4">
          <div class="rounded-xl p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
            <h2 class="mb-3 text-sm font-semibold">Top requesters</h2>
            <ol class="flex flex-col gap-2">
              <li v-for="u in stats.top_users.slice(0, 5)" :key="u.user" class="text-xs">
                <div class="mb-0.5 flex justify-between gap-2">
                  <span class="truncate">{{ u.user }}</span>
                  <span class="tabular-nums" :style="{ color: 'var(--c-text-muted)' }">{{ u.count }}</span>
                </div>
                <div class="h-1.5 rounded-full" :style="{ background: 'var(--c-surface-2)' }">
                  <div class="h-full rounded-full" :style="{ width: `${(u.count / maxUser) * 100}%`, background: 'var(--c-accent)' }" />
                </div>
              </li>
            </ol>
          </div>

          <div v-if="stats.most_skipped.length" class="rounded-xl p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
            <h2 class="mb-2 text-sm font-semibold">Most skipped</h2>
            <ul class="flex flex-col gap-1">
              <li v-for="s in stats.most_skipped.slice(0, 3)" :key="s.item_id" class="flex justify-between gap-2 text-xs">
                <span class="truncate" :title="s.title">{{ s.title || s.item_id }}</span>
                <span class="tabular-nums" :style="{ color: 'var(--c-text-muted)' }">{{ s.count }}×</span>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <p class="mt-4 text-center text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
        {{ formatTime(stats.total_seconds) }} of music since tracking began.
      </p>
    </template>
  </section>
</template>
