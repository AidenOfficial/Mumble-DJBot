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

// The app is mounted at /app/ (dev server included, via Vite `base`), so
// APIs live one level up. Relative paths keep reverse-proxy prefixes working.
const BASE = '..'

export async function fetchStatus(): Promise<BotStatus> {
  const rv = await fetch(`${BASE}/api/status`, { headers: { Accept: 'application/json' } })
  if (!rv.ok) throw new Error(`status ${rv.status}`)
  return rv.json()
}

export function thumbnailUrl(id: string): string {
  return `${BASE}/api/thumbnail/${encodeURIComponent(id)}`
}
