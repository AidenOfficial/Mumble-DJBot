<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useStatus } from '../composables/useStatus'

interface LibItem {
  id: string
  title: string
  type: string
  artist: string
  path: string
  thumb: string
  tags: [string, string][]
}

const { refresh } = useStatus()

const keywords = ref('')
const typeFilter = ref<'file' | 'url' | 'radio' | ''>('')
const tagFilter = ref('')
const allTags = ref<string[]>([])
const uploadEnabled = ref(false)
const items = ref<LibItem[]>([])
const totalPages = ref(0)
const page = ref(1)
const loading = ref(false)
const feedback = ref<Record<string, string>>({})
const uploadMsg = ref('')

onMounted(async () => {
  try {
    const rv = await fetch('../library/info')
    const info = await rv.json()
    allTags.value = info.tags ?? []
    uploadEnabled.value = !!info.upload_enabled
  } catch { /* non-fatal */ }
  query()
})

let debounce: ReturnType<typeof setTimeout> | undefined
function onInput() {
  clearTimeout(debounce)
  debounce = setTimeout(() => query(1), 400)
}

async function query(toPage = 1) {
  loading.value = true
  page.value = toPage
  try {
    const body = new URLSearchParams({
      action: 'query',
      type: typeFilter.value || 'file,url,radio',
      dir: '.',
      tags: tagFilter.value,
      keywords: keywords.value,
      page: String(toPage),
    })
    const rv = await fetch('../library', { method: 'POST', body })
    const data = await rv.json()
    items.value = data.items ?? []
    totalPages.value = data.total_pages ?? 0
  } catch {
    items.value = []
    totalPages.value = 0
  } finally {
    loading.value = false
  }
}

async function add(item: LibItem, next: boolean) {
  feedback.value[item.id] = '...'
  try {
    const rv = await fetch('../post', {
      method: 'POST',
      body: new URLSearchParams(
        next ? { add_item_next: item.id } : { add_item_bottom: item.id }),
    })
    if (!rv.ok) throw new Error(String(rv.status))
    feedback.value[item.id] = '✓'
    refresh()
  } catch {
    feedback.value[item.id] = '✗'
  }
  setTimeout(() => delete feedback.value[item.id], 2000)
}

async function onUpload(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploadMsg.value = 'Uploading...'
  const form = new FormData()
  form.append('file', file)
  form.append('targetdir', 'uploads/')
  try {
    const rv = await fetch('../upload', { method: 'POST', body: form })
    if (rv.status === 409) uploadMsg.value = 'Already exists.'
    else if (!rv.ok) throw new Error(String(rv.status))
    else {
      uploadMsg.value = 'Uploaded ✓ (rescan to index)'
      query(page.value)
    }
  } catch {
    uploadMsg.value = 'Upload failed.'
  }
  input.value = ''
  setTimeout(() => (uploadMsg.value = ''), 4000)
}

const TYPES = [
  { key: '', label: 'All' },
  { key: 'file', label: 'Library' },
  { key: 'url', label: 'Stream' },
  { key: 'radio', label: 'Radio' },
] as const
</script>

<template>
  <section class="mx-auto w-full max-w-3xl px-4 py-8">
    <!-- filters -->
    <div class="flex flex-col gap-3">
      <input
        v-model="keywords"
        type="search"
        placeholder="Filter the library..."
        class="w-full rounded-full border px-5 py-2.5 text-sm outline-none"
        :style="{ background: 'var(--c-surface)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
        @input="onInput"
      />
      <div class="flex flex-wrap items-center gap-2">
        <div class="flex gap-1 rounded-full p-1" :style="{ background: 'var(--c-surface-2)' }">
          <button
            v-for="t in TYPES"
            :key="t.key"
            class="cursor-pointer rounded-full border-0 px-3 py-1 text-xs font-medium"
            :style="typeFilter === t.key
              ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' }
              : { background: 'transparent', color: 'var(--c-text-muted)' }"
            @click="typeFilter = t.key; query(1)"
          >{{ t.label }}</button>
        </div>
        <select
          v-if="allTags.length"
          v-model="tagFilter"
          class="rounded-full border px-3 py-1.5 text-xs outline-none"
          :style="{ background: 'var(--c-surface)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
          @change="query(1)"
        >
          <option value="">All tags</option>
          <option v-for="t in allTags" :key="t" :value="t">{{ t }}</option>
        </select>
        <label
          v-if="uploadEnabled"
          class="ml-auto cursor-pointer rounded-full px-3 py-1.5 text-xs font-medium"
          :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }"
        >
          Upload
          <input type="file" accept="audio/*,video/*" class="hidden" @change="onUpload" />
        </label>
      </div>
      <p v-if="uploadMsg" class="text-xs" :style="{ color: 'var(--c-text-muted)' }">{{ uploadMsg }}</p>
    </div>

    <!-- results -->
    <ul v-if="items.length" class="mt-4 flex flex-col gap-1.5">
      <li
        v-for="item in items"
        :key="item.id"
        class="flex items-center gap-3 rounded-xl px-3 py-2"
        :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }"
      >
        <div class="h-10 w-10 shrink-0 overflow-hidden rounded-lg" :style="{ background: 'var(--c-surface-2)' }">
          <img v-if="item.thumb && item.thumb.startsWith('data:')" :src="item.thumb" alt="" class="h-full w-full object-cover" />
          <div v-else class="flex h-full w-full items-center justify-center opacity-40">
            <svg viewBox="0 0 24 24" class="h-5 w-5" fill="currentColor"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" /></svg>
          </div>
        </div>
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-medium" :title="item.title">{{ item.title || item.path }}</p>
          <p class="flex items-center gap-1.5 truncate text-xs" :style="{ color: 'var(--c-text-muted)' }">
            <span v-if="item.artist && item.artist !== '??'">{{ item.artist }}</span>
            <span v-for="[tag] in item.tags.slice(0, 3)" :key="tag" class="rounded-full px-1.5 py-0.5 text-[10px]" :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }">{{ tag }}</span>
          </p>
        </div>
        <div class="flex shrink-0 gap-1">
          <span v-if="feedback[item.id]" class="px-2 py-1 text-xs" :style="{ color: 'var(--c-accent)' }">{{ feedback[item.id] }}</span>
          <template v-else>
            <button
              class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
              :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
              title="Play next"
              @click="add(item, true)"
            >⤴ Next</button>
            <button
              class="cursor-pointer rounded-md border-0 px-2.5 py-1 text-xs font-semibold"
              :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
              title="Add to queue"
              @click="add(item, false)"
            >+ Queue</button>
          </template>
        </div>
      </li>
    </ul>
    <p v-else-if="!loading" class="mt-8 text-center text-sm" :style="{ color: 'var(--c-text-muted)' }">
      Nothing in the library matches.
    </p>

    <!-- pagination -->
    <div v-if="totalPages > 1" class="mt-4 flex items-center justify-center gap-2 text-xs">
      <button
        class="cursor-pointer rounded-full border-0 px-3 py-1.5"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
        :disabled="page <= 1"
        @click="query(page - 1)"
      >‹ Prev</button>
      <span class="tabular-nums" :style="{ color: 'var(--c-text-muted)' }">{{ page }} / {{ totalPages }}</span>
      <button
        class="cursor-pointer rounded-full border-0 px-3 py-1.5"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
        :disabled="page >= totalPages"
        @click="query(page + 1)"
      >Next ›</button>
    </div>
  </section>
</template>
