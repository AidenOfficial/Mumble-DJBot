<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { saveAlias } from '../api'
import { useMe } from '../composables/useMe'

const { me } = useMe()

const open = ref(false)
const draft = ref('')
const saving = ref(false)
const message = ref('')
const root = ref<HTMLElement | null>(null)

const initial = computed(() => (me.value?.display_name || '?').trim().charAt(0).toUpperCase())
const signedIn = computed(() => !!me.value?.identity)

function toggle() {
  open.value = !open.value
  if (open.value) {
    draft.value = me.value?.alias ?? ''
    message.value = ''
  }
}

async function save(alias: string) {
  saving.value = true
  message.value = ''
  try {
    me.value = await saveAlias(alias.trim())
    message.value = alias.trim() ? 'Saved ✓' : 'Alias cleared'
    draft.value = me.value.alias
  } catch (e) {
    const reason = e instanceof Error ? e.message : ''
    message.value =
      reason === 'taken' ? 'Someone already uses that name.'
      : reason === 'invalid' ? 'Max 24 characters, no < > & or quotes.'
      : 'Could not save.'
  } finally {
    saving.value = false
  }
}

function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

const SOURCE_LABEL: Record<string, string> = {
  'cloudflare': 'Cloudflare Access',
  'cloudflare-jwt': 'Cloudflare Access (JWT verified)',
  'web-user': 'Web login',
  'anonymous': 'Not identified',
}
</script>

<template>
  <div ref="root" class="relative">
    <button
      class="flex h-9 max-w-[9rem] cursor-pointer items-center gap-2 rounded-full border-0 py-1 pr-3 pl-1 text-xs font-medium"
      :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
      :title="me?.email || 'Who am I?'"
      @click.stop="toggle"
    >
      <span
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
        :style="signedIn
          ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' }
          : { background: 'var(--c-border)', color: 'var(--c-text-muted)' }"
      >{{ signedIn ? initial : '?' }}</span>
      <span class="truncate">{{ signedIn ? me?.display_name : 'Guest' }}</span>
    </button>

    <div
      v-if="open"
      class="absolute top-full right-0 z-30 mt-2 w-72 rounded-2xl p-4 text-sm"
      :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-2)', border: '1px solid var(--c-border)' }"
      @click.stop
    >
      <template v-if="signedIn">
        <p class="text-xs" :style="{ color: 'var(--c-text-muted)' }">Signed in as</p>
        <p class="truncate font-medium" :title="me?.email || me?.identity || ''">{{ me?.email || me?.identity }}</p>
        <p class="mt-0.5 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">via {{ SOURCE_LABEL[me?.source ?? 'anonymous'] }}</p>

        <label class="mt-4 block text-xs font-medium" :style="{ color: 'var(--c-text-muted)' }" for="alias-input">
          Display name
        </label>
        <p class="text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
          Shown in Mumble when you add songs, and in Stats.
        </p>
        <form class="mt-2 flex gap-2" @submit.prevent="save(draft)">
          <input
            id="alias-input"
            v-model="draft"
            maxlength="24"
            :placeholder="me?.email?.split('@')[0] ?? 'alias'"
            class="min-w-0 flex-1 rounded-lg border px-3 py-1.5 text-sm outline-none"
            :style="{ background: 'var(--c-bg)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
          />
          <button
            type="submit"
            class="cursor-pointer rounded-lg border-0 px-3 py-1.5 text-xs font-semibold"
            :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
            :disabled="saving"
          >Save</button>
        </form>
        <div class="mt-2 flex items-center justify-between">
          <span class="text-xs" :style="{ color: message.endsWith('✓') || message === 'Alias cleared' ? 'var(--c-success)' : 'var(--c-danger)' }">{{ message }}</span>
          <button
            v-if="me?.alias"
            class="cursor-pointer border-0 bg-transparent p-0 text-xs underline"
            :style="{ color: 'var(--c-text-muted)' }"
            @click="save('')"
          >Clear alias</button>
        </div>
      </template>
      <template v-else>
        <p class="font-medium">You're browsing as a guest</p>
        <p class="mt-1 text-xs leading-relaxed" :style="{ color: 'var(--c-text-muted)' }">
          No Cloudflare Access identity reached the bot, so songs you add are credited to
          "Remote Control" and personal playlists are off. Open the bot through its
          Access-protected address to sign in.
        </p>
      </template>
    </div>
  </div>
</template>
