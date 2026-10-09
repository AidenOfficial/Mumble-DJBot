<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  addToPlaylist, copyPlaylist, createPlaylist, deletePlaylist, fetchPlaylist, fetchPublicPlaylists,
  movePlaylistItem, playPlaylist, removeFromPlaylist, renamePlaylist, setPlaylistPublic, startImport,
  fetchImportJob, type ImportJob, type PlaylistDetail, type PlaylistSummary,
} from '../api'
import AddToPlaylist from './AddToPlaylist.vue'
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
const signedIn = computed(() => !!me.value?.can_have_playlists)

// 别人公开的歌单(访客也能看、能播放)
const shared = ref<PlaylistSummary[]>([])
async function loadShared() {
  try {
    shared.value = await fetchPublicPlaylists()
  } catch { /* 状态轮询那边会提示连接错误 */ }
}
let sharedTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  loadShared()
  sharedTimer = setInterval(loadShared, 60000)
})
onBeforeUnmount(() => clearInterval(sharedTimer))

// 默认选中:自己的第一个,没有就选大家的第一个
watch([playlists, shared], ([mine, others]) => {
  if (selectedId.value === null) {
    const first = mine[0] ?? others[0]
    if (first) select(first.id)
    return
  }
  // 自己的歌单在别处被删了
  if (detail.value?.mine && !mine.some((p) => p.id === selectedId.value)) {
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
    // 别人刚把它改回私人 / 删掉了
    const wasShared = shared.value.some((p) => p.id === id)
    detail.value = null
    selectedId.value = null
    if (wasShared) {
      say(t('pl.noLongerShared'))
      loadShared()
    }
  }
}

async function togglePublic() {
  if (!detail.value?.mine) return
  try {
    const next = !detail.value.public
    detail.value = await setPlaylistPublic(detail.value.id, next)
    say(t(next ? 'pl.nowPublic' : 'pl.nowPrivate'))
    reloadPlaylists()
  } catch {
    say(t('common.saveFailed'))
  }
}

async function copyToMine() {
  if (!detail.value || detail.value.mine) return
  try {
    const pl = await copyPlaylist(detail.value.id)
    await reloadPlaylists()
    await select(pl.id)
    say(t('pl.copied', { name: pl.name }))
  } catch (e) {
    say(e instanceof Error && e.message.endsWith('409') ? t('import.too_many_playlists') : t('pl.copyFailed'))
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
    <!-- 访客、也没有任何公开歌单:只解释为什么空 -->
    <div v-if="me && !signedIn && !shared.length" class="mx-auto max-w-md rounded-2xl p-6 text-center"
         :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
      <p class="font-semibold">{{ t('pl.needSignIn') }}</p>
      <p class="mt-2 text-sm" :style="{ color: 'var(--c-text-muted)' }">{{ t('pl.needSignInHint') }}</p>
    </div>

    <div v-else class="grid gap-6 md:grid-cols-[16rem_1fr]">
      <!-- 歌单列表 -->
      <aside>
        <div v-if="!signedIn" class="rounded-xl p-3 text-xs leading-relaxed"
             :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)', color: 'var(--c-text-muted)' }">
          <p class="font-semibold" :style="{ color: 'var(--c-text)' }">{{ t('pl.needSignIn') }}</p>
          <p class="mt-1">{{ t('pl.guestShared') }}</p>
        </div>
        <template v-else>
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
              <option v-if="detail?.mine" value="current">{{ t('pl.importInto', { name: detail.name }) }}</option>
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
        <h2 class="mt-5 mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-faint)' }">
          {{ t('pl.mine') }}
        </h2>
        <ul class="flex flex-col gap-1">
          <li v-for="pl in playlists" :key="pl.id">
            <button
              class="flex w-full cursor-pointer items-center justify-between gap-2 rounded-xl border-0 px-3 py-2.5 text-left"
              :style="pl.id === selectedId
                ? { background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }
                : { background: 'transparent', color: 'var(--c-text)' }"
              @click="select(pl.id)"
            >
              <span class="truncate text-sm font-medium">{{ pl.name }}</span>
              <span class="flex shrink-0 items-center gap-1.5 text-xs tabular-nums" :style="{ color: 'var(--c-text-faint)' }">
                <span v-if="pl.public" :title="t('pl.publicBadge')" aria-hidden="true">🌐</span>{{ pl.count }}
              </span>
            </button>
          </li>
        </ul>
        <p v-if="!playlists.length" class="mt-1 px-3 text-sm" :style="{ color: 'var(--c-text-faint)' }">
          {{ t('pl.noneYet') }}
        </p>
        </template>

        <!-- 大家公开的歌单 -->
        <template v-if="shared.length">
          <h2 class="mt-5 mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-faint)' }">
            {{ t('pl.shared') }}
          </h2>
          <ul class="flex flex-col gap-1">
            <li v-for="pl in shared" :key="pl.id">
              <button
                class="flex w-full cursor-pointer items-center justify-between gap-2 rounded-xl border-0 px-3 py-2 text-left"
                :style="pl.id === selectedId
                  ? { background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }
                  : { background: 'transparent', color: 'var(--c-text)' }"
                @click="select(pl.id)"
              >
                <span class="min-w-0">
                  <span class="block truncate text-sm font-medium">{{ pl.name }}</span>
                  <span class="block truncate text-[11px]" :style="{ color: 'var(--c-text-muted)' }">{{ pl.owner_name }}</span>
                </span>
                <span class="shrink-0 text-xs tabular-nums" :style="{ color: 'var(--c-text-faint)' }">{{ pl.count }}</span>
              </button>
            </li>
          </ul>
        </template>
        <p v-else-if="signedIn" class="mt-5 px-3 text-xs leading-relaxed" :style="{ color: 'var(--c-text-faint)' }">
          {{ t('pl.sharedNone') }}
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
              <template v-if="detail.mine">
                <button class="cursor-pointer border-0 bg-transparent p-0 text-sm" :style="{ color: 'var(--c-text-faint)' }"
                        :title="t('pl.rename')" @click="renaming = true; renameDraft = detail.name">✎</button>
                <button class="cursor-pointer border-0 bg-transparent p-0 text-sm" :style="{ color: 'var(--c-text-faint)' }"
                        :title="t('pl.delete')" @click="remove">🗑</button>
              </template>
            </h1>
            <p class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm" :style="{ color: 'var(--c-text-muted)' }">
              <template v-if="!detail.mine">
                <span>{{ t('pl.by', { name: detail.owner_name ?? '?' }) }}</span>
                <span aria-hidden="true">·</span>
              </template>
              <span>{{ t('common.songs', { n: detail.items.length }) }}<template v-if="totalDuration"> · {{ formatTime(totalDuration) }}</template></span>
              <button
                v-if="detail.mine"
                class="cursor-pointer rounded-full border-0 px-2.5 py-0.5 text-xs font-medium"
                :style="detail.public
                  ? { background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }
                  : { background: 'var(--c-surface-2)', color: 'var(--c-text-muted)' }"
                :title="t(detail.public ? 'pl.makePrivateHint' : 'pl.makePublicHint')"
                :aria-pressed="detail.public"
                @click="togglePublic"
              >{{ t(detail.public ? 'pl.public' : 'pl.private') }}</button>
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
            <button
              v-if="!detail.mine && signedIn"
              class="cursor-pointer rounded-full border-0 px-3.5 py-2 text-sm"
              :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
              :title="t('pl.copyHint')"
              @click="copyToMine"
            >{{ t('pl.copy') }}</button>
          </div>
        </div>

        <p v-if="toast" class="mt-3 rounded-lg px-3 py-2 text-sm"
           :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }">{{ toast }}</p>

        <ul v-if="detail.items.length" class="mt-4 flex flex-col gap-1.5">
          <li
            v-for="(e, i) in detail.items"
            :key="e.id"
            class="group flex items-center gap-3 rounded-xl px-3 py-2"
            :class="detail.mine ? 'cursor-grab active:cursor-grabbing' : ''"
            :style="{
              background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)',
              ...(dragOver === i && dragFrom !== null && dragFrom !== i ? { outline: '2px solid var(--c-accent)', outlineOffset: '-2px' } : {}),
              ...(dragFrom === i ? { opacity: 0.4 } : {}),
            }"
            :draggable="detail.mine"
            @dragstart="detail.mine && (dragFrom = i)"
            @dragover.prevent="detail.mine && (dragOver = i)"
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
              <button v-if="detail.mine" class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                      :style="{ background: 'var(--c-surface-2)', color: 'var(--c-danger)' }"
                      :title="t('pl.removeItem')" @click="dropItem(e.id)">✕</button>
              <AddToPlaylist v-else :source="{ source: 'playlist_entry', playlist_id: detail.id, entry: e.id }" />
            </div>
          </li>
        </ul>
        <div v-else-if="detail.mine" class="mt-6 rounded-2xl p-6 text-center text-sm"
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
