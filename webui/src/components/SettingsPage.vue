<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  fetchChannels, joinChannel, saveChannelSettings, type ChannelNode, type ChannelOverview, type FollowMode,
} from '../api'
import ChannelTreeNode from './ChannelTreeNode.vue'

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
    error.value = "Can't read the channel list from the bot."
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
  if (s.default_channel_id === null) return s.default_path.length ? `${s.default_path.join(' / ')} (not found)` : ''
  return findPath(data.value?.tree ?? null, s.default_channel_id)
})

async function apply(promise: Promise<ChannelOverview>, msg: string) {
  busy.value = true
  try {
    data.value = await promise
    say(msg)
  } catch {
    say('Could not save.')
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
  apply(saveChannelSettings(body), 'Follow mode saved.')
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
    minutes ? `Will pause after ${minutes} empty minute${minutes === 1 ? '' : 's'}.` : 'Auto-pause off.')
}

const MODES: { key: FollowMode; label: string; hint: string }[] = [
  { key: 'off', label: 'Stay put', hint: 'The bot stays where it is until someone moves it.' },
  { key: 'auto', label: 'Follow the crowd', hint: 'When nobody is left in its channel, the bot follows the last person who left — or goes where most people are.' },
  { key: 'user', label: 'Follow someone', hint: 'The bot always moves to the channel of the person you pick. If they go offline it follows the crowd.' },
]
const modeHint = computed(() => MODES.find((m) => m.key === data.value?.settings.follow)?.hint ?? '')
</script>

<template>
  <section class="mx-auto w-full max-w-3xl px-4 py-8">
    <h1 class="text-xl font-semibold">Settings</h1>
    <p v-if="error" class="mt-3 rounded-lg px-3 py-2 text-sm" :style="{ background: 'var(--c-accent-soft)', color: 'var(--c-danger)' }">{{ error }}</p>

    <template v-if="data">
      <!-- 跟随 -->
      <div class="mt-5 rounded-2xl p-5" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <h2 class="text-sm font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-muted)' }">Channel</h2>
        <p class="mt-2 text-sm">
          Now in <b>{{ currentName || '—' }}</b>
          <span :style="{ color: 'var(--c-text-muted)' }"> · default <b>{{ defaultName || 'Root' }}</b></span>
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
          >{{ m.label }}</button>
        </div>
        <p class="mt-2 text-xs" :style="{ color: 'var(--c-text-muted)' }">{{ modeHint }}</p>

        <div v-if="data.settings.follow === 'user'" class="mt-3 flex items-center gap-2 text-sm">
          <label for="follow-user" :style="{ color: 'var(--c-text-muted)' }">Follow</label>
          <select
            id="follow-user"
            :value="data.settings.follow_user"
            class="rounded-full border px-3 py-1.5 text-sm outline-none"
            :style="{ background: 'var(--c-bg)', borderColor: 'var(--c-border)', color: 'var(--c-text)' }"
            :disabled="busy"
            @change="apply(saveChannelSettings({ follow_user: ($event.target as HTMLSelectElement).value }), 'Saved.')"
          >
            <option v-if="!followChoices.length" value="">Nobody online</option>
            <option v-for="n in followChoices" :key="n" :value="n">
              {{ n }}{{ data.online_users.includes(n) ? '' : ' (offline)' }}
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
            @change="apply(saveChannelSettings({ return_home: ($event.target as HTMLInputElement).checked }), 'Saved.')"
          />
          Go back to the default channel when nobody is online
        </label>

        <p v-if="toast" class="mt-3 text-xs" :style="{ color: 'var(--c-success)' }">{{ toast }}</p>
      </div>

      <!-- 没人时自动暂停 -->
      <div class="mt-5 rounded-2xl p-5" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <h2 class="text-sm font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-muted)' }">When nobody is listening</h2>
        <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <label for="idle-minutes">Pause after the bot's channel has been empty for</label>
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
          <span>minutes</span>
        </div>
        <p class="mt-1 text-xs" :style="{ color: 'var(--c-text-muted)' }">
          0 turns it off. Other bots don't count as listeners. With a follow mode on, the bot follows people instead of pausing.
        </p>
        <label class="mt-3 flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            :checked="data.settings.idle_resume"
            :disabled="busy || data.settings.idle_pause_minutes === 0"
            class="h-4 w-4"
            :style="{ accentColor: 'var(--c-accent)' }"
            @change="apply(saveChannelSettings({ idle_resume: ($event.target as HTMLInputElement).checked }), 'Saved.')"
          />
          Resume automatically when someone comes back
        </label>
      </div>

      <!-- 频道树 -->
      <div class="mt-5 rounded-2xl p-3 sm:p-4" :style="{ background: 'var(--c-surface)', boxShadow: 'var(--shadow-1)' }">
        <div class="flex items-center justify-between px-2 pb-2">
          <h2 class="text-sm font-semibold uppercase tracking-wider" :style="{ color: 'var(--c-text-muted)' }">Server channels</h2>
          <span class="text-[11px]" :style="{ color: 'var(--c-text-faint)' }">live · {{ data.online_users.length }} online</span>
        </div>
        <ul v-if="data.tree">
          <ChannelTreeNode
            :node="data.tree"
            :current-id="data.current_channel_id"
            :default-id="data.settings.default_channel_id"
            :busy="busy"
            @move="(id: number) => apply(joinChannel(id), data?.settings.follow === 'off'
              ? 'Moving…' : 'Moving… (follow mode may move it again — set Stay put to keep it there)')"
            @set-default="(id: number) => apply(saveChannelSettings({ default_channel_id: id }), 'Default channel saved.')"
          />
        </ul>
        <p v-else class="px-2 py-4 text-sm" :style="{ color: 'var(--c-text-muted)' }">Not connected to a Mumble server.</p>
      </div>
    </template>
  </section>
</template>
