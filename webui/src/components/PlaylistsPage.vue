<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  addToPlaylist, createPlaylist, deletePlaylist, fetchPlaylist, movePlaylistItem, playPlaylist,
  removeFromPlaylist, renamePlaylist, startImport, fetchImportJob, type ImportJob, type PlaylistDetail,
} from '../api'
import { useMe } from '../composables/useMe'
import { formatTime, useStatus } from '../composables/useStatus'
import { t, typeLabel } from '../i18n'
import type { Key } from '../i18n/en'

const { me, playlists, reloadPlaylists } = useMe()
const { applyStatus } = useStatus()

const selectedId = ref<number | null>(null)
const detail = ref<PlaylistDetail | null>(null)
const newName = ref('')
const renaming = ref(false)
const renameDraft = ref('')
const toast = ref('')
const busy = ref(false)
const dragFrom = ref<number | null>(null)
const dragOver = ref<number | null>(null)

let toastTimer: ReturnType<typeof setTimeout> | undefined
function say(msg: string) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 3000)
}

watch(() => me.value?.can_have_playlists, (ok) => { if (ok) reloadPlaylists() }, { immediate: true })

// 默认选中第一个歌单
watch(playlists, (list) => {
  if (selectedId.value === null && list.length) select(list[0]!.id)
  if (selectedId.value !== null && !list.some((p) => p.id === selectedId.value)) {
    selectedId.value = null
    detail.value = null
  }
})

async function select(id: number) {
  selectedId.value = id
  renaming.value = false
  try {
    detail.value = await fetchPlaylist(id)
  } catch {
    detail.value = null
  }
}

async function create() {
  const name = newName.value.trim()
  if (!name) return
  try {
    const pl = await createPlaylist(name)
    newName.value = ''
    await reloadPlaylists()
    select(pl.id)
  } catch {
    say(t('pl.createFailed'))
  }
}

async function rename() {
  if (!detail.value) return
  const name = renameDraft.value.trim()
  if (!name) return
  try {
    detail.value = await renamePlaylist(detail.value.id, name)
    renaming.value = false
    reloadPlaylists()
  } catch {
    say(t('pl.renameFailed'))
  }
}

async function remove() {
  if (!detail.value) return
  if (!confirm(t('pl.confirmDelete', { name: detail.value.name }))) return
  await deletePlaylist(detail.value.id)
  selectedId.value = null
  detail.value = null
  reloadPlaylists()
}

async function play(mode: 'append' | 'next' | 'replace', opts: { shuffle?: boolean; item?: number } = {}) {
  if (!detail.value || busy.value) return
  busy.value = true
  try {
    const rv = await playPlaylist(detail.value.id, { mode, ...opts })
    applyStatus(rv)
    const verb = t(mode === 'replace' ? 'pl.verbReplace' : mode === 'next' ? 'pl.verbNext' : 'pl.verbAppend')
    say(t('pl.playResult', { verb, n: rv.queued }) +
        (rv.skipped ? t('pl.unavailable', { n: rv.skipped }) : ''))
  } catch {
    say(t('pl.queueFailed'))
  } finally {
    busy.value = false
  }
}

// ---- 导入 YouTube / 网易云 / Spotify 歌单 ----
const importOpen = ref(false)
const importUrl = ref('')
const importInto = ref<'new' | 'current'>('new')
const importJob = ref<ImportJob | null>(null)
const importError = ref('')
const IMPORT_ERRORS = ['unsupported_source', 'spotify_not_configured', 'list_failed', 'no_entries', 'too_many_playlists']
const importErrorText = (code: string) =>
  IMPORT_ERRORS.includes(code) ? t(`import.${code}` as Key) : t('pl.importFailed')

