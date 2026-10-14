<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchQueue, postQueue, type QueueAction, type QueueGroup, type QueueItem } from '../api'
import { formatTime, useStatus } from '../composables/useStatus'
import AddToPlaylist from './AddToPlaylist.vue'
import QueueRow from './QueueRow.vue'
import { t } from '../i18n'

const { status, applyStatus } = useStatus()

const items = ref<QueueItem[]>([])
const busy = ref(false)
const dragFrom = ref<number | null>(null)
const dragOver = ref<number | null>(null)

// ---- 整张歌单一起入队的,折叠成一组 ------------------------------------------

interface Member { item: QueueItem; index: number }
type Row =
  | { kind: 'item'; key: string; member: Member }
  | { kind: 'group'; key: string; group: QueueGroup; members: Member[]; currentPos: number; duration: number }

/** 连续、同一组的条目合成一行;只剩一首的组就当普通单曲显示 */
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  const list = items.value
  let i = 0
  while (i < list.length) {
    const item = list[i]!
    const key = item.group?.key
    let j = i + 1
    if (key) while (j < list.length && list[j]!.group?.key === key) j++
    if (key && j - i > 1) {
      const members = list.slice(i, j).map((it, k) => ({ item: it, index: i + k }))
      out.push({
        kind: 'group',
        key: `g-${key}-${i}`,
        group: item.group!,
        members,
        currentPos: members.findIndex((m) => m.item.is_current),
        duration: members.reduce((sum, m) => sum + (m.item.duration || 0), 0),
      })
    } else {
      for (let k = i; k < j; k++) out.push({ kind: 'item', key: `${list[k]!.id}-${k}`, member: { item: list[k]!, index: k } })
    }
    i = j
  }
  return out
})

// 展开状态按组 key 记(队列推进、下标变化时不会自己收起)
const expanded = ref<Set<string>>(new Set())
const showAll = ref<Set<string>>(new Set())
const PAGE = 100

function toggled(set: Set<string>, key: string) {
  const next = new Set(set)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}
const toggleExpanded = (key: string) => (expanded.value = toggled(expanded.value, key))
const toggleShowAll = (key: string) => (showAll.value = toggled(showAll.value, key))

/** 展开后默认只渲染 100 首(从正在播的附近开始),上千首的歌单不会卡页面 */
function visibleMembers(row: Extract<Row, { kind: 'group' }>): Member[] {
  if (showAll.value.has(row.group.key) || row.members.length <= PAGE) return row.members
  const start = row.currentPos > PAGE / 2 ? row.currentPos - 10 : 0
  return row.members.slice(start, start + PAGE)
}

const nextIndex = computed(() => (status.value?.current_index ?? -1) + 1)

function onDragStart(i: number, e: DragEvent) {
  dragFrom.value = i
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', String(i))
  }
}

function onDragOver(i: number, e: DragEvent) {
  e.preventDefault()
  dragOver.value = i
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
}

async function onDrop(i: number) {
  const from = dragFrom.value
  dragFrom.value = null
  dragOver.value = null
  if (from === null || from === i) return
  // optimistic reorder so the row lands immediately
  const moved = items.value.splice(from, 1)[0]!
  items.value.splice(i, 0, moved)
  await act({ action: 'move', index: from, to: i })
}

function onDragEnd() {
  dragFrom.value = null
  dragOver.value = null
}

async function reload() {
  try {
    const q = await fetchQueue()
    items.value = q.items
  } catch {
    /* the status poll already surfaces connectivity errors */
  }
}

// refresh whenever the playlist version changes (covers external edits too)
watch(() => status.value?.version, reload, { immediate: true })

let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(reload, 10000)
})
onBeforeUnmount(() => clearInterval(timer))

