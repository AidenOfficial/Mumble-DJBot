<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  fetchChannels, joinChannel, saveChannelSettings, type ChannelNode, type ChannelOverview, type FollowMode,
} from '../api'
import ChannelTreeNode from './ChannelTreeNode.vue'
import { t, tParts } from '../i18n'
import type { Key } from '../i18n/en'

const data = ref<ChannelOverview | null>(null)
const error = ref('')
const busy = ref(false)
const toast = ref('')

let toastTimer: ReturnType<typeof setTimeout> | undefined
function say(msg: string) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 3000)
}

async function reload() {
  try {
    data.value = await fetchChannels()
    error.value = ''
  } catch {
    error.value = t('set.readFailed')
  }
}

// 频道里的人随时在变,定时刷新
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  reload()
  timer = setInterval(reload, 4000)
})
onBeforeUnmount(() => clearInterval(timer))

function findPath(node: ChannelNode | null, id: number | null): string {
  if (!node || id === null) return ''
  if (node.id === id) return node.path.length ? node.path.join(' / ') : node.name
  for (const c of node.children) {
    const p = findPath(c, id)
    if (p) return p
  }
  return ''
}
const currentName = computed(() => findPath(data.value?.tree ?? null, data.value?.current_channel_id ?? null))
const defaultName = computed(() => {
  const s = data.value?.settings
  if (!s) return ''
  if (s.default_channel_id === null) return s.default_path.length ? t('set.notFound', { path: s.default_path.join(' / ') }) : ''
  return findPath(data.value?.tree ?? null, s.default_channel_id)
})

async function apply(promise: Promise<ChannelOverview>, msg: string) {
  busy.value = true
  try {
    data.value = await promise
    say(msg)
  } catch {
    say(t('common.saveFailed'))
  } finally {
    busy.value = false
  }
}

function setFollow(mode: FollowMode) {
  const body: Parameters<typeof saveChannelSettings>[0] = { follow: mode }
  // 切到"跟随某人"时,默认选第一个在线的人
  if (mode === 'user' && !data.value?.settings.follow_user && data.value?.online_users.length) {
    body.follow_user = data.value.online_users[0]
  }
  apply(saveChannelSettings(body), t('set.followSaved'))
}

// 跟随对象离线时也保留在下拉里,免得一刷新就被换掉
const followChoices = computed(() => {
  const list = [...(data.value?.online_users ?? [])]
  const cur = data.value?.settings.follow_user
  if (cur && !list.includes(cur)) list.unshift(cur)
  return list
})

function onIdleMinutes(e: Event) {
  const input = e.target as HTMLInputElement
  const minutes = Math.max(0, Math.min(1440, Math.round(Number(input.value) || 0)))
  input.value = String(minutes)
  apply(saveChannelSettings({ idle_pause_minutes: minutes }),
    minutes ? t('set.idleSaved', { n: minutes }) : t('set.idleOff'))
}

const MODES: { key: FollowMode; label: Key; hint: Key }[] = [
  { key: 'off', label: 'set.offLabel', hint: 'set.offHint' },
  { key: 'auto', label: 'set.autoLabel', hint: 'set.autoHint' },
  { key: 'user', label: 'set.userLabel', hint: 'set.userHint' },
]
const modeHint = computed(() => {
  const m = MODES.find((m) => m.key === data.value?.settings.follow)
  return m ? t(m.hint) : ''
})
</script>