async function runImport() {
  importError.value = ''
  importJob.value = null
  try {
    const id = await startImport(importUrl.value.trim(),
      importInto.value === 'current' && detail.value ? detail.value.id : undefined)
    for (;;) {
      const job = await fetchImportJob(id)
      importJob.value = job
      if (job.status === 'done' || job.status === 'error') break
      await new Promise((r) => setTimeout(r, 1500))
    }
    const job = importJob.value!
    if (job.status === 'error') {
      importError.value = importErrorText(job.error ?? '')
      return
    }
    importUrl.value = ''
    await reloadPlaylists()
    if (job.playlist_id) await select(job.playlist_id)
    say(t('pl.imported', { n: job.added ?? 0 }) +
        (job.unmatched?.length ? t('pl.importedUnmatched', { n: job.unmatched.length }) : '') +
        (job.note === 'spotify_truncated' ? t('pl.importedTruncated') : ''))
  } catch (e) {
    importError.value = importErrorText(e instanceof Error ? e.message : '')
  }
}

const importRunning = computed(() => !!importJob.value && ['listing', 'matching'].includes(importJob.value.status))
const importProgress = computed(() => {
  const j = importJob.value
  if (!j) return ''
  if (j.status === 'listing') return t('pl.reading')
  if (j.status === 'matching') {
    return j.source === 'youtube'
      ? t('pl.addingVideos', { n: j.total })
      : t('pl.matching', { done: j.processed, total: j.total })
  }
  return ''
})

async function saveQueue() {
  if (!detail.value) return
  try {
    const rv = await addToPlaylist(detail.value.id, { source: 'all_queue' })
    detail.value = rv.playlist
    say(rv.added ? t('pl.savedQueue', { n: rv.added }) : t('pl.queueEmpty'))
    reloadPlaylists()
  } catch {
    say(t('pl.saveQueueFailed'))
  }
}

async function dropItem(rowId: number) {
  if (!detail.value) return
  detail.value = await removeFromPlaylist(detail.value.id, rowId)
  reloadPlaylists()
}

async function onDrop(i: number) {
  const from = dragFrom.value
  dragFrom.value = null
  dragOver.value = null
  if (!detail.value || from === null || from === i) return
  const moved = detail.value.items.splice(from, 1)[0]!
  detail.value.items.splice(i, 0, moved)
  try {
    detail.value = await movePlaylistItem(detail.value.id, from, i)
  } catch {
    select(detail.value.id)
  }
}

const totalDuration = computed(() =>
  (detail.value?.items ?? []).reduce((sum, e) => sum + (e.duration || 0), 0))

</script>

