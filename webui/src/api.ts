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
