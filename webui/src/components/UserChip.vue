<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { createBindCode, fetchMe, saveAlias, unbindMumble } from '../api'
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

// ---- Mumble 绑定:生成一次性码,等用户在 Mumble 里发 !bind ----
const bind = ref<{ code: string; command: string; expires_at: number } | null>(null)
const bindMsg = ref('')
const copied = ref(false)
let bindPoll: ReturnType<typeof setInterval> | undefined

async function startBind() {
  bindMsg.value = ''
  try {
    bind.value = await createBindCode()
    clearInterval(bindPoll)
    bindPoll = setInterval(async () => {
      if (!bind.value || Date.now() / 1000 > bind.value.expires_at) {
        clearInterval(bindPoll)
        if (bind.value) bindMsg.value = 'Code expired — generate a new one.'
        bind.value = null
        return
      }
      try {
        const fresh = await fetchMe()
        if (fresh.mumble) {
          me.value = fresh
          bind.value = null
          bindMsg.value = `Linked to ${fresh.mumble.mumble_name} ✓`
          clearInterval(bindPoll)
        }
      } catch { /* 下一轮再试 */ }
    }, 3000)
  } catch {
    bindMsg.value = 'Could not create a code.'
  }
}

async function copyCommand() {
  if (!bind.value) return
  try {
    await navigator.clipboard.writeText(bind.value.command)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch { /* 不支持剪贴板就手动抄 */ }
}

async function unlink() {
  if (!confirm('Unlink your Mumble account?')) return
  me.value = await unbindMumble()
  bindMsg.value = 'Unlinked.'
}

onBeforeUnmount(() => clearInterval(bindPoll))

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

        <!-- Mumble 账号绑定 -->
        <div class="mt-4 border-t pt-3" :style="{ borderColor: 'var(--c-border)' }">
          <p class="text-xs font-medium" :style="{ color: 'var(--c-text-muted)' }">Mumble account</p>
          <template v-if="me?.mumble">
            <div class="mt-1 flex items-center justify-between">
              <span class="text-sm">🎧 {{ me.mumble.mumble_name }}</span>
              <button class="cursor-pointer border-0 bg-transparent p-0 text-xs underline"
                      :style="{ color: 'var(--c-text-muted)' }" @click="unlink">Unlink</button>
            </div>
            <p class="mt-1 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
              In Mumble: <b>!mylist</b> lists your playlists, <b>!fav</b> saves the current song.
            </p>
          </template>
          <template v-else-if="bind">
            <p class="mt-1 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">Send this to the bot in Mumble (a private message works best):</p>
            <button
              class="mt-1.5 flex w-full cursor-pointer items-center justify-between rounded-lg border-0 px-3 py-2 font-mono text-base tracking-wider"
              :style="{ background: 'var(--c-bg)', color: 'var(--c-text)' }"
              title="Copy"
              @click="copyCommand"
            >{{ bind.command }}<span class="font-sans text-[11px]" :style="{ color: 'var(--c-text-faint)' }">{{ copied ? 'Copied ✓' : 'Copy' }}</span></button>
            <p class="mt-1 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">Valid for 10 minutes · waiting for you…</p>
          </template>
          <button
            v-else
            class="mt-1.5 w-full cursor-pointer rounded-lg border-0 px-3 py-1.5 text-xs font-medium"
            :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
            @click="startBind"
          >Link Mumble account</button>
          <p v-if="bindMsg" class="mt-1 text-xs" :style="{ color: bindMsg.includes('✓') ? 'var(--c-success)' : 'var(--c-text-muted)' }">{{ bindMsg }}</p>
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
