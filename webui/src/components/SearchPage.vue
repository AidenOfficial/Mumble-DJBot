<script setup lang="ts">
import { ref } from 'vue'
import { postQueue as _pq, thumbnailUrl as _tn } from '../api'
import { formatTime, useStatus } from '../composables/useStatus'

interface SearchResult {
  source: 'youtube' | 'bilibili'
  id: string
  url: string
  title: string
  uploader: string
  duration: number
  thumbnail: string
}

const { applyStatus } = useStatus()

const query = ref('')
const results = ref<SearchResult[]>([])
const failed = ref<string[]>([])
const loading = ref(false)
const searched = ref(false)
const added = ref<Record<string, 'pending' | 'done' | 'error'>>({})

let debounce: ReturnType<typeof setTimeout> | undefined
let seq = 0

function onInput() {
  clearTimeout(debounce)
  debounce = setTimeout(search, 400)
}

async function search() {
  const q = query.value.trim()
  if (q.length < 2) {
    results.value = []
    searched.value = false
    return
  }
  const mySeq = ++seq
  loading.value = true
  try {
    const rv = await fetch(`../api/search?q=${encodeURIComponent(q)}`)
    if (!rv.ok) throw new Error(String(rv.status))
    const data = await rv.json()
    if (mySeq !== seq) return // stale response
    results.value = data.results
    failed.value = data.failed
    searched.value = true
  } catch {
    if (mySeq === seq) {
      results.value = []
      failed.value = ['youtube', 'bilibili']
      searched.value = true
    }
  } finally {
    if (mySeq === seq) loading.value = false
  }
}

async function add(r: SearchResult) {
  const key = `${r.source}:${r.id}`
  added.value[key] = 'pending'
  try {
    const rv = await fetch('../api/search/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: r.source, id: r.id, url: r.url }),
    })
    if (!rv.ok) throw new Error(String(rv.status))
    applyStatus(await rv.json())
    added.value[key] = 'done'
  } catch {
    added.value[key] = 'error'
    setTimeout(() => delete added.value[key], 2500)
  }
}

const SOURCE_STYLE: Record<string, { label: string; bg: string; fg: string }> = {
  youtube: { label: 'YouTube', bg: 'rgba(255,0,0,0.12)', fg: '#e53935' },
  bilibili: { label: 'Bilibili', bg: 'rgba(0,161,214,0.12)', fg: '#00a1d6' },
}
</script>

<template>
  <section class="mx-auto w-full max-w-3xl px-4 py-8">
    <!-- search box -->
    <div class="relative">
      <svg viewBox="0 0 24 24" class="pointer-events-none absolute top-1/2 left-4 h-5 w-5 -translate-y-1/2" fill="none" stroke="currentColor" stroke-width="2" :style="{ color: 'var(--c-text-faint)' }">
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <input
        v-model="query"
        type="search"
        placeholder="Search YouTube and Bilibili..."
        class="w-full rounded-full border py-3 pr-4 pl-12 text-sm outline-none"
        :style="{ background: 'var(--c-surface)', borderColor: 'var(--c-border)', color: 'var(--c-text)', boxShadow: 'var(--shadow-1)' }"
        @input="onInput"
        @keydown.enter="search"
      />
      <div
        v-if="loading"
        class="absolute top-1/2 right-4 h-4 w-4 -translate-y-1/2 animate-spin rounded-full border-2 border-t-transparent"
        :style="{ borderColor: 'var(--c-accent)', borderTopColor: 'transparent' }"
      />
    </div>

    <p v-if="failed.length && searched" class="mt-3 text-xs" :style="{ color: 'var(--c-text-muted)' }">
      {{ failed.join(' & ') }} search unavailable right now - showing the rest.
    </p>

    <!-- results -->
    <ul v-if="results.length" class="mt-5 flex flex-col gap-2">
      <li
        v-for="r in results"
        :key="`${r.source}:${r.id}`"
        class="flex items-center gap-3 rounded-xl p-2.5"
        :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }"
      >
        <div class="h-14 w-24 shrink-0 overflow-hidden rounded-lg" :style="{ background: 'var(--c-surface-2)' }">
          <img v-if="r.thumbnail" :src="r.thumbnail" alt="" class="h-full w-full object-cover" loading="lazy" referrerpolicy="no-referrer" />
        </div>
        <div class="min-w-0 flex-1">
          <p class="line-clamp-2 text-sm font-medium" :title="r.title">{{ r.title }}</p>
          <p class="mt-0.5 flex items-center gap-2 text-xs" :style="{ color: 'var(--c-text-muted)' }">
            <span
              class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
              :style="{ background: SOURCE_STYLE[r.source]?.bg, color: SOURCE_STYLE[r.source]?.fg }"
            >{{ SOURCE_STYLE[r.source]?.label ?? r.source }}</span>
            <span v-if="r.uploader" class="truncate">{{ r.uploader }}</span>
            <span v-if="r.duration" class="shrink-0 tabular-nums">{{ formatTime(r.duration) }}</span>
          </p>
        </div>
        <button
          class="shrink-0 cursor-pointer rounded-full border-0 px-3.5 py-2 text-xs font-semibold"
          :style="added[`${r.source}:${r.id}`] === 'done'
            ? { background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }
            : added[`${r.source}:${r.id}`] === 'error'
              ? { background: 'var(--c-accent-soft)', color: 'var(--c-danger)' }
              : { background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
          :disabled="added[`${r.source}:${r.id}`] === 'pending' || added[`${r.source}:${r.id}`] === 'done'"
          @click="add(r)"
        >
          <span v-if="added[`${r.source}:${r.id}`] === 'done'">Queued ✓</span>
          <span v-else-if="added[`${r.source}:${r.id}`] === 'pending'">...</span>
          <span v-else-if="added[`${r.source}:${r.id}`] === 'error'">Failed</span>
          <span v-else>+ Queue</span>
        </button>
      </li>
    </ul>

    <p v-else-if="searched && !loading" class="mt-8 text-center text-sm" :style="{ color: 'var(--c-text-muted)' }">
      No results for "{{ query }}".
    </p>
    <p v-else-if="!searched" class="mt-8 text-center text-sm" :style="{ color: 'var(--c-text-faint)' }">
      Type at least two characters to search both sources at once.
    </p>
  </section>
</template>
