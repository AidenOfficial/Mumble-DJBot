import { ref } from 'vue'
import { fetchMe, fetchPlaylists, type Me, type PlaylistSummary } from '../api'

// 模块级单例:头部用户标识、各页面的"加入歌单"菜单共享同一份数据。
const me = ref<Me | null>(null)
const playlists = ref<PlaylistSummary[]>([])
let started = false

async function reloadMe() {
  try {
    me.value = await fetchMe()
  } catch {
    /* 状态轮询那边会提示连接错误 */
  }
}

async function reloadPlaylists() {
  if (!me.value?.can_have_playlists) {
    playlists.value = []
    return
  }
  try {
    playlists.value = await fetchPlaylists()
  } catch {
    /* ignore */
  }
}

export function useMe() {
  if (!started) {
    started = true
    reloadMe().then(reloadPlaylists)
  }
  return { me, playlists, reloadMe, reloadPlaylists }
}
