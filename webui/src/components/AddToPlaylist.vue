<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { addToPlaylist, createPlaylist, type PlaylistSource } from '../api'
import { useMe } from '../composables/useMe'
import { t } from '../i18n'

const props = withDefaults(defineProps<{ source: PlaylistSource; label?: string }>(), { label: '' })

const { me, playlists, reloadPlaylists } = useMe()

const open = ref(false)
const creating = ref(false)
const newName = ref('')
const feedback = ref('')
const root = ref<HTMLElement | null>(null)

function toggle() {
  open.value = !open.value
  feedback.value = ''
  creating.value = false
  if (open.value) reloadPlaylists()
}

async function addTo(id: number) {
  feedback.value = '...'
  try {
    const rv = await addToPlaylist(id, props.source)
    feedback.value = t(rv.added ? 'atp.added' : 'atp.already', { name: rv.playlist.name })
    reloadPlaylists()
    setTimeout(() => (open.value = false), 900)
  } catch {
    feedback.value = t('atp.failed')
  }
}

async function createAndAdd() {
  const name = newName.value.trim()
  if (!name) return
  try {
    const pl = await createPlaylist(name)
    newName.value = ''
    creating.value = false
    await addTo(pl.id)
  } catch {
    feedback.value = t('atp.createFailed')
  }
}

function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <div v-if="me?.can_have_playlists" ref="root" class="relative inline-flex">
    <button
      class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs"
      :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
      :title="t('atp.title')"
      @click.stop="toggle"
    >♡<span v-if="props.label"> {{ props.label }}</span></button>

    <div
      v-if="open"
      class="absolute top-full right-0 z-30 mt-1 w-56 rounded-xl p-1.5 text-left text-sm"
      :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-2)', border: '1px solid var(--c-border)' }"
      @click.stop
    >
      <p class="px-2 pt-1 pb-1.5 text-[11px] font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-faint)' }">
        {{ t('atp.header') }}
      </p>
      <ul class="max-h-56 overflow-y-auto">
        <li v-for="pl in playlists" :key="pl.id">
          <button
            class="flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg border-0 bg-transparent px-2 py-1.5 text-left text-sm hover:opacity-80"
            :style="{ color: 'var(--c-text)' }"
            @click="addTo(pl.id)"
          >
            <span class="truncate">{{ pl.name }}</span>
            <span class="shrink-0 text-xs tabular-nums" :style="{ color: 'var(--c-text-faint)' }">{{ pl.count }}</span>
          </button>
        </li>
      </ul>
      <form v-if="creating" class="flex gap-1 p-1" @submit.prevent="createAndAdd">
        <input
          v-model="newName"
          maxlength="60"
          :placeholder="t('atp.name')"
          class="min-w-0 flex-1 rounded-md border px-2 py-1 text-xs outline-none"
          :style="{ background: 'var(--c-bg)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
          autofocus
        />
        <button
          type="submit"
          class="cursor-pointer rounded-md border-0 px-2 py-1 text-xs font-semibold"
          :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
        >{{ t('atp.add') }}</button>
      </form>
      <button
        v-else
        class="w-full cursor-pointer rounded-lg border-0 bg-transparent px-2 py-1.5 text-left text-sm"
        :style="{ color: 'var(--c-accent)' }"
        @click="creating = true"
      >{{ t('atp.new') }}</button>
      <p v-if="feedback" class="px-2 pt-1 pb-0.5 text-xs" :style="{ color: 'var(--c-text-muted)' }">{{ feedback }}</p>
    </div>
  </div>
</template>
