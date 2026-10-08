<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { formatBytes } from '../api'
import { uploadFile, UploadError } from '../upload'
import { useStatus } from '../composables/useStatus'
import AddToPlaylist from './AddToPlaylist.vue'
import { t, typeLabel } from '../i18n'
import type { Key } from '../i18n/en'

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

interface UploadRow {
  key: number
  name: string
  sent: number
  total: number
  phase: 'uploading' | 'processing' | 'done' | 'error'
  message: string
  itemId?: string
  queued?: boolean
  ctrl: AbortController
}
const uploads = ref<UploadRow[]>([])
let uploadSeq = 0

const UPLOAD_ERRORS = ['too_large', 'unsupported_type', 'no_space', 'network', 'aborted']

async function onUpload(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  // 依次上传,避免多个大文件同时抢带宽
  for (const file of files) {
    const row = reactive<UploadRow>({
      key: ++uploadSeq, name: file.name, sent: 0, total: file.size, phase: 'uploading',
      message: '', ctrl: new AbortController(),
    })
    uploads.value.unshift(row)
    try {
      const rv = await uploadFile(file, (p) => {
        row.sent = p.sent
        row.phase = p.phase
      }, { signal: row.ctrl.signal })
      if (rv.status === 'done') {
        row.phase = 'done'
        row.itemId = rv.item_id
        row.message = rv.extracted
          ? t('lib.savedAudio', { size: formatBytes(rv.final_size ?? 0) })
          : t('lib.savedAs', { path: rv.path ?? '' })
        query(page.value)
      } else {
        row.phase = 'error'
        row.message = rv.error ?? t('lib.processFailed')
      }
    } catch (err) {
      row.phase = 'error'
      row.message = !(err instanceof UploadError) ? t('lib.uploadFailed')
        : UPLOAD_ERRORS.includes(err.code) ? t(`upload.${err.code}` as Key)
        : t('lib.uploadFailedCode', { code: err.code })
    }
  }
}

async function queueUpload(row: UploadRow) {
  if (!row.itemId) return
  try {
    const rv = await fetch('../post', { method: 'POST', body: new URLSearchParams({ add_item_bottom: row.itemId }) })
    if (!rv.ok) throw new Error()
    row.queued = true
    refresh()
  } catch {
    row.message = t('lib.queueFailed')
  }
}

const TYPES = ['', 'file', 'url', 'radio'] as const
</script>

<template>
  <section class="mx-auto w-full max-w-3xl px-4 py-8">
    <!-- filters -->
    <div class="flex flex-col gap-3">
      <input
        v-model="keywords"
        type="search"
        :placeholder="t('lib.filter')"
        class="w-full rounded-full border px-5 py-2.5 text-sm outline-none"
        :style="{ background: 'var(--c-surface)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
        @input="onInput"
      />
      <div class="flex flex-wrap items-center gap-2">
        <div class="flex gap-1 rounded-full p-1" :style="{ background: 'var(--c-surface-2)' }">
          <button
            v-for="k in TYPES"
            :key="k"
            class="cursor-pointer rounded-full border-0 px-3 py-1 text-xs font-medium"
            :style="typeFilter === k
              ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' }
              : { background: 'transparent', color: 'var(--c-text-muted)' }"
            @click="typeFilter = k; query(1)"
          >{{ k ? typeLabel(k) : t('common.all') }}</button>
        </div>
        <select
          v-if="allTags.length"
          v-model="tagFilter"
          class="rounded-full border px-3 py-1.5 text-xs outline-none"
          :style="{ background: 'var(--c-surface)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
          @change="query(1)"
        >
          <option value="">{{ t('lib.allTags') }}</option>
          <option v-for="tag in allTags" :key="tag" :value="tag">{{ tag }}</option>
        </select>
        <label
          v-if="uploadEnabled"
          class="ml-auto cursor-pointer rounded-full px-3 py-1.5 text-xs font-medium"
          :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }"
        >
          {{ t('lib.upload') }}
          <input type="file" accept="audio/*,video/*,.mkv,.flv" multiple class="hidden" @change="onUpload" />
        </label>
      </div>
      <!-- 上传进度 -->
      <ul v-if="uploads.length" class="flex flex-col gap-1.5">
        <li v-for="u in uploads" :key="u.key" class="rounded-xl px-3 py-2"
            :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
          <div class="flex items-center gap-2 text-xs">
            <span class="min-w-0 flex-1 truncate font-medium" :title="u.name">{{ u.name }}</span>
            <span class="shrink-0 tabular-nums" :style="{ color: 'var(--c-text-muted)' }">
              <template v-if="u.phase === 'uploading'">{{ formatBytes(u.sent) }} / {{ formatBytes(u.total) }}</template>
              <template v-else-if="u.phase === 'processing'">{{ t('lib.processing') }}</template>
            </span>
            <button v-if="u.phase === 'uploading'" class="shrink-0 cursor-pointer border-0 bg-transparent p-0 text-xs"
                    :style="{ color: 'var(--c-text-faint)' }" :title="t('common.cancel')" @click="u.ctrl.abort()">✕</button>
            <button v-if="u.phase === 'done' && u.itemId && !u.queued"
                    class="shrink-0 cursor-pointer rounded-md border-0 px-2 py-0.5 text-xs font-semibold"
                    :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
                    @click="queueUpload(u)">{{ t('common.queue') }}</button>
            <span v-if="u.queued" class="shrink-0" :style="{ color: 'var(--c-accent)' }">{{ t('common.queued') }}</span>
          </div>
          <div v-if="u.phase === 'uploading' || u.phase === 'processing'" class="mt-1.5 h-1 w-full overflow-hidden rounded-full"
               :style="{ background: 'var(--c-surface-2)' }">
            <div class="h-full rounded-full" :class="u.phase === 'processing' ? 'animate-pulse' : ''"
                 :style="{ width: `${u.total ? (u.sent / u.total) * 100 : 0}%`, background: 'var(--c-accent)', transition: 'width 300ms' }" />
          </div>
          <p v-if="u.message" class="mt-1 text-xs"
             :style="{ color: u.phase === 'error' ? 'var(--c-danger)' : 'var(--c-success)' }">{{ u.message }}</p>
        </li>
      </ul>
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
            <AddToPlaylist :source="{ source: 'library', item_id: item.id }" />
            <button
              class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
              :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
              :title="t('common.playNext')"
              @click="add(item, true)"
            >{{ t('lib.next') }}</button>
            <button
              class="cursor-pointer rounded-md border-0 px-2.5 py-1 text-xs font-semibold"
              :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
              :title="t('common.addToQueue')"
              @click="add(item, false)"
            >{{ t('common.queue') }}</button>
          </template>
        </div>
      </li>
    </ul>
    <p v-else-if="!loading" class="mt-8 text-center text-sm" :style="{ color: 'var(--c-text-muted)' }">
      {{ t('lib.empty') }}
    </p>

    <!-- pagination -->
    <div v-if="totalPages > 1" class="mt-4 flex items-center justify-center gap-2 text-xs">
      <button
        class="cursor-pointer rounded-full border-0 px-3 py-1.5"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
        :disabled="page <= 1"
        @click="query(page - 1)"
      >{{ t('lib.prev') }}</button>
      <span class="tabular-nums" :style="{ color: 'var(--c-text-muted)' }">{{ page }} / {{ totalPages }}</span>
      <button
        class="cursor-pointer rounded-full border-0 px-3 py-1.5"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
        :disabled="page >= totalPages"
        @click="query(page + 1)"
      >{{ t('lib.nextPage') }}</button>
    </div>
  </section>
</template>
