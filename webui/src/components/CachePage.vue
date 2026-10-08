<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  cleanupCache, deleteCache, fetchCache, formatBytes, pinCache, saveCacheToLibrary,
  type CacheEntry, type CacheSummary,
} from '../api'
import { formatTime } from '../composables/useStatus'

const summary = ref<CacheSummary | null>(null)
const entries = ref<CacheEntry[]>([])
const loading = ref(false)
const toast = ref('')
const rowBusy = ref<Record<string, boolean>>({})
const filter = ref<'all' | 'pinned' | 'frequent' | 'cold'>('all')
const sortBy = ref<'last_used' | 'size' | 'plays'>('last_used')

let toastTimer: ReturnType<typeof setTimeout> | undefined
function say(msg: string) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 3500)
}

async function reload() {
  loading.value = true
  try {
    const data = await fetchCache()
    summary.value = data.summary
    entries.value = data.entries
  } catch {
    say('Could not load the cache.')
  } finally {
    loading.value = false
  }
}
onMounted(reload)

const shown = computed(() => {
  let list = entries.value
  if (filter.value === 'pinned') list = list.filter((e) => e.pinned)
  else if (filter.value === 'frequent') list = list.filter((e) => e.frequent)
  else if (filter.value === 'cold') list = list.filter((e) => !e.pinned && !e.frequent && !e.in_queue)
  const key = sortBy.value
  return [...list].sort((a, b) => (b[key] as number) - (a[key] as number))
})

const usedPct = computed(() => {
  const s = summary.value
  if (!s || !s.limit_bytes) return 0
  return Math.min(100, (s.total_bytes / s.limit_bytes) * 100)
})
const pinnedPct = computed(() => {
  const s = summary.value
  if (!s || !s.limit_bytes) return 0
  return Math.min(100, (s.pinned_bytes / s.limit_bytes) * 100)
})

function ago(ts: number): string {
  if (!ts) return 'never'
  const s = Date.now() / 1000 - ts
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  if (s < 86400 * 60) return `${Math.round(s / 86400)} d ago`
  return `${Math.round(s / 86400 / 30)} mo ago`
}

async function togglePin(e: CacheEntry) {
  rowBusy.value[e.id] = true
  try {
    await pinCache(e.id, !e.pinned)
    e.pinned = !e.pinned
    say(e.pinned ? 'Pinned — automatic cleanup will keep it.' : 'Unpinned.')
    reload()
  } catch {
    say('Could not change the pin.')
  } finally {
    rowBusy.value[e.id] = false
  }
}

async function remove(e: CacheEntry) {
  const force = e.in_queue
  if (force && !confirm('This song is in the queue. Delete the cached file anyway? It will be downloaded again when it plays.')) return
  rowBusy.value[e.id] = true
  try {
    const freed = await deleteCache(e.id, force)
    say(`Freed ${formatBytes(freed)}.`)
    reload()
  } catch (err) {
    say(err instanceof Error && err.message === 'busy' ? 'Still downloading — try again later.' : 'Delete failed.')
  } finally {
    rowBusy.value[e.id] = false
  }
}

async function saveToLibrary(e: CacheEntry) {
  rowBusy.value[e.id] = true
  try {
    const rv = await saveCacheToLibrary(e.id)
    say(`Saved to the library as ${rv.path}`)
  } catch {
    say('Could not save — is the download complete?')
  } finally {
    rowBusy.value[e.id] = false
  }
}

async function cleanup(mode: 'limit' | 'expired' | 'unpinned') {
  if (mode === 'unpinned' && !confirm('Delete every cached song that is not pinned (queued songs are kept)?')) return
  loading.value = true
  try {
    const rv = await cleanupCache(mode)
    say(rv.freed ? `Freed ${formatBytes(rv.freed)}.` : 'Nothing to clean up.')
  } catch {
    say('Cleanup failed.')
  }
  await reload()
}

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'pinned', label: 'Pinned' },
  { key: 'frequent', label: 'Frequent' },
  { key: 'cold', label: 'Evictable' },
] as const
</script>