<template>
  <section class="mx-auto w-full max-w-3xl px-4 py-8">
    <h1 class="text-xl font-semibold">{{ t('set.title') }}</h1>
    <p v-if="error" class="mt-3 rounded-lg px-3 py-2 text-sm" :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-danger)' }">{{ error }}</p>

    <template v-if="data">
      <!-- 跟随 -->
      <div class="mt-5 rounded-2xl p-5" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <h2 class="text-sm font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-muted)' }">{{ t('set.channel') }}</h2>
        <p class="mt-2 text-sm">
          <template v-for="(p, i) in tParts('set.nowIn', { cur: currentName || '—', def: defaultName || t('set.root') })" :key="i"><b
            v-if="p.param">{{ p.text }}</b><template v-else>{{ p.text }}</template></template>
        </p>

        <div class="mt-4 grid grid-cols-3 gap-1 rounded-2xl p-1 sm:inline-grid sm:rounded-full" :style="{ background: 'var(--c-surface-2)' }">
          <button
            v-for="m in MODES"
            :key="m.key"
            class="cursor-pointer rounded-xl border-0 px-2 py-1.5 text-xs font-medium sm:rounded-full sm:px-3.5"
            :style="data.settings.follow === m.key
              ? { background: 'var(--c-accent)', color: 'var(--c-on-accent)' }
              : { background: 'transparent', color: 'var(--c-text-muted)' }"
            :disabled="busy"
            @click="setFollow(m.key)"
          >{{ t(m.label) }}</button>
        </div>
        <p class="mt-2 text-xs" :style="{ color: 'var(--c-text-muted)' }">{{ modeHint }}</p>

        <div v-if="data.settings.follow === 'user'" class="mt-3 flex items-center gap-2 text-sm">
          <label for="follow-user" :style="{ color: 'var(--c-text-muted)' }">{{ t('set.follow') }}</label>
          <select
            id="follow-user"
            :value="data.settings.follow_user"
            class="rounded-full border px-3 py-1.5 text-sm outline-none"
            :style="{ background: 'var(--c-bg)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
            :disabled="busy"
            @change="apply(saveChannelSettings({ follow_user: ($event.target as HTMLSelectElement).value }), t('common.saved'))"
          >
            <option v-if="!followChoices.length" value="">{{ t('set.nobody') }}</option>
            <option v-for="n in followChoices" :key="n" :value="n">
              {{ n }}{{ data.online_users.includes(n) ? '' : t('set.offline') }}
            </option>
          </select>
        </div>

        <label v-if="data.settings.follow !== 'off'" class="mt-3 flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            :checked="data.settings.return_home"
            :disabled="busy"
            class="h-4 w-4"
            :style="{ accentColor: 'var(--c-accent)' }"
            @change="apply(saveChannelSettings({ return_home: ($event.target as HTMLInputElement).checked }), t('common.saved'))"
          />
          {{ t('set.returnHome') }}
        </label>

        <p v-if="toast" class="mt-3 text-xs" :style="{ color: 'var(--c-success)' }">{{ toast }}</p>
      </div>

      <!-- 没人时自动暂停 -->
      <div class="mt-5 rounded-2xl p-5" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <h2 class="text-sm font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-muted)' }">{{ t('set.idleTitle') }}</h2>
        <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <label for="idle-minutes">{{ t('set.idleLabel') }}</label>
          <input
            id="idle-minutes"
            type="number"
            min="0"
            max="1440"
            :value="data.settings.idle_pause_minutes"
            :disabled="busy"
            class="w-20 rounded-lg border px-2 py-1 text-sm tabular-nums outline-none"
            :style="{ background: 'var(--c-bg)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
            @change="onIdleMinutes"
          />
          <span>{{ t('set.minutes') }}</span>
        </div>
        <p class="mt-1 text-xs" :style="{ color: 'var(--c-text-muted)' }">
          {{ t('set.idleHint') }}
        </p>
        <label class="mt-3 flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            :checked="data.settings.idle_resume"
            :disabled="busy || data.settings.idle_pause_minutes === 0"
            class="h-4 w-4"
            :style="{ accentColor: 'var(--c-accent)' }"
            @change="apply(saveChannelSettings({ idle_resume: ($event.target as HTMLInputElement).checked }), t('common.saved'))"
          />
          {{ t('set.idleResume') }}
        </label>
      </div>

      <!-- 频道树 -->
      <div class="mt-5 rounded-2xl p-3 sm:p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <div class="flex items-center justify-between px-2 pb-2">
          <h2 class="text-sm font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-muted)' }">{{ t('set.channels') }}</h2>
          <span class="text-[11px]" :style="{ color: 'var(--c-text-faint)' }">{{ t('set.online', { n: data.online_users.length }) }}</span>
        </div>
        <ul v-if="data.tree">
          <ChannelTreeNode
            :node="data.tree"
            :current-id="data.current_channel_id"
            :default-id="data.settings.default_channel_id"
            :busy="busy"
            @move="(id: number) => apply(joinChannel(id), t(data?.settings.follow === 'off' ? 'set.moving' : 'set.movingFollow'))"
            @set-default="(id: number) => apply(saveChannelSettings({ default_channel_id: id }), t('set.defaultSaved'))"
          />
        </ul>
        <p v-else class="px-2 py-4 text-sm" :style="{ color: 'var(--c-text-muted)' }">{{ t('set.notConnected') }}</p>
      </div>
    </template>
  </section>
</template>
