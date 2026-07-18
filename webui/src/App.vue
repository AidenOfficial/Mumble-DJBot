<script setup lang="ts">
import { onMounted, ref } from 'vue'
import NowPlaying from './components/NowPlaying.vue'
import QueueList from './components/QueueList.vue'
import SearchPage from './components/SearchPage.vue'

type Theme = 'light' | 'dark' | 'auto'
const theme = ref<Theme>('auto')

type View = 'home' | 'search'
const view = ref<View>('home')

function applyTheme(t: Theme) {
  theme.value = t
  const root = document.documentElement
  if (t === 'auto') {
    root.removeAttribute('data-theme')
    localStorage.removeItem('theme')
  } else {
    root.setAttribute('data-theme', t)
    localStorage.setItem('theme', t)
  }
}

function cycleTheme() {
  const order: Theme[] = ['auto', 'light', 'dark']
  applyTheme(order[(order.indexOf(theme.value) + 1) % order.length]!)
}

onMounted(() => {
  const saved = localStorage.getItem('theme')
  if (saved === 'light' || saved === 'dark') applyTheme(saved)
})
</script>

<template>
  <div class="flex min-h-full flex-col">
    <header
      class="sticky top-0 z-10 border-b backdrop-blur"
      :style="{ borderColor: 'var(--c-border)', background: 'color-mix(in srgb, var(--c-bg) 85%, transparent)' }"
    >
      <div class="mx-auto flex w-full max-w-5xl items-center justify-between px-4 py-3">
        <div class="flex items-center gap-2">
          <span
            class="flex h-8 w-8 items-center justify-center rounded-full"
            :style="{ background: 'var(--c-accent)', color: 'var(--c-on-accent)' }"
          >
            <svg viewBox="0 0 24 24" class="h-4 w-4" fill="currentColor" aria-hidden="true">
              <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" />
            </svg>
          </span>
          <span class="text-base font-semibold tracking-tight">DJ Bot</span>
        </div>
        <nav class="flex gap-1 rounded-full p-1" :style="{ background: 'var(--c-surface-2)' }">
          <button
            v-for="v in (['home', 'search'] as const)"
            :key="v"
            class="cursor-pointer rounded-full border-0 px-3.5 py-1.5 text-xs font-medium capitalize"
            :style="view === v
              ? { background: 'var(--c-surface)', color: 'var(--c-text)', boxShadow: 'var(--shadow-1)' }
              : { background: 'transparent', color: 'var(--c-text-muted)' }"
            @click="view = v"
          >{{ v === 'home' ? 'Now Playing' : 'Search' }}</button>
        </nav>
        <button
          class="flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border-0 text-lg"
          :style="{ background: 'var(--c-surface-2)' }"
          :title="`Theme: ${theme}`"
          @click="cycleTheme"
        >
          <span v-if="theme === 'light'">☀️</span>
          <span v-else-if="theme === 'dark'">🌙</span>
          <span v-else>🌗</span>
        </button>
      </div>
    </header>

    <main class="flex-1">
      <template v-if="view === 'home'">
        <NowPlaying />
        <QueueList />
      </template>
      <SearchPage v-else />
    </main>
  </div>
</template>
