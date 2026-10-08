<script setup lang="ts">
import { computed } from 'vue'
import type { ChannelNode } from '../api'
import { t } from '../i18n'

// 递归渲染一个频道及其子频道
const props = defineProps<{
  node: ChannelNode
  currentId: number | null
  defaultId: number | null
  busy: boolean
  depth?: number
}>()
const emit = defineEmits<{ move: [id: number]; setDefault: [id: number] }>()

const depth = computed(() => props.depth ?? 0)
const isHere = computed(() => props.node.id === props.currentId)
const isDefault = computed(() => props.node.id === props.defaultId)
// 本频道里的真人数(不含 bot)
const people = computed(() => props.node.users.filter((u) => !u.is_me && !u.is_bot).length)
</script>

<template>
  <li>
    <div
      class="group flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl px-2.5 py-2"
      :style="isHere ? { background: 'var(--c-accent-soft)' } : {}"
    >
      <span class="flex min-w-0 items-center gap-1.5 text-sm font-medium"
            :style="{ color: isHere ? 'var(--c-accent)' : 'var(--c-text)' }">
        <svg viewBox="0 0 24 24" class="h-3.5 w-3.5 shrink-0 opacity-60" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path v-if="depth === 0" d="M3 12h18M3 6h18M3 18h18" />
          <path v-else d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18" />
        </svg>
        <span class="truncate">{{ node.name }}</span>
      </span>
      <span v-if="isDefault" class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
            :style="{ background: 'var(--c-surface-2)', color: 'var(--c-accent)' }" :title="t('ch.defaultTitle')">{{ t('ch.default') }}</span>
      <span v-if="node.temporary" class="text-[10px]" :style="{ color: 'var(--c-text-faint)' }">{{ t('ch.temporary') }}</span>
      <span v-if="people" class="text-[11px] tabular-nums" :style="{ color: 'var(--c-text-faint)' }">{{ people }} 👤</span>

      <span class="ml-auto flex shrink-0 gap-1 opacity-70 transition-opacity group-hover:opacity-100">
        <button
          v-if="!isHere"
          class="cursor-pointer rounded-md border-0 px-2 py-0.5 text-[11px]"
          :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
          :disabled="busy"
          :title="t('ch.moveTitle')"
          @click="emit('move', node.id)"
        >{{ t('ch.move') }}</button>
        <button
          v-if="!isDefault"
          class="cursor-pointer rounded-md border-0 px-2 py-0.5 text-[11px]"
          :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
          :disabled="busy"
          :title="t('ch.setDefaultTitle')"
          @click="emit('setDefault', node.id)"
        >{{ t('ch.setDefault') }}</button>
      </span>

      <!-- 频道里的人 -->
      <div v-if="node.users.length" class="flex w-full flex-wrap gap-1.5 pl-5">
        <span
          v-for="u in node.users"
          :key="u.session"
          class="flex items-center gap-1 rounded-full py-0.5 pr-2 pl-0.5 text-xs"
          :style="{
            background: u.is_me ? 'var(--c-accent)' : 'var(--c-surface-2)',
            color: u.is_me ? 'var(--c-on-accent)' : (u.is_bot ? 'var(--c-text-muted)' : 'var(--c-text)'),
          }"
        >
          <span class="flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold"
                :style="{ background: u.is_me ? 'rgba(255,255,255,0.25)' : 'var(--c-border)' }">
            {{ u.is_me ? '♪' : u.name.charAt(0).toUpperCase() }}
          </span>
          {{ u.name }}
          <span v-if="u.deafened" :title="t('ch.deafened')">🔇</span>
          <span v-else-if="u.muted" class="text-[10px] opacity-70" :title="t('ch.mutedTitle')">{{ t('ch.muted') }}</span>
        </span>
      </div>
    </div>

    <ul v-if="node.children.length" class="ml-4 border-l pl-2" :style="{ borderColor: 'var(--c-border)' }">
      <ChannelTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :current-id="currentId"
        :default-id="defaultId"
        :busy="busy"
        :depth="depth + 1"
        @move="(id: number) => emit('move', id)"
        @set-default="(id: number) => emit('setDefault', id)"
      />
    </ul>
  </li>
</template>
