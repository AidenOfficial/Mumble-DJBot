<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchQueue, postQueue, thumbnailUrl, type QueueAction, type QueueItem } from '../api'
import { formatTime, useStatus } from '../composables/useStatus'

const { status, applyStatus } = useStatus()

const items = ref<QueueItem[]>([])
const busy = ref(false)

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
        Queue · {{ items.length }}
      </h2>
      <button
        class="cursor-pointer rounded-full border-0 px-3 py-1 text-xs"
        :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text-muted)' }"
        @click="act({ action: 'clear' })"
      >Clear all</button>
    </div>

    <ul class="flex flex-col gap-1.5">
      <li
        v-for="(item, i) in items"
        :key="`${item.id}-${i}`"
        class="group flex items-center gap-3 rounded-xl px-3 py-2"
        :style="item.is_current
          ? { background: 'var(--c-accent-soft)' }
          : { background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }"
      >
        <!-- art -->
        <div class="h-10 w-10 shrink-0 overflow-hidden rounded-lg" :style="{ background: 'var(--c-surface-2)' }">
          <img v-if="item.has_thumbnail" :src="thumbnailUrl(item.id)" alt="" class="h-full w-full object-cover" loading="lazy" />
          <div v-else class="flex h-full w-full items-center justify-center opacity-40">
            <svg viewBox="0 0 24 24" class="h-5 w-5" fill="currentColor"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" /></svg>
          </div>
        </div>

        <!-- text -->
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-medium" :title="item.title">
            <span v-if="item.is_current" :style="{ color: 'var(--c-accent)' }">▸ </span>{{ item.title || 'Untitled' }}
          </p>
          <p class="truncate text-xs" :style="{ color: 'var(--c-text-muted)' }">
            {{ item.type }}<span v-if="item.duration"> · {{ formatTime(item.duration) }}</span>
          </p>
        </div>

        <!-- actions -->
        <div class="flex shrink-0 items-center gap-1 opacity-60 transition-opacity group-hover:opacity-100">
          <button
            v-if="!item.is_current"
            class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
            :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
            title="Play now"
            @click="act({ action: 'play', index: i })"
          >▶</button>
          <button
            v-if="!item.is_current && i !== (status?.current_index ?? -1) + 1"
            class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
            :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
            title="Play next"
            @click="act({ action: 'top', index: i })"
          >⤴</button>
          <button
            class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
            :style="{ background: 'var(--c-surface-2)', color: 'var(--c-danger)' }"
            title="Remove"
            @click="act({ action: 'remove', index: i })"
          >✕</button>
        </div>
      </li>
    </ul>
  </section>
</template>