<template>
  <section class="mx-auto w-full max-w-5xl px-4 py-8">
    <div v-if="me && !me.can_have_playlists" class="mx-auto max-w-md rounded-2xl p-6 text-center"
         :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
      <p class="font-semibold">{{ t('pl.needSignIn') }}</p>
      <p class="mt-2 text-sm" :style="{ color: 'var(--c-text-muted)' }">{{ t('pl.needSignInHint') }}</p>
    </div>

    <div v-else class="grid gap-6 md:grid-cols-[16rem_1fr]">
      <!-- 歌单列表 -->
      <aside>
        <form class="flex gap-2" @submit.prevent="create">
          <input
            v-model="newName"
            maxlength="60"
            :placeholder="t('pl.newPlaceholder')"
            class="min-w-0 flex-1 rounded-full border px-4 py-2 text-sm outline-none"
            :style="{ background: 'var(--c-surface)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
          />
          <button
            type="submit"
            class="cursor-pointer rounded-full border-0 px-3.5 text-sm font-semibold"
            :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
            :title="t('pl.create')"
          >＋</button>
        </form>
        <button
          class="mt-2 w-full cursor-pointer rounded-full border-0 px-4 py-2 text-xs font-medium"
          :style="{ background: importOpen ? 'var(--c-accent-soft)' : 'var(--c-surface-2)', color: importOpen ? 'var(--c-accent)' : 'var(--c-text)' }"
          @click="importOpen = !importOpen"
        >{{ t('pl.importToggle') }}</button>
        <div v-if="importOpen" class="mt-2 rounded-xl p-3 text-xs"
             :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
          <form class="flex flex-col gap-2" @submit.prevent="runImport">
            <input
              v-model="importUrl"
              type="url"
              required
              :placeholder="t('pl.importLink')"
              class="rounded-lg border px-3 py-1.5 text-xs outline-none"
              :style="{ background: 'var(--c-bg)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
              :disabled="importRunning"
            />
            <select
              v-model="importInto"
              class="rounded-lg border px-2 py-1.5 text-xs outline-none"
              :style="{ background: 'var(--c-bg)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
              :disabled="importRunning"
            >
              <option value="new">{{ t('pl.importNew') }}</option>
              <option v-if="detail" value="current">{{ t('pl.importInto', { name: detail.name }) }}</option>
            </select>
            <button type="submit" class="cursor-pointer rounded-lg border-0 px-3 py-1.5 text-xs font-semibold"
                    :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
                    :disabled="importRunning || !importUrl.trim()">{{ t('pl.import') }}</button>
          </form>
          <p class="mt-2 leading-relaxed" :style="{ color: 'var(--c-text-faint)' }">
            {{ t('pl.importHint') }}
          </p>
          <template v-if="importJob && importRunning">
            <p class="mt-2" :style="{ color: 'var(--c-text-muted)' }">{{ importProgress }}</p>
            <div v-if="importJob.total" class="mt-1 h-1 w-full overflow-hidden rounded-full" :style="{ background: 'var(--c-surface-2)' }">
              <div class="h-full rounded-full" :style="{ width: `${(importJob.processed / importJob.total) * 100}%`, background: 'var(--c-accent)', transition: 'width 400ms' }" />
            </div>
          </template>
          <p v-if="importError" class="mt-2" :style="{ color: 'var(--c-danger)' }">{{ importError }}</p>
          <details v-if="importJob?.status === 'done' && importJob.unmatched?.length" class="mt-2">
            <summary class="cursor-pointer" :style="{ color: 'var(--c-text-muted)' }">
              {{ t('pl.notFound', { n: importJob.unmatched.length }) }}
            </summary>
            <ul class="mt-1 max-h-32 overflow-y-auto pl-3" :style="{ color: 'var(--c-text-faint)' }">
              <li v-for="u in importJob.unmatched" :key="u" class="truncate">{{ u }}</li>
            </ul>
          </details>
        </div>
        <ul class="mt-3 flex flex-col gap-1">
          <li v-for="pl in playlists" :key="pl.id">
            <button
              class="flex w-full cursor-pointer items-center justify-between gap-2 rounded-xl border-0 px-3 py-2.5 text-left"
              :style="pl.id === selectedId
                ? { background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }
                : { background: 'transparent', color: 'var(--c-text)' }"
              @click="select(pl.id)"
            >
              <span class="truncate text-sm font-medium">{{ pl.name }}</span>
              <span class="shrink-0 text-xs tabular-nums" :style="{ color: 'var(--c-text-faint)' }">{{ pl.count }}</span>
            </button>
          </li>
        </ul>
        <p v-if="!playlists.length" class="mt-4 text-sm" :style="{ color: 'var(--c-text-faint)' }">
          {{ t('pl.noneYet') }}
        </p>
      </aside>

      <!-- 歌单详情 -->
      <div v-if="detail" class="min-w-0">
        <div class="flex flex-wrap items-end justify-between gap-3">
          <div class="min-w-0">
            <form v-if="renaming" class="flex gap-2" @submit.prevent="rename">
              <input
                v-model="renameDraft"
                maxlength="60"
                class="rounded-lg border px-3 py-1.5 text-lg font-semibold outline-none"
                :style="{ background: 'var(--c-surface)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
              />
              <button type="submit" class="cursor-pointer rounded-lg border-0 px-3 text-xs font-semibold"
                      :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }">{{ t('common.save') }}</button>
            </form>
            <h1 v-else class="flex items-center gap-2 truncate text-2xl font-semibold">
              {{ detail.name }}
              <button class="cursor-pointer border-0 bg-transparent p-0 text-sm" :style="{ color: 'var(--c-text-faint)' }"
                      :title="t('pl.rename')" @click="renaming = true; renameDraft = detail.name">✎</button>
              <button class="cursor-pointer border-0 bg-transparent p-0 text-sm" :style="{ color: 'var(--c-text-faint)' }"
                      :title="t('pl.delete')" @click="remove">🗑</button>
            </h1>
            <p class="mt-1 text-sm" :style="{ color: 'var(--c-text-muted)' }">
              {{ t('common.songs', { n: detail.items.length }) }}
              <span v-if="totalDuration"> · {{ formatTime(totalDuration) }}</span>
            </p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button
              class="cursor-pointer rounded-full border-0 px-4 py-2 text-sm font-semibold"
              :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
              :disabled="!detail.items.length || busy"
              :title="t('pl.playNowHint')"
              @click="play('replace')"
            >{{ t('pl.playNow') }}</button>
            <button
              class="cursor-pointer rounded-full border-0 px-3.5 py-2 text-sm"
              :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
              :disabled="!detail.items.length || busy"
              @click="play('replace', { shuffle: true })"
            >{{ t('pl.shuffle') }}</button>
            <button
              class="cursor-pointer rounded-full border-0 px-3.5 py-2 text-sm"
              :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
              :disabled="!detail.items.length || busy"
              @click="play('append')"
            >{{ t('pl.queueAll') }}</button>
          </div>
        </div>

        <p v-if="toast" class="mt-3 rounded-lg px-3 py-2 text-sm"
           :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }">{{ toast }}</p>

        <ul v-if="detail.items.length" class="mt-4 flex flex-col gap-1.5">
          <li
            v-for="(e, i) in detail.items"
            :key="e.id"
            class="group flex cursor-grab items-center gap-3 rounded-xl px-3 py-2 active:cursor-grabbing"
            :style="{
              background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)',
              ...(dragOver === i && dragFrom !== null && dragFrom !== i ? { outline: '2px solid var(--c-accent)', outlineOffset: '-2px' } : {}),
              ...(dragFrom === i ? { opacity: 0.4 } : {}),
            }"
            draggable="true"
            @dragstart="dragFrom = i"
            @dragover.prevent="dragOver = i"
            @drop.prevent="onDrop(i)"
            @dragend="dragFrom = null; dragOver = null"
          >
            <span class="w-5 shrink-0 text-right text-xs tabular-nums" :style="{ color: 'var(--c-text-faint)' }">{{ i + 1 }}</span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium" :title="e.title">{{ e.title || e.ref }}</p>
              <p class="truncate text-xs" :style="{ color: 'var(--c-text-muted)' }">
                {{ [typeLabel(e.type), e.duration ? formatTime(e.duration) : ''].filter(Boolean).join(' · ') }}
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-1 opacity-60 transition-opacity group-hover:opacity-100">
              <button class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                      :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
                      :title="t('common.playNext')" @click="play('next', { item: e.id })">⤴</button>
              <button class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                      :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
                      :title="t('common.addToQueue')" @click="play('append', { item: e.id })">＋</button>
              <button class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                      :style="{ background: 'var(--c-surface-2)', color: 'var(--c-danger)' }"
                      :title="t('pl.removeItem')" @click="dropItem(e.id)">✕</button>
            </div>
          </li>
        </ul>
        <div v-else class="mt-6 rounded-2xl p-6 text-center text-sm"
             :style="{ background: 'var(--c-surface)', color: 'var(--c-text-muted)' }">
          <p>{{ t('pl.empty') }}</p>
          <p class="mt-1">{{ t('pl.emptyHint') }}</p>
          <button class="mt-3 cursor-pointer rounded-full border-0 px-4 py-2 text-xs font-semibold"
                  :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }"
                  @click="saveQueue">{{ t('pl.saveQueueHere') }}</button>
        </div>
      </div>
    </div>
  </section>
</template>
