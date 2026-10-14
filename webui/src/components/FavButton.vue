<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PlaylistSource } from '../api'
import { useMe } from '../composables/useMe'
import { useFavorites } from '../composables/useFavorites'
import { t } from '../i18n'

// 一键收藏到"我喜欢的音乐"(和聊天里的 !fav 同一张歌单)
const props = withDefaults(defineProps<{ itemId: string; source: PlaylistSource; label?: string }>(), { label: '' })

const { me } = useMe()
const { favoriteIds, favoriteBusy, toggleFavorite } = useFavorites()

const on = computed(() => !!props.itemId && favoriteIds.value.has(props.itemId))
const failed = ref(false)

async function toggle() {
  if (!props.itemId) return
  failed.value = !(await toggleFavorite(props.itemId, props.source))
  if (failed.value) setTimeout(() => (failed.value = false), 2500)
}
</script>

<template>
  <button
    v-if="me?.can_have_playlists && props.itemId"
    class="inline-flex cursor-pointer items-center gap-1 rounded-md border-0 px-2 py-1 text-xs"
    :style="{ background: 'var(--c-surface-2)', color: on ? 'var(--c-danger)' : 'var(--c-text)' }"
    :title="failed ? t('fav.failed') : t(on ? 'fav.remove' : 'fav.add')"
    :aria-label="t(on ? 'fav.remove' : 'fav.add')"
    :aria-pressed="on"
    :disabled="favoriteBusy.has(props.itemId)"
    @click.stop="toggle"
  >
    <Icon :icon="on ? 'heart' : ['far', 'heart']" />
    <span v-if="props.label">{{ props.label }}</span>
  </button>
</template>
