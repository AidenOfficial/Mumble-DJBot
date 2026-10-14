import { ref } from 'vue'
import { fetchFavorites, setFavorite, type PlaylistSource } from '../api'
import { useMe } from './useMe'

// 模块级单例:所有 ♥ 按钮共享同一份"收藏了哪些歌"
const ids = ref<Set<string>>(new Set())
const playlistId = ref<number | null>(null)
const busy = ref<Set<string>>(new Set())
let started = false

async function reloadFavorites() {
  try {
    const state = await fetchFavorites()
    ids.value = new Set(state.item_ids)
    playlistId.value = state.playlist_id
  } catch {
    /* 没有身份 / 连不上:♥ 都显示成空心 */
  }
}

/** 切换收藏。itemId 用来立刻更新按钮状态,source 告诉后端是哪首歌。 */
async function toggleFavorite(itemId: string, source: PlaylistSource): Promise<boolean> {
  if (busy.value.has(itemId)) return true
  const want = !ids.value.has(itemId)
  busy.value = new Set(busy.value).add(itemId)
  const optimistic = new Set(ids.value)
  if (want) optimistic.add(itemId)
  else optimistic.delete(itemId)
  ids.value = optimistic
  try {
    const state = await setFavorite(source, want)
    ids.value = new Set(state.item_ids)
    playlistId.value = state.playlist_id
    // 歌单页侧栏的数量(第一次收藏还会自动建"我喜欢的音乐")跟着刷新
    useMe().reloadPlaylists()
    return true
  } catch {
    await reloadFavorites()
    return false
  } finally {
    const next = new Set(busy.value)
    next.delete(itemId)
    busy.value = next
  }
}

export function useFavorites() {
  if (!started) {
    started = true
    const { me, reloadMe } = useMe()
    // 等 /api/me 回来再判断有没有身份,匿名访客不用请求
    ;(me.value ? Promise.resolve() : reloadMe()).then(() => {
      if (me.value?.can_have_playlists) reloadFavorites()
    })
  }
  return { favoriteIds: ids, favoritesPlaylistId: playlistId, favoriteBusy: busy, reloadFavorites, toggleFavorite }
}
