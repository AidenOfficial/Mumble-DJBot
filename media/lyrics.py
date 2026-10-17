"""歌词(默认关闭,[bot] lyrics_lookup = True 打开)。

来源(默认依次,[bot] lyrics_sources 可以改顺序):
  1. LRCLIB(lrclib.net):免费、不用 key,社区上传的 LRC。按歌名 + 歌手搜,再按时长挑。
     同一时间戳的第二行是上传者加的翻译(语言不定,常见越南语、英语),丢掉。
  2. 网易云:中日文歌收录全,带中文翻译(tlyric),按时间戳合并成每行的第二行。

查的是 media/artwork.py 认出来的正式歌名 / 歌手(URL 条目的 track / artist,本地文件的标签)。
artwork 还没查完就等着;认不出是哪首就不查——宁可没有,也不贴别的歌的歌词。

时间轴:LRC 是按录音室版写的,MV 常有片头。条目时长和歌词那一版的时长相差不到
ALIGN_TOLERANCE 秒时先当作对得上;音频下载好后再用 Shazam 截一段校准,算出 MV 比原曲
晚几秒(offset)。都对不上就标 aligned=False,网页只列出歌词、不跟着滚动
(有人手动调了偏移后才滚动)。

每首一个 JSON,存在 lyrics_folder(默认 settings 数据库旁的 lyrics/)。
"""

import html
import itertools
import json
import logging
import os
import queue
import re
import threading
import time

import variables as var
from media import artwork

log = logging.getLogger("bot")

FORMAT_VERSION = 1
ALIGN_TOLERANCE = 3.0     # 条目时长和歌词版本时长差多少秒以内,直接当作同一版
MAX_DURATION_DIFF = 90    # 歌词版本比条目长 / 短太多:多半是别的版本,不要
RETRY_NONE_DAYS = 7       # 没找到的过几天再查一次(LRCLIB 每天都有人上传)
MEMORY_ITEMS = 64         # 内存里缓存几首(状态轮询每 3 秒问一次)
MAX_FILES = 5000          # 目录里最多留几首,超过按修改时间删最旧的

LRCLIB_URL = 'https://lrclib.net/api/search'
NETEASE_LYRIC_URL = 'https://music.163.com/api/song/lyric'
# LRCLIB 要求 User-Agent 写明是哪个程序
LRCLIB_HEADERS = {'User-Agent': 'Mumble-DJBot (https://github.com/AidenOfficial/Mumble-DJBot)'}

_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def enabled():
    return var.config.getboolean('bot', 'lyrics_lookup', fallback=False) if var.config else False


# ---- 存储 -----------------------------------------------------------------------

def folder():
    conf = var.config.get('bot', 'lyrics_folder', fallback='') if var.config else ''
    if conf:
        return conf
    base = os.path.dirname(os.path.abspath(var.settings_db_path)) if var.settings_db_path else \
        (var.tmp_folder or '.')
    return os.path.join(base, 'lyrics')


def path_for(item_id):
    if not item_id or not _ID_RE.match(item_id):
        return None
    return os.path.join(folder(), item_id + '.json')


_memory = {}
_memory_lock = threading.Lock()


def load(item_id):
    with _memory_lock:
        if item_id in _memory:
            return _memory[item_id]
    path = path_for(item_id)
    data = None
    if path and os.path.isfile(path):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            log.debug("lyrics: unreadable %s", path, exc_info=True)
    if data is not None and data.get('v') != FORMAT_VERSION:
        data = None
    _remember(item_id, data)
    return data


def save(item_id, data):
    path = path_for(item_id)
    if not path:
        return
    data = dict(data, v=FORMAT_VERSION)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    _remember(item_id, data)
    _prune()


def _remember(item_id, data):
    with _memory_lock:
        _memory.pop(item_id, None)
        _memory[item_id] = data
        while len(_memory) > MEMORY_ITEMS:
            _memory.pop(next(iter(_memory)))


def _prune():
    try:
        names = [n for n in os.listdir(folder()) if n.endswith('.json')]
    except OSError:
        return
    if len(names) <= MAX_FILES:
        return
    paths = sorted((os.path.join(folder(), n) for n in names), key=os.path.getmtime)
    for p in paths[:len(paths) - MAX_FILES]:
        try:
            os.remove(p)
        except OSError:
            pass


# ---- LRC 解析 --------------------------------------------------------------------

_TIME = re.compile(r'\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]')
_OFFSET_TAG = re.compile(r'^\[offset:\s*([+-]?\d+)\]', re.I | re.M)

