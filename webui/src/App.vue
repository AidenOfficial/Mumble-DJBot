<script setup lang="ts">
import { onMounted, ref } from 'vue'
import NowPlaying from './components/NowPlaying.vue'
import QueueList from './components/QueueList.vue'
import SearchPage from './components/SearchPage.vue'
import StatsPage from './components/StatsPage.vue'
import LibraryPage from './components/LibraryPage.vue'
import PlaylistsPage from './components/PlaylistsPage.vue'
import UserChip from './components/UserChip.vue'
import CachePage from './components/CachePage.vue'
import SettingsPage from './components/SettingsPage.vue'
import { LOCALES, locale, t } from './i18n'
import type { Key } from './i18n/en'

type Theme = 'light' | 'dark' | 'auto'
const theme = ref<Theme>('auto')
const THEME_KEY: Record<Theme, Key> = { auto: 'theme.auto', light: 'theme.light', dark: 'theme.dark' }

type View = 'home' | 'search' | 'library' | 'lists' | 'stats' | 'cache' | 'settings'
const VIEWS: View[] = ['home', 'search', 'library', 'lists', 'stats', 'cache', 'settings']
const VIEW_LABEL: Record<View, Key> = {
  home: 'nav.home', search: 'nav.search', library: 'nav.library', lists: 'nav.lists', stats: 'nav.stats', cache: 'nav.cache', settings: 'nav.settings',
}
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
      <div class="mx-auto flex w-full max-w-5xl flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-3">
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
        <!-- 窄屏时导航换到第二行并可横向滚动 -->
        <nav
          class="order-last flex w-full gap-1 overflow-x-auto rounded-full p-1 md:order-none md:w-auto"
          :style="{ background: 'var(--c-surface-2)' }"
        >
          <button
            v-for="v in VIEWS"
            :key="v"
            class="shrink-0 cursor-pointer rounded-full border-0 px-3.5 py-1.5 text-xs font-medium whitespace-nowrap"
            :style="view === v
              ? { background: 'var(--c-surface)', color: 'var(--c-text)', boxShadow: 'var(--shadow-1)' }
              : { background: 'transparent', color: 'var(--c-text-muted)' }"
            @click="view = v"
          >{{ t(VIEW_LABEL[v]) }}</button>
        </nav>
        <div class="flex items-center gap-2">
          <UserChip />
          <select
            v-model="locale"
            class="h-9 cursor-pointer rounded-full border-0 px-2.5 text-xs outline-none"
            :style="{ background: 'var(--c-surface-2)', color: 'var(--c-text)' }"
            :title="t('lang.title')"
            :aria-label="t('lang.title')"
          >
            <option v-for="l in LOCALES" :key="l.code" :value="l.code">{{ l.label }}</option>
          </select>
          <button
            class="flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border-0 text-lg"
            :style="{ background: 'var(--c-surface-2)' }"
            :title="t('theme.title', { mode: t(THEME_KEY[theme]) })"
            @click="cycleTheme"
          >
            <span v-if="theme === 'light'">☀️</span>
            <span v-else-if="theme === 'dark'">🌙</span>
            <span v-else>🌗</span>
          </button>
        </div>
      </div>
    </header>

    <main class="flex-1">
      <template v-if="view === 'home'">
        <NowPlaying />
        <QueueList />
      </template>
      <SearchPage v-else-if="view === 'search'" />
      <LibraryPage v-else-if="view === 'library'" />
      <PlaylistsPage v-else-if="view === 'lists'" />
      <CachePage v-else-if="view === 'cache'" />
      <SettingsPage v-else-if="view === 'settings'" />
      <StatsPage v-else />
    </main>
  </div>
</template>