async function act(body: QueueAction) {
  if (busy.value) return
  busy.value = true
  try {
    applyStatus(await postQueue(body))
    await reload()
  } catch {
    await reload()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section v-if="items.length" class="mx-auto w-full max-w-3xl px-4 pb-12">
    <div class="mb-3 flex items-center justify-between">
      <h2 class="text-sm font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-muted)' }">
        {{ t('queue.title', { n: items.length }) }}
      </h2>
      <div class="flex items-center gap-1.5">
        <AddToPlaylist :source="{ source: 'all_queue' }" :label="t('queue.save')" />
        <button
          class="cursor-pointer rounded-full border-0 px-3 py-1 text-xs"
          :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text-muted)' }"
          @click="act({ action: 'clear' })"
        >{{ t('queue.clear') }}</button>
      </div>
    </div>

    <ul class="flex flex-col gap-1.5">
      <template v-for="row in rows" :key="row.key">
        <QueueRow
          v-if="row.kind === 'item'"
          :item="row.member.item"
          :index="row.member.index"
          :is-next="row.member.index === nextIndex"
          :drag-over="dragOver === row.member.index && dragFrom !== null && dragFrom !== row.member.index"
          :dragging="dragFrom === row.member.index"
          @act="act"
          @dragstart="onDragStart(row.member.index, $event)"
          @dragover="onDragOver(row.member.index, $event)"
          @drop.prevent="onDrop(row.member.index)"
          @dragend="onDragEnd"
        />

        <!-- 一整张歌单:默认折叠成一行 -->
        <li
          v-else
          class="overflow-hidden rounded-xl"
          :style="{
            background: row.currentPos >= 0 ? 'var(--c-accent-soft)' : 'var(--c-surface)',
            boxShadow: 'var(--shadow-1)',
          }"
        >
          <div
            class="group flex cursor-pointer items-center gap-3 px-3 py-2.5"
            role="button"
            :aria-expanded="expanded.has(row.group.key)"
            @click="toggleExpanded(row.group.key)"
          >
            <Icon
              :icon="expanded.has(row.group.key) ? 'chevron-down' : 'chevron-right'"
              class="w-3 shrink-0 text-xs"
              :style="{ color: 'var(--c-text-faint)' }"
            />
            <div
              class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg"
              :style="{ background: 'var(--c-surface-2)', color: 'var(--c-accent)' }"
            >
              <Icon :icon="row.group.kind === 'url_playlist' ? 'globe' : 'list-ul'" />
            </div>
            <div class="min-w-0 flex-1">
              <p class="flex min-w-0 items-center gap-2 text-sm font-medium">
                <span class="truncate" :title="row.group.name">{{ row.group.name || t('common.untitled') }}</span>
                <span
                  class="shrink-0 rounded-full px-1.5 py-px text-[10px] font-medium"
                  :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text-muted)' }"
                >{{ t(row.group.kind === 'url_playlist' ? 'queue.groupKindOnline' : 'queue.groupKindPlaylist') }}</span>
              </p>
              <p class="truncate text-xs" :style="{ color: 'var(--c-text-muted)' }">
                <template v-if="row.currentPos >= 0">
                  <Icon icon="caret-right" class="mr-1" :style="{ color: 'var(--c-accent)' }" />{{ t('queue.groupNow', {
                    i: row.currentPos + 1, n: row.members.length,
                    title: row.members[row.currentPos]!.item.title || t('common.untitled'),
                  }) }}
                </template>
                <template v-else>{{ t('queue.groupSongs', { n: row.members.length, time: formatTime(row.duration) }) }}</template>
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-1 opacity-60 transition-opacity group-hover:opacity-100" @click.stop>
              <button
                v-if="row.currentPos !== 0"
                class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
                :title="t('queue.groupPlay')"
                :aria-label="t('queue.groupPlay')"
                @click="act({ action: 'play', index: row.members[0]!.index })"
              ><Icon icon="play" /></button>
              <button
                class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
                :style="{ background: 'var(--c-surface-2)', color: 'var(--c-danger)' }"
                :title="t('queue.groupRemove')"
                :aria-label="t('queue.groupRemove')"
                @click="act({ action: 'remove_group', group: row.group.key })"
              ><Icon icon="xmark" /></button>
            </div>
          </div>

          <ul v-if="expanded.has(row.group.key)" class="flex flex-col gap-1 px-2 pb-2">
            <QueueRow
              v-for="m in visibleMembers(row)"
              :key="`${m.item.id}-${m.index}`"
              nested
              :item="m.item"
              :index="m.index"
              :is-next="m.index === nextIndex"
              :drag-over="dragOver === m.index && dragFrom !== null && dragFrom !== m.index"
              :dragging="dragFrom === m.index"
              @act="act"
              @dragstart="onDragStart(m.index, $event)"
              @dragover="onDragOver(m.index, $event)"
              @drop.prevent="onDrop(m.index)"
              @dragend="onDragEnd"
            />
            <li v-if="visibleMembers(row).length < row.members.length">
              <button
                class="w-full cursor-pointer rounded-lg border-0 bg-transparent py-1.5 text-xs"
                :style="{ color: 'var(--c-accent)' }"
                @click="toggleShowAll(row.group.key)"
              >{{ t('queue.showAll', { n: row.members.length }) }}</button>
            </li>
          </ul>
        </li>
      </template>
    </ul>
  </section>
</template>