# 作词 / 作曲 / 编曲… 的署名行(不是歌词)。要求名称后面紧跟冒号,正常歌词不会这样开头
_CREDIT = re.compile(
    r'^\s*(?:作词|作詞|作曲|编曲|編曲|词|詞|曲|制作人|製作人|制作|製作|监制|監製|演唱|原唱|歌手|和声|和聲|'
    r'混音|母带|母帶|录音|錄音|吉他|贝斯|貝斯|鼓|键盘|鍵盤|弦乐|弦樂|出品|发行|發行|统筹|統籌|策划|企划|'
    r'作詞者|作曲者|編曲者|lyrics?(?:\s+by)?|lyricist|written(?:\s+by)?|compos(?:ed|er)(?:\s+by)?|'
    r'arrange(?:d|r)(?:\s+by)?|produce(?:d|r)(?:\s+by)?|mix(?:ed|ing)?(?:\s+by)?|master(?:ed|ing)?(?:\s+by)?|'
    r'vocals?|op|sp)\s*[:：]', re.I)


def parse_lrc(text):
    """LRC 文本 → [(秒, 文字)],按时间排好。一行可以有多个时间戳;
    没有时间戳的行(网易云开头的 JSON 署名、[ar:] 这类标签)跳过。"""
    lines = []
    offset = 0.0
    m = _OFFSET_TAG.search(text or '')
    if m:
        offset = int(m.group(1)) / 1000  # 正数表示歌词提前出现
    for raw in (text or '').splitlines():
        stamps = []
        pos = 0
        while True:
            m = _TIME.match(raw, pos)
            if not m:
                break
            frac = m.group(3) or '0'
            stamps.append(int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / (10 ** len(frac)))
            pos = m.end()
        if not stamps:
            continue
        words = raw[pos:].strip()
        for t in stamps:
            lines.append((round(max(0.0, t - offset), 3), words))
    lines.sort(key=lambda x: x[0])  # sort 是稳定的:同一时间戳保持原来的先后
    return lines


def clean_lines(lines):
    """去掉署名行、同一时间戳的重复行(只留第一行),把连续的空行并成一行,去掉首尾空行。
    返回 [[秒, 文字, 翻译]]。"""
    out = []
    last_t = None
    for t, text in lines:
        if t == last_t:
            continue
        last_t = t
        if _CREDIT.match(text):
            continue
        if not text and (not out or not out[-1][1]):
            continue
        out.append([t, text, ''])
    while out and not out[-1][1]:
        out.pop()
    return out


def clean_plain(text):
    lines = [ln.strip() for ln in (text or '').replace('\r', '').split('\n')]
    lines = [ln for ln in lines if not _CREDIT.match(ln)]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def merge_translation(lines, translated):
    """translated 也是 parse_lrc 的结果:按时间戳(允许 0.3 秒误差)对上就填进第三列。"""
    if not translated:
        return lines
    trans = [(t, s) for t, s in translated if s and not _CREDIT.match(s)]
    j = 0
    for line in lines:
        while j < len(trans) and trans[j][0] < line[0] - 0.3:
            j += 1
        if j < len(trans) and abs(trans[j][0] - line[0]) <= 0.3 and line[1]:
            if trans[j][1] != line[1]:
                line[2] = trans[j][1]
            j += 1
    return lines


def plain_from_lines(lines):
    return '\n'.join(s for _, s, _ in lines).strip()


_INSTRUMENTAL = re.compile(r'^\s*(?:纯音乐|純音樂)[,，、]?\s*请(?:欣赏|您欣赏)|^\s*此歌曲为没有填词的纯音乐')


def _instrumental_text(lines):
    return len([s for _, s, _ in lines if s]) <= 2 and any(_INSTRUMENTAL.search(s) for _, s, _ in lines)


# ---- 来源 ------------------------------------------------------------------------

def search_lrclib(track, artist):
    data = artwork._get_json('lrclib', LRCLIB_URL, 0.5, headers=LRCLIB_HEADERS,
                             params={'track_name': track, 'artist_name': artist})
    results = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        results.append({
            'title': r.get('trackName') or r.get('name') or '',
            'artists': [r.get('artistName') or ''],
            'album': r.get('albumName') or '',
            'duration': r.get('duration') or 0,
            'synced': r.get('syncedLyrics') or '',
            'plain': r.get('plainLyrics') or '',
            'instrumental': bool(r.get('instrumental')),
            'source': 'lrclib',
        })
    return results


def search_netease(track, artist):
    return artwork.search_netease(f"{track} {artist}".strip())