<template>
  <section class="mx-auto w-full max-w-3xl px-4 py-8">
    <!-- 概览 -->
    <div v-if="summary" class="rounded-2xl p-5" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <p class="text-2xl font-semibold tabular-nums">
          {{ formatBytes(summary.total_bytes) }}
          <span class="text-sm font-normal" :style="{ color: 'var(--c-text-muted)' }">
            <template v-if="summary.limit_bytes !== null"> of {{ formatBytes(summary.limit_bytes) }} limit</template>
            <template v-else> · no size limit</template>
          </span>
        </p>
        <p class="text-xs" :style="{ color: 'var(--c-text-muted)' }">
          {{ summary.count }} song{{ summary.count === 1 ? '' : 's' }} cached ·
          {{ formatBytes(summary.disk_free) }} free on disk
        </p>
      </div>
      <div v-if="summary.limit_bytes" class="relative mt-3 h-2 w-full overflow-hidden rounded-full" :style="{ background: 'var(--c-surface-2)' }"
           role="meter" :aria-valuenow="Math.round(usedPct)" aria-valuemin="0" aria-valuemax="100">
        <div class="absolute inset-y-0 left-0 rounded-full" :style="{ width: `${usedPct}%`, background: 'var(--c-accent)' }" />
        <div class="absolute inset-y-0 left-0 rounded-full" :style="{ width: `${pinnedPct}%`, background: 'var(--c-accent-strong)', opacity: 0.55 }" />
      </div>
      <p class="mt-2 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
        Over the limit, the least recently used songs are removed first. Pinned songs are never removed
        automatically; songs played {{ summary.auto_keep_plays }}+ times go last and skip the
        {{ summary.keep_days }}-day expiry.
      </p>
      <p v-if="!summary.persistent" class="mt-3 rounded-lg px-3 py-2 text-xs"
         :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-danger)' }">
        The cache lives in {{ summary.folder }}, which is wiped whenever the container is recreated.
        Mount a volume and set BAM_TMP_FOLDER (see docker-compose.yml) to keep it.
      </p>
      <div class="mt-4 flex flex-wrap gap-2">
        <button class="cursor-pointer rounded-full border-0 px-3.5 py-1.5 text-xs font-semibold"
                :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
                :disabled="loading" @click="cleanup('limit')">Trim to limit</button>
        <button class="cursor-pointer rounded-full border-0 px-3.5 py-1.5 text-xs"
                :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
                :disabled="loading" @click="cleanup('expired')">Remove expired</button>
        <button class="cursor-pointer rounded-full border-0 px-3.5 py-1.5 text-xs"
                :style="{ background: 'var(--c-surface-2)', color: 'var(--c-danger)' }"
                :disabled="loading" @click="cleanup('unpinned')">Clear unpinned…</button>
      </div>
    </div>

    <p v-if="toast" class="mt-3 rounded-lg px-3 py-2 text-sm"
       :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }">{{ toast }}</p>

    <!-- 筛选 / 排序 -->
    <div class="mt-5 flex flex-wrap items-center gap-2">
      <div class="flex gap-1 rounded-full p-1" :style="{ background: 'var(--c-surface-2)' }">
        <button v-for="f in FILTERS" :key="f.key"
                class="cursor-pointer rounded-full border-0 px-3 py-1 text-xs font-medium"
                :style="filter === f.key ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' } : { background: 'transparent', color: 'var(--c-text-muted)' }"
                @click="filter = f.key">{{ f.label }}</button>
      </div>
      <select v-model="sortBy" class="ml-auto rounded-full border px-3 py-1.5 text-xs outline-none"
              :style="{ background: 'var(--c-surface)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }">
        <option value="last_used">Recently used</option>
        <option value="size">Largest</option>
        <option value="plays">Most played</option>
      </select>
    </div>

    <!-- 列表 -->
    <ul v-if="shown.length" class="mt-3 flex flex-col gap-1.5">
      <li v-for="e in shown" :key="e.id" class="flex items-center gap-3 rounded-xl px-3 py-2.5"
          :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-medium" :title="e.url || e.id">
            <a v-if="e.url" :href="e.url" target="_blank" rel="noopener noreferrer" class="no-underline"
               :style="{ color: 'var(--c-text)' }">{{ e.title || e.url }}</a>
            <span v-else :style="{ color: 'var(--c-text-muted)' }">{{ e.title || `Unknown (${e.id.slice(0, 8)})` }}</span>
          </p>
          <p class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs" :style="{ color: 'var(--c-text-muted)' }">
            <span class="tabular-nums">{{ formatBytes(e.size) }}</span>
            <span v-if="e.duration" class="tabular-nums">{{ formatTime(e.duration) }}</span>
            <span>{{ e.plays }} play{{ e.plays === 1 ? '' : 's' }}</span>
            <span>used {{ ago(e.last_used) }}</span>
            <span v-if="e.pinned" class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                  :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }">Pinned</span>
            <span v-if="e.frequent" class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                  :style="{ background: 'var(--c-surface-2)', color: 'var(--c-success)' }">Frequent</span>
            <span v-if="e.in_queue" class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                  :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }">In queue</span>
            <span v-if="e.downloading" class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                  :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text-muted)' }">Downloading</span>
          </p>
        </div>
        <div class="flex shrink-0 items-center gap-1">
          <button class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                  :style="e.pinned ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' } : { background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
                  :title="e.pinned ? 'Unpin' : 'Pin: never remove automatically'"
                  :disabled="rowBusy[e.id]" @click="togglePin(e)">📌</button>
          <button class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                  :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
                  title="Copy into the local library (music_folder/saved)"
                  :disabled="rowBusy[e.id] || !e.complete" @click="saveToLibrary(e)">💾</button>
          <button class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                  :style="{ background: 'var(--c-surface-2)', color: 'var(--c-danger)' }"
                  title="Delete cached file" :disabled="rowBusy[e.id] || e.downloading" @click="remove(e)">🗑</button>
        </div>
      </li>
    </ul>
    <p v-else-if="!loading" class="mt-8 text-center text-sm" :style="{ color: 'var(--c-text-muted)' }">
      {{ entries.length ? 'Nothing matches this filter.' : 'The download cache is empty.' }}
    </p>
  </section>
</template>
