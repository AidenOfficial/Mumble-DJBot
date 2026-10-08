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
  /** SponsorBlock 会跳过的片段 [开始, 结束](秒),只有当前曲有 */
  skip_segments?: [number, number][]
  /** 后台下载中(预下载 / 边下边播)才有 */
  download?: { progress: number; speed: number; eta: number | null; stage: string }
}

/** 当前曲还没出声时的准备进度 */
export interface PrepStatus {
  stage: 'pending' | 'fetching_info' | 'starting' | 'downloading' | 'launching' | 'ready' | 'failed'
  elapsed: number
  stage_elapsed: number
  eta: number | null
  eta_estimated: boolean
  speed: number
  downloaded: number
  total: number
  progress: number
  streaming: boolean
  buffered_secs: number
  target_secs: number
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
  prep: PrepStatus | null
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
  mumble: { mumble_name: string; linked_at: number } | null
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

// ---- 缓存管理 -----------------------------------------------------------

export interface CacheEntry {
  id: string
  title: string
  url: string
  duration: number
  size: number
  files: number
  complete: boolean
  last_played: number
  last_used: number
  plays: number
  pinned: boolean
  frequent: boolean
  in_queue: boolean
  downloading: boolean
}

export interface CacheSummary {
  folder: string
  persistent: boolean
  total_bytes: number
  count: number
  pinned_bytes: number
  limit_bytes: number | null
  disk_total: number
  disk_free: number
  auto_keep_plays: number
  keep_days: number
}

export const fetchCache = () => getJson<{ summary: CacheSummary; entries: CacheEntry[] }>('/api/cache')
export const pinCache = (id: string, pinned: boolean) =>
  send<{ id: string; pinned: boolean }>('POST', '/api/cache/pin', { id, pinned })
export async function deleteCache(id: string, force = false): Promise<number> {
  const rv = await fetch(`${BASE}/api/cache/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, force }),
  })
  if (rv.status === 409) throw new Error('busy')
  if (!rv.ok) throw new Error(String(rv.status))
  return (await rv.json()).freed
}
export const cleanupCache = (mode: 'limit' | 'expired' | 'unpinned') =>
  send<{ freed: number }>('POST', '/api/cache/cleanup', { mode })
export const saveCacheToLibrary = (id: string) => send<{ path: string }>('POST', '/api/cache/save', { id })

export function formatBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  const v = n / 1024 ** i
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

// ---- 频道 / 跟随 ---------------------------------------------------------

export interface ChannelUser {
  session: number
  name: string
  is_me: boolean
  is_bot: boolean
  muted: boolean
  deafened: boolean
}

export interface ChannelNode {
  id: number
  name: string
  path: string[]
  temporary: boolean
  users: ChannelUser[]
  children: ChannelNode[]
}

export type FollowMode = 'off' | 'auto' | 'user'

export interface ChannelOverview {
  tree: ChannelNode | null
  current_channel_id: number | null
  settings: {
    default_path: string[]
    default_channel_id: number | null
    follow: FollowMode
    follow_user: string
    return_home: boolean
    idle_pause_minutes: number
    idle_resume: boolean
  }
  online_users: string[]
}

export const fetchChannels = () => getJson<ChannelOverview>('/api/channels')
export const saveChannelSettings = (body: Partial<{
  default_channel_id: number; follow: FollowMode; follow_user: string; return_home: boolean
  idle_pause_minutes: number; idle_resume: boolean
}>) => send<ChannelOverview>('POST', '/api/channels/settings', body)
export const joinChannel = (channel_id: number) =>
  send<ChannelOverview>('POST', '/api/channels/join', { channel_id })

// ---- Mumble 绑定 ---------------------------------------------------------

export const createBindCode = () =>
  send<{ code: string; expires_at: number; command: string }>('POST', '/api/me/bind')
export const unbindMumble = () => send<Me>('DELETE', '/api/me/bind')

// ---- 导入歌单 -----------------------------------------------------------

export interface ImportJob {
  id: string
  status: 'listing' | 'matching' | 'done' | 'error'
  source: 'youtube' | 'netease' | 'qqmusic' | 'spotify'
  total: number
  processed: number
  matched: number
  added?: number
  playlist_id?: number
  source_title?: string
  unmatched?: string[]
  error?: string
  note?: 'spotify_truncated'
}

export async function startImport(url: string, playlistId?: number): Promise<string> {
  const rv = await fetch(`${BASE}/api/playlists/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(playlistId ? { url, playlist_id: playlistId } : { url }),
  })
  const body = await rv.json().catch(() => ({}))
  if (!rv.ok) throw new Error(body.error ?? String(rv.status))
  return body.job_id
}
export const fetchImportJob = (id: string) => getJson<ImportJob>(`/api/playlists/import/${id}`)