def netease_lyric(song_id):
    """网易云的歌词:{synced, translated, instrumental};请求失败 None。"""
    data = artwork._get_json('netease', NETEASE_LYRIC_URL, 0.5, headers=artwork._NETEASE_HEADERS,
                             params={'id': song_id, 'lv': 1, 'tv': -1})
    if not isinstance(data, dict):
        return None
    return {
        'synced': ((data.get('lrc') or {}).get('lyric') or ''),
        'translated': ((data.get('tlyric') or {}).get('lyric') or ''),
        'instrumental': bool(data.get('nolyric') or data.get('pureMusic')),
    }


def _artist_ok(result_artists, artist):
    if not artist:
        return False
    return artwork.artist_in(result_artists, artwork.words(artist)) or \
        artwork.artist_in([artist], artwork.words(' '.join(result_artists)))


def choose(results, track, artist, duration=0):
    """歌名一致、歌手对得上、不是 live / 伴奏 之类的另一版本;再挑有时间轴、时长最接近的。"""
    want, want_full = artwork.key(track), artwork.full_key(track)
    good = []
    for r in results or []:
        if artwork.key(r.get('title')) != want and artwork.full_key(r.get('title')) != want_full:
            continue
        if not _artist_ok(r.get('artists') or [], artist):
            continue
        if artwork.is_variant(r, track):
            continue
        d = r.get('duration') or 0
        if duration and d and abs(duration - d) > MAX_DURATION_DIFF:
            continue
        good.append(r)
    if not good:
        return None

    def rank(r):
        d = r.get('duration') or 0
        diff = abs(duration - d) if duration and d else MAX_DURATION_DIFF
        has_lyrics = bool(r.get('synced') or r.get('plain') or r.get('netease_id'))
        return (not has_lyrics, not r.get('synced') and r.get('source') == 'lrclib', diff)
    return min(good, key=rank)


def _from_lrclib(r):
    if r.get('instrumental'):
        return {'status': 'instrumental', 'lines': [], 'plain': ''}
    lines = clean_lines(parse_lrc(r.get('synced')))
    plain = plain_from_lines(lines) if lines else clean_plain(r.get('plain'))
    if not plain:
        return None
    return {'status': 'found', 'lines': lines, 'plain': plain}


def _from_netease(r):
    lyric = netease_lyric(r['netease_id']) if r.get('netease_id') else None
    if not lyric:
        return None
    if lyric['instrumental']:
        return {'status': 'instrumental', 'lines': [], 'plain': ''}
    lines = clean_lines(parse_lrc(lyric['synced']))
    if _instrumental_text(lines):
        return {'status': 'instrumental', 'lines': [], 'plain': ''}
    if not lines:
        # 少数歌只有不带时间戳的歌词
        plain = clean_plain(lyric['synced'])
        return {'status': 'found', 'lines': [], 'plain': plain} if plain else None
    merge_translation(lines, parse_lrc(lyric['translated']))
    return {'status': 'found', 'lines': lines, 'plain': plain_from_lines(lines)}


def _drop_title_lines(data, title, artist):
    """很多 LRC 第一行是「歌手 - 歌名」:不是歌词,去掉(只看开头几行)。"""
    want_title, want_artist = artwork.key(title), artwork.key(artist)
    if not want_title:
        return

    def is_title(text):
        k = re.sub(r'[\W_]+', '', artwork._base(text))
        return k == want_title or (want_title in k and any(
            artwork.key(a) and artwork.key(a) in k for a in artwork._artist_names([artist])) and
            len(k) <= len(want_title) + len(want_artist) + 2)
    lines = data.get('lines') or []
    while lines[:1] and (not lines[0][1] or is_title(lines[0][1])) and len(lines) > 1:
        lines.pop(0)
    if lines:
        data['plain'] = plain_from_lines(lines)
    else:
        plain = (data.get('plain') or '').split('\n')
        while plain[:1] and (not plain[0].strip() or is_title(plain[0])) and len(plain) > 1:
            plain.pop(0)
        data['plain'] = '\n'.join(plain)


SOURCES = {'lrclib': (search_lrclib, _from_lrclib), 'netease': (search_netease, _from_netease)}
DEFAULT_SOURCES = 'lrclib,netease'


def configured_sources():
    """[bot] lyrics_sources:按顺序问哪些来源(逗号分隔),写错的名字忽略。"""
    conf = var.config.get('bot', 'lyrics_sources', fallback=DEFAULT_SOURCES) if var.config else DEFAULT_SOURCES
    names = [n.strip().lower() for n in (conf or DEFAULT_SOURCES).split(',')]
    return [SOURCES[n] for n in dict.fromkeys(names) if n in SOURCES]


