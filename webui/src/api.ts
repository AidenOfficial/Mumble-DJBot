// Thin typed client for the bot's JSON API (web_api.py).

export interface CurrentItem {
  id: string
  index: number
  type: string
  title: string
  artist: string
  url: string
  duration: number
  has_thumbnail: boolean
}

export interface QueueItem extends CurrentItem {
  is_current: boolean
}

export interface BotStatus {
  version: number
  empty: boolean
  play: boolean
  mode: string
  volume: number
  ducking: boolean
  playhead: number
  queue_length: number
  current_index: number
  server_time: number
  current: CurrentItem | null
}

export interface QueueResponse {
  items: QueueItem[]
  current_index: number
  version: number
}

// The app is mounted at /app/ (dev server included, via Vite `base`), so
// APIs live one level up. Relative paths keep reverse-proxy prefixes working.
const BASE = '..'

async function getJson<T>(path: string): Promise<T> {
  const rv = await fetch(`${BASE}${path}`, { headers: { Accept: 'application/json' } })
  if (!rv.ok) throw new Error(`${path} -> ${rv.status}`)
  return rv.json()
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const rv = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  if (!rv.ok) throw new Error(`${path} -> ${rv.status}`)
  return rv.json()
}

export const fetchStatus = () => getJson<BotStatus>('/api/status')
export const fetchQueue = () => getJson<QueueResponse>('/api/queue')

export type ControlAction =
  | { action: 'pause' | 'resume' | 'skip' | 'stop' | 'clear' }
  | { action: 'mode'; mode: string }
  | { action: 'volume'; volume: number }

export const postControls = (body: ControlAction) => postJson<BotStatus>('/api/controls', body)

export type QueueAction =
  | { action: 'remove' | 'top' | 'play'; index: number }
  | { action: 'move'; index: number; to: number }
  | { action: 'clear' }

export const postQueue = (body: QueueAction) => postJson<BotStatus>('/api/queue', body)

export function thumbnailUrl(id: string): string {
  return `${BASE}/api/thumbnail/${encodeURIComponent(id)}`
}

// ---- 身份 / 别名 --------------------------------------------------------

export interface Me {
  identity: string | null
  email: string | null
  alias: string
  display_name: string
  source: 'cloudflare' | 'cloudflare-jwt' | 'web-user' | 'anonymous'
  can_have_playlists: boolean
  jwt_verified?: boolean
}

export const fetchMe = () => getJson<Me>('/api/me')

/** 409 = 别名已被占用,400 = 含非法字符/过长 */
export async function saveAlias(alias: string): Promise<Me> {
  const rv = await fetch(`${BASE}/api/me`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ alias }),
  })
  if (rv.status === 409) throw new Error('taken')
  if (rv.status === 400) throw new Error('invalid')
  if (!rv.ok) throw new Error(String(rv.status))
  return rv.json()
}

// ---- 个人歌单 -----------------------------------------------------------

export interface PlaylistSummary {
  id: number
  name: string
  count: number
  total_duration: number
  updated_at: number
}

export interface PlaylistEntry {
  id: number
  item_id: string
  type: string
  title: string
  ref: string
  duration: number
}

export interface PlaylistDetail {
  id: number
  name: string
  items: PlaylistEntry[]
}

/** 往歌单里加什么:当前曲 / 队列第 i 首 / 整个队列 / 曲库条目 / 搜索结果链接 */
export type PlaylistSource =
  | { source: 'current' }
  | { source: 'all_queue' }
  | { source: 'queue'; index: number }
  | { source: 'library'; item_id: string }
  | { source: 'url'; url: string; title?: string; duration?: number; provider?: string; id?: string }

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const rv = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!rv.ok) throw new Error(`${path} -> ${rv.status}`)
  return rv.json()
}

export const fetchPlaylists = () =>
  getJson<{ playlists: PlaylistSummary[] }>('/api/playlists').then((r) => r.playlists)
export const fetchPlaylist = (id: number) => getJson<PlaylistDetail>(`/api/playlists/${id}`)
export const createPlaylist = (name: string) => send<PlaylistDetail>('POST', '/api/playlists', { name })
export const renamePlaylist = (id: number, name: string) =>
  send<PlaylistDetail>('POST', `/api/playlists/${id}/rename`, { name })
export const deletePlaylist = (id: number) => send<{ ok: boolean }>('DELETE', `/api/playlists/${id}`)
export const addToPlaylist = (id: number, src: PlaylistSource) =>
  send<{ added: number; playlist: PlaylistDetail }>('POST', `/api/playlists/${id}/items`, src)
export const removeFromPlaylist = (id: number, rowId: number) =>
  send<PlaylistDetail>('DELETE', `/api/playlists/${id}/items/${rowId}`)
export const movePlaylistItem = (id: number, index: number, to: number) =>
  send<PlaylistDetail>('POST', `/api/playlists/${id}/move`, { index, to })
export const playPlaylist = (
  id: number,
  body: { mode: 'append' | 'next' | 'replace'; shuffle?: boolean; item?: number },
) => send<BotStatus & { queued: number; skipped: number }>('POST', `/api/playlists/${id}/play`, body)
