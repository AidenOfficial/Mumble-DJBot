<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { createBindCode, fetchMe, saveAlias, unbindMumble } from '../api'
import { useMe } from '../composables/useMe'
import { t, tParts } from '../i18n'
import type { Key } from '../i18n/en'

const { me } = useMe()

const open = ref(false)
const draft = ref('')
const saving = ref(false)
const message = ref('')
const messageOk = ref(false) // 颜色靠这个判断,不再看文案里有没有 ✓
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
    message.value = t(alias.trim() ? 'me.saved' : 'me.cleared')
    messageOk.value = true
    draft.value = me.value.alias
  } catch (e) {
    const reason = e instanceof Error ? e.message : ''
    message.value = t(reason === 'taken' ? 'me.taken' : reason === 'invalid' ? 'me.invalid' : 'common.saveFailed')
    messageOk.value = false
  } finally {
    saving.value = false
  }
}

// ---- Mumble 绑定:生成一次性码,等用户在 Mumble 里发 !bind ----
const bind = ref<{ code: string; command: string; expires_at: number } | null>(null)
const bindMsg = ref('')
const bindOk = ref(false)
const copied = ref(false)
let bindPoll: ReturnType<typeof setInterval> | undefined

async function startBind() {
  bindMsg.value = ''
  bindOk.value = false
  try {
    bind.value = await createBindCode()
    clearInterval(bindPoll)
    bindPoll = setInterval(async () => {
      if (!bind.value || Date.now() / 1000 > bind.value.expires_at) {
        clearInterval(bindPoll)
        if (bind.value) bindMsg.value = t('me.codeExpired')
        bind.value = null
        return
      }
      try {
        const fresh = await fetchMe()
        if (fresh.mumble) {
          me.value = fresh
          bind.value = null
          bindMsg.value = t('me.linked', { name: fresh.mumble.mumble_name })
          bindOk.value = true
          clearInterval(bindPoll)
        }
      } catch { /* 下一轮再试 */ }
    }, 3000)
  } catch {
    bindMsg.value = t('me.codeFailed')
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
  if (!confirm(t('me.confirmUnlink'))) return
  me.value = await unbindMumble()
  bindMsg.value = t('me.unlinked')
  bindOk.value = false
}

onBeforeUnmount(() => clearInterval(bindPoll))

function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

const SOURCE_LABEL: Record<string, Key> = {
  'cloudflare': 'me.src.cloudflare',
  'cloudflare-jwt': 'me.src.cloudflareJwt',
  'web-user': 'me.src.webUser',
  'anonymous': 'me.src.anonymous',
}
</script>

<template>
  <div ref="root" class="relative">
    <button
      class="flex h-9 max-w-[9rem] cursor-pointer items-center gap-2 rounded-full border-0 py-1 pr-3 pl-1 text-xs font-medium"
      :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
      :title="me?.email || t('me.whoami')"
      @click.stop="toggle"
    >
      <span
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
        :style="signedIn
          ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' }
          : { background: 'var(--c-border)', color: 'var(--c-text-muted)' }"
      >{{ signedIn ? initial : '?' }}</span>
      <span class="truncate">{{ signedIn ? me?.display_name : t('me.guest') }}</span>
    </button>

    <div
      v-if="open"
      class="absolute top-full right-0 z-30 mt-2 w-72 rounded-2xl p-4 text-sm"
      :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-2)', border: '1px solid var(--c-border)' }"
      @click.stop
    >
      <template v-if="signedIn">
        <p class="text-xs" :style="{ color: 'var(--c-text-muted)' }">{{ t('me.signedInAs') }}</p>
        <p class="truncate font-medium" :title="me?.email || me?.identity || ''">{{ me?.email || me?.identity }}</p>
        <p class="mt-0.5 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">{{ t('me.via', { source: t(SOURCE_LABEL[me?.source ?? 'anonymous'] ?? 'me.src.anonymous') }) }}</p>

        <label class="mt-4 block text-xs font-medium" :style="{ color: 'var(--c-text-muted)' }" for="alias-input">
          {{ t('me.displayName') }}
        </label>
        <p class="text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
          {{ t('me.displayNameHint') }}
        </p>
        <form class="mt-2 flex gap-2" @submit.prevent="save(draft)">
          <input
            id="alias-input"
            v-model="draft"
            maxlength="24"
            :placeholder="me?.email?.split('@')[0] ?? t('me.aliasPlaceholder')"
            class="min-w-0 flex-1 rounded-lg border px-3 py-1.5 text-sm outline-none"
            :style="{ background: 'var(--c-bg)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
          />
          <button
            type="submit"
            class="cursor-pointer rounded-lg border-0 px-3 py-1.5 text-xs font-semibold"
            :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
            :disabled="saving"
          >{{ t('common.save') }}</button>
        </form>
        <div class="mt-2 flex items-center justify-between">
          <span class="text-xs" :style="{ color: messageOk ? 'var(--c-success)' : 'var(--c-danger)' }">{{ message }}</span>
          <button
            v-if="me?.alias"
            class="cursor-pointer border-0 bg-transparent p-0 text-xs underline"
            :style="{ color: 'var(--c-text-muted)' }"
            @click="save('')"
          >{{ t('me.clear') }}</button>
        </div>

        <!-- Mumble 账号绑定 -->
        <div class="mt-4 border-t pt-3" :style="{ borderColor: 'var(--c-border)' }">
          <p class="text-xs font-medium" :style="{ color: 'var(--c-text-muted)' }">{{ t('me.mumble') }}</p>
          <template v-if="me?.mumble">
            <div class="mt-1 flex items-center justify-between">
              <span class="text-sm">🎧 {{ me.mumble.mumble_name }}</span>
              <button class="cursor-pointer border-0 bg-transparent p-0 text-xs underline"
                      :style="{ color: 'var(--c-text-muted)' }" @click="unlink">{{ t('me.unlink') }}</button>
            </div>
            <p class="mt-1 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">
              <template v-for="(p, i) in tParts('me.mumbleHint', { mylist: '!mylist', fav: '!fav' })" :key="i"><b
                v-if="p.param">{{ p.text }}</b><template v-else>{{ p.text }}</template></template>
            </p>
          </template>
          <template v-else-if="bind">
            <p class="mt-1 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">{{ t('me.sendThis') }}</p>
            <button
              class="mt-1.5 flex w-full cursor-pointer items-center justify-between rounded-lg border-0 px-3 py-2 font-mono text-base tracking-wider"
              :style="{ background: 'var(--c-bg)', color: 'var(--c-text)' }"
              :title="t('me.copy')"
              @click="copyCommand"
            >{{ bind.command }}<span class="font-sans text-[11px]" :style="{ color: 'var(--c-text-faint)' }">{{ t(copied ? 'me.copied' : 'me.copy') }}</span></button>
            <p class="mt-1 text-[11px]" :style="{ color: 'var(--c-text-faint)' }">{{ t('me.validFor') }}</p>
          </template>
          <button
            v-else
            class="mt-1.5 w-full cursor-pointer rounded-lg border-0 px-3 py-1.5 text-xs font-medium"
            :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
            @click="startBind"
          >{{ t('me.link') }}</button>
          <p v-if="bindMsg" class="mt-1 text-xs" :style="{ color: bindOk ? 'var(--c-success)' : 'var(--c-text-muted)' }">{{ bindMsg }}</p>
        </div>
      </template>
      <template v-else>
        <p class="font-medium">{{ t('me.guestTitle') }}</p>
        <p class="mt-1 text-xs leading-relaxed" :style="{ color: 'var(--c-text-muted)' }">{{ t('me.guestHint') }}</p>
      </template>
    </div>
  </div>
</template>