def find(track, artist, duration=0, sources=None):
    """按顺序问各个来源,返回 {status, lines, plain, source, title, artist, duration} 或 None。
    纯音乐也算找到(status='instrumental'),不再往下问。"""
    for search, convert in configured_sources() if sources is None else sources:
        try:
            match = choose(search(track, artist), track, artist, duration)
            data = convert(match) if match else None
        except Exception:
            log.debug("lyrics: source %s failed", getattr(search, '__name__', search), exc_info=True)
            continue
        if data:
            _drop_title_lines(data, match.get('title') or track, ' '.join(match.get('artists') or [artist]))
            return dict(data, source=match.get('source') or '', title=match.get('title') or track,
                        artist=', '.join(a for a in match.get('artists') or [] if a) or artist,
                        duration=match.get('duration') or 0)
    return None


# ---- 条目 → 查询 ------------------------------------------------------------------

def identity(item):
    """(歌名, 歌手);还在等 artwork 认歌是 'waiting';认不出 / 不支持是 None。"""
    kind = getattr(item, 'type', '')
    if kind in artwork.URL_TYPES:
        track = getattr(item, 'track', None)
        artist = getattr(item, 'artist', '') or ''
        if track and artist:
            return track, artist
        if not artwork.lookup_enabled():
            return None
        if track is None or not getattr(item, 'artwork_done', False):
            return 'waiting'
        if getattr(item, 'shazam', '') == 'pending' and artwork.shazam_enabled():
            return 'waiting'  # 等音频下载好听歌识曲
        return None
    if kind == 'file':
        title, artist = getattr(item, 'title', '') or '', getattr(item, 'artist', '') or ''
        return (title, artist) if title and artist and artist != '??' else None
    return None


def lookup_key(track, artist):
    return f"{artwork.key(track)}|{artwork.key(artist)}"


def _fresh(data, ident):
    """data 是不是这首歌现在的身份查出来的、还用不用重查。"""
    if not data or data.get('key') != lookup_key(*ident):
        return False
    if data.get('status') == 'none':
        return time.time() - (data.get('fetched') or 0) < RETRY_NONE_DAYS * 86400
    return True


def _needs_align(item, data):
    return bool(data and data.get('status') == 'found' and data.get('lines') and not data.get('manual')
                and data.get('align') == 'pending' and _audio_ready(item))


def _audio_ready(item):
    return artwork._audio_ready(item)


def get(item):
    """已经查好的歌词(可能是 status='none');没查过 / 身份变了是 None。"""
    if item is None:
        return None
    ident = identity(item)
    if not isinstance(ident, tuple):
        return None
    data = load(item.id)
    return data if _fresh(data, ident) else None


def status(item):
    """给网页的状态:off / '' (这种条目没有歌词) / waiting / pending / found / none / instrumental。"""
    if not enabled():
        return 'off'
    if item is None:
        return ''
    ident = identity(item)
    if ident is None:
        return 'none' if getattr(item, 'type', '') in artwork.URL_TYPES + ('file',) else ''
    if ident == 'waiting':
        return 'waiting'
    data = load(item.id)
    if _fresh(data, ident):
        return data['status']
    return 'pending'


# ---- 后台任务 --------------------------------------------------------------------

_queue = queue.PriorityQueue()
_seq = itertools.count()
_queued = set()
_urgent = set()
_events = {}
_lock = threading.Lock()
_worker = None


def ensure(item, urgent=False):
    """需要时把条目排进后台去查,可以随便多次调用(已经有了就只是查一下内存)。"""
    if item is None or not enabled():
        return
    ident = identity(item)
    if not isinstance(ident, tuple):
        return
    data = load(item.id)
    if _fresh(data, ident) and not _needs_align(item, data):
        return
    with _lock:
        if item.id in _queued and (not urgent or item.id in _urgent):
            return
        _queued.add(item.id)
        if urgent:
            _urgent.add(item.id)
        _events.setdefault(item.id, threading.Event())
        _queue.put((0 if urgent else 1, next(_seq), item))


def wait(item, timeout):
    """ensure 之后等结果(聊天命令用),超时返回 None。"""
    ensure(item, urgent=True)
    with _lock:
        event = _events.get(item.id)
    if event is not None:
        event.wait(timeout)
    return get(item)


def start():
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name='Lyrics', daemon=True)
            _worker.start()


def _run():
    while True:
        _, _, item = _queue.get()
        with _lock:
            if item.id not in _queued:
                continue
            _queued.discard(item.id)
            _urgent.discard(item.id)
        try:
            resolve(item)
        except Exception:
            log.warning("lyrics: failed for %s", getattr(item, 'id', '?'), exc_info=True)
        finally:
            with _lock:
                event = _events.pop(item.id, None)
            if event is not None:
                event.set()


