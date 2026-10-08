// 轻量 i18n:不引第三方库。locale 是全局 ref,模板/computed 里调用 t() 时会自动
// 追踪它,切语言即时重渲染。en 是基准语言包,其他语言缺 key 时 TS 直接报错。
import { ref, watch } from 'vue'
import { en, type Key, type Msg } from './en'
import { zhCN } from './zh-CN'
import { ja } from './ja'

export type Locale = 'en' | 'zh-CN' | 'ja'

export const LOCALES: { code: Locale; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'zh-CN', label: '简体中文' },
  { code: 'ja', label: '日本語' },
]

const MESSAGES: Record<Locale, Record<Key, Msg>> = { en, 'zh-CN': zhCN, ja }
const STORAGE_KEY = 'lang'

function detect(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && saved in MESSAGES) return saved as Locale
  } catch { /* 隐私模式等读不到 localStorage */ }
  for (const tag of navigator.languages ?? [navigator.language]) {
    const l = tag.toLowerCase()
    if (l.startsWith('zh')) return 'zh-CN'
    if (l.startsWith('ja')) return 'ja'
    if (l.startsWith('en')) return 'en'
  }
  return 'en'
}

export const locale = ref<Locale>(detect())

watch(locale, (l) => {
  document.documentElement.lang = l
  try { localStorage.setItem(STORAGE_KEY, l) } catch { /* ignore */ }
}, { immediate: true })

type Params = Record<string, string | number>

const pluralRules = new Map<Locale, Intl.PluralRules>()
function pick(msg: Msg, n: unknown): string {
  if (typeof msg === 'string') return msg
  const l = locale.value
  let rules = pluralRules.get(l)
  if (!rules) pluralRules.set(l, (rules = new Intl.PluralRules(l)))
  const cat = rules.select(Number(n) || 0)
  return cat === 'one' ? msg.one : msg.other
}

function raw(key: Key, params?: Params): string {
  const msg = MESSAGES[locale.value][key] ?? en[key]
  return pick(msg, params?.n)
}

/** 取文案;`{name}` 换成 params.name,复数形式按 params.n 选择 */
export function t(key: Key, params?: Params): string {
  const s = raw(key, params)
  return params ? s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m)) : s
}

/** 同 t(),但把插值和普通文字拆开返回,方便模板里把参数单独加粗(避免 v-html) */
export function tParts(key: Key, params: Params): { text: string; param: boolean }[] {
  return raw(key, params)
    .split(/(\{\w+\})/)
    .filter(Boolean)
    .map((seg) => {
      const m = /^\{(\w+)\}$/.exec(seg)
      return m && m[1]! in params ? { text: String(params[m[1]!]), param: true } : { text: seg, param: false }
    })
}

/** "5 分钟前" 这类相对时间,交给 Intl 按当前语言格式化 */
export function timeAgo(ts: number): string {
  if (!ts) return t('time.never')
  const s = Date.now() / 1000 - ts
  const rtf = new Intl.RelativeTimeFormat(locale.value, { numeric: 'always', style: 'short' })
  if (s < 3600) return rtf.format(-Math.max(1, Math.round(s / 60)), 'minute')
  if (s < 86400) return rtf.format(-Math.round(s / 3600), 'hour')
  if (s < 86400 * 60) return rtf.format(-Math.round(s / 86400), 'day')
  return rtf.format(-Math.round(s / 86400 / 30), 'month')
}

/** 播放模式 / 来源类型这些后端枚举值的显示名 */
const MODE_KEYS: Record<string, Key> = {
  'one-shot': 'mode.oneShot', repeat: 'mode.repeat', single: 'mode.single', random: 'mode.random', autoplay: 'mode.autoplay',
}
export const modeLabel = (m: string) => (MODE_KEYS[m] ? t(MODE_KEYS[m]!) : m)

const TYPE_KEYS: Record<string, Key> = {
  url: 'type.url', file: 'type.file', radio: 'type.radio', playlist: 'type.playlist', livestream: 'type.livestream',
}
export const typeLabel = (type: string) => (TYPE_KEYS[type] ? t(TYPE_KEYS[type]!) : type || t('type.other'))
