<script setup lang="ts">
import { thumbnailUrl, type QueueAction, type QueueItem } from '../api'
import { formatTime } from '../composables/useStatus'
import AddToPlaylist from './AddToPlaylist.vue'
import FavButton from './FavButton.vue'
import { t, typeLabel } from '../i18n'

// 队列里的一行。拖拽事件由父组件挂在根元素上(属性透传)。
const props = defineProps<{
  item: QueueItem
  index: number
  /** 已经是"下一首"了,不用再显示"下一首播放" */
  isNext: boolean
  dragOver: boolean
  dragging: boolean
  /** 歌单分组里的行:缩进、去掉阴影 */
  nested?: boolean
}>()

const emit = defineEmits<{ act: [body: QueueAction] }>()
</script>

<template>
  <li
    class="group flex cursor-grab items-center gap-3 rounded-xl px-3 py-2 active:cursor-grabbing"
    :style="{
      ...(props.item.is_current
        ? { background: 'var(--c-accent-soft)' }
        : props.nested
          ? { background: 'var(--c-surface)' }
          : { background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }),
      ...(props.dragOver ? { outline: '2px solid var(--c-accent)', outlineOffset: '-2px' } : {}),
      ...(props.dragging ? { opacity: 0.4 } : {}),
    }"
    draggable="true"
  >
    <Icon icon="grip-vertical" class="shrink-0 select-none text-xs" :style="{ color: 'var(--c-text-faint)' }" aria-hidden="true" />
    <!-- art -->
    <div class="h-10 w-10 shrink-0 overflow-hidden rounded-lg" :style="{ background: 'var(--c-surface-2)' }">
      <img v-if="props.item.has_thumbnail" :src="thumbnailUrl(props.item.id)" alt="" class="h-full w-full object-cover" loading="lazy" />
      <div v-else class="flex h-full w-full items-center justify-center opacity-40">
        <Icon icon="music" />
      </div>
    </div>

    <!-- text -->
    <div class="min-w-0 flex-1">
      <p class="truncate text-sm font-medium" :title="props.item.title">
        <Icon v-if="props.item.is_current" icon="caret-right" class="mr-1" :style="{ color: 'var(--c-accent)' }" />{{ props.item.title || t('common.untitled') }}
      </p>
      <p class="truncate text-xs" :style="{ color: 'var(--c-text-muted)' }">
        {{ typeLabel(props.item.type) }}<span v-if="props.item.duration"> · {{ formatTime(props.item.duration) }}</span>
        <span v-if="props.item.download && !props.item.is_current" :style="{ color: 'var(--c-accent)' }">
          · <Icon icon="download" /> {{ props.item.download.stage === 'downloading' ? `${Math.round(props.item.download.progress * 100)}%` : t('queue.preparing') }}</span>
      </p>
    </div>

    <!-- actions -->
    <div class="flex shrink-0 items-center gap-1 opacity-60 transition-opacity group-hover:opacity-100">
      <button
        v-if="!props.item.is_current"
        class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
        :title="t('common.playNow')"
        :aria-label="t('common.playNow')"
        @click="emit('act', { action: 'play', index: props.index })"
      ><Icon icon="play" /></button>
      <button
        v-if="!props.item.is_current && !props.isNext"
        class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
        :title="t('common.playNext')"
        :aria-label="t('common.playNext')"
        @click="emit('act', { action: 'top', index: props.index })"
      ><Icon icon="arrow-turn-up" /></button>
      <FavButton :item-id="props.item.id" :source="{ source: 'library', item_id: props.item.id }" />
      <AddToPlaylist :source="{ source: 'queue', index: props.index }" />
      <button
        class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-danger)' }"
        :title="t('common.remove')"
        :aria-label="t('common.remove')"
        @click="emit('act', { action: 'remove', index: props.index })"
      ><Icon icon="xmark" /></button>
    </div>
  </li>
</template>