def resolve(item, sources=None):
    """查歌词并存盘(同步执行;后台线程和单测调用)。"""
    ident = identity(item)
    if not isinstance(ident, tuple):
        return None
    track, artist = ident
    duration = getattr(item, 'duration', 0) or 0
    old = load(item.id)
    if _fresh(old, ident):
        if not _needs_align(item, old):
            return old
        data = dict(old)
    else:
        found = find(track, artist, duration, sources)
        data = found or {'status': 'none', 'lines': [], 'plain': ''}
        data.update(key=lookup_key(track, artist), fetched=time.time(), offset=0.0,
                    aligned=False, align='', manual=False)
        if data['status'] == 'found' and data['lines']:
            # LRCLIB 的时长是上传者填的,同一份时间轴常挂着好几个不同的时长(0、40、281…),
            # 所以时长对得上只算"先这样滚动",能听歌识曲时再校准一次
            lyric_duration = data.get('duration') or 0
            data['aligned'] = bool(duration and lyric_duration and abs(duration - lyric_duration) <= ALIGN_TOLERANCE)
            data['align'] = 'pending' if artwork.shazam_enabled() else ''
        log.info("lyrics: %s for %s%s", data['status'], item.format_title(),
                 f" ({data['source']}: {data['artist']} - {data['title']})" if found else '')
    if _needs_align(item, data):
        _align(item, data)
    save(item.id, data)
    return data


# Shazam 返回的 offset 不是片段起点在原曲里的秒数:按我们的截取方式(16kHz 单声道 12 秒)实测
# offset ≈ (1 + timeskew) × 原曲位置 - 0.8,timeskew 约 0.05(原速的正式音源也是这样)
SHAZAM_LAG = 0.8
MAX_INTRO = 180  # MV 片头最长按 3 分钟算,再长多半是认错了


def _align(item, data):
    """用 Shazam 认一段,算出条目时间比歌词时间晚多少秒(MV 片头)。认不出 / 认成别的歌时
    保留按时长判断的结果。"""
    from media import recognize, sponsorblock
    data['align'] = 'failed'
    if not artwork.shazam_enabled() or not recognize.shazam_available():
        return
    duration = getattr(item, 'duration', 0) or 0
    url = getattr(item, 'url', '') or ''
    segments = sponsorblock.fetch_segments(url, sponsorblock.configured_categories()) if url else []
    for start in recognize.clip_offsets(duration, segments):
        try:
            match = recognize._recognize_clip(item.path, start)
        except recognize.ShazamUnavailable:
            data['align'] = 'pending'  # 没连上:下次再试
            return
        if not match or match.get('offset') is None:
            continue
        if artwork.key(match['title']) != artwork.key(data.get('title') or ''):
            continue
        song_pos = (match['offset'] + SHAZAM_LAG) / (1 + (match.get('timeskew') or 0.0))
        offset = round(start - song_pos, 1)
        if abs(offset) < 0.3:
            offset = 0.0
        if -15 <= offset <= min(MAX_INTRO, duration / 2 if duration else MAX_INTRO):
            data.update(offset=offset, aligned=True, align='done')
            log.info("lyrics: aligned %s, offset %.1fs", item.format_title(), offset)
            return


def set_offset(item, offset):
    """网页上手动调偏移:之后就当作对齐了。"""
    data = get(item)
    if not data or data.get('status') != 'found':
        return None
    data = dict(data, offset=round(max(-600.0, min(600.0, float(offset))), 2), aligned=True, manual=True,
                align='done')
    save(item.id, data)
    return data


# ---- 输出 ------------------------------------------------------------------------

def to_json(data):
    """给网页的格式。"""
    return {
        'status': data.get('status'),
        'source': data.get('source') or '',
        'title': data.get('title') or '',
        'artist': data.get('artist') or '',
        'lines': data.get('lines') or [],
        'plain': data.get('plain') or '',
        'offset': data.get('offset') or 0.0,
        'aligned': bool(data.get('aligned')),
        'manual': bool(data.get('manual')),
    }


def chat_lines(data):
    """聊天消息用的 HTML 行(带翻译的放在原文下面,用斜体)。"""
    out = []
    if data.get('lines'):
        for _, text, trans in data['lines']:
            out.append(html.escape(text) if text else '&nbsp;')
            if trans:
                out.append(f"<i>{html.escape(trans)}</i>")
    else:
        out = [html.escape(ln) if ln else '&nbsp;' for ln in (data.get('plain') or '').split('\n')]
    return out
